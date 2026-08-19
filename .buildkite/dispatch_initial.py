#!/usr/bin/env python3
"""Lease and dispatch every initial attempt from one durable coordinator job."""

import json
import os
from pathlib import Path
import subprocess
import threading
import time

import bazel
from test_scheduler_client import configure_auth, metadata, request


TARGET_COUNT = 100
DEFAULT_INITIAL_ATTEMPTS = 1
QUALIFICATION_INITIAL_ATTEMPTS = 10
# The final ten labels use the qualification policy and ten Bazel runs.
QUALIFICATION_START = 90
QUALIFICATION_TARGET_COUNT = TARGET_COUNT - QUALIFICATION_START
# The completion API accepts at most 5,000 attempts in one request.
COMPLETION_MAX_ATTEMPTS = 5_000
EXPECTED_ATTEMPTS = (
    QUALIFICATION_START * DEFAULT_INITIAL_ATTEMPTS
    + QUALIFICATION_TARGET_COUNT * QUALIFICATION_INITIAL_ATTEMPTS
)


def target_index(label: str) -> int:
    """Return the numeric suffix from a label such as //demo:target_42."""
    return int(label.rsplit("_", 1)[1])


def initial_attempts_for(label: str) -> int:
    """Return the initial allocation for the policy selected by a label."""
    if target_index(label) >= QUALIFICATION_START:
        return QUALIFICATION_INITIAL_ATTEMPTS
    return DEFAULT_INITIAL_ATTEMPTS


def completion_batches(completions: list[dict]) -> list[list[dict]]:
    """Group whole leases without exceeding the completion API limit."""
    batches: list[list[dict]] = []
    batch: list[dict] = []
    attempt_count = 0
    for completion in completions:
        lease_attempt_count = len(completion["attempts"])
        if batch and attempt_count + lease_attempt_count > COMPLETION_MAX_ATTEMPTS:
            batches.append(batch)
            batch = []
            attempt_count = 0
        batch.append(completion)
        attempt_count += lease_attempt_count
    if batch:
        batches.append(batch)
    return batches


def read_bep(path: Path) -> dict[tuple[str, int], str]:
    """Map each Bazel label and zero-based run index to its result."""
    statuses: dict[tuple[str, int], str] = {}
    with path.open() as events:
        for line in events:
            event = json.loads(line)
            test_result = event.get("id", {}).get("testResult")
            if test_result:
                # Bazel run numbers start at one. Scheduler attempt indexes
                # start at zero.
                statuses[(test_result["label"], test_result["run"] - 1)] = event[
                    "testResult"
                ]["status"]
    return statuses


def heartbeat(
    pool_id: str,
    lease_ids: list[str],
    stop: threading.Event,
    errors: list[Exception],
) -> None:
    """Extend all initial leases until Bazel finishes or a request fails."""
    try:
        while not stop.wait(60):
            request(
                "POST",
                f"/pools/{pool_id}/leases/heartbeat",
                {"lease_ids": lease_ids, "lease_ttl_seconds": 600},
            )
            print(f"Heartbeated {len(lease_ids)} initial leases")
    except Exception as error:
        errors.append(error)
        stop.set()


def main() -> None:
    configure_auth()
    pool_id = metadata("get")
    leases = []
    leased_attempts = 0

    # Acquire all initial work before Bazel starts. This barrier lets one Bazel
    # invocation submit the complete initial target set to remote execution.
    while leased_attempts < EXPECTED_ATTEMPTS:
        response = request(
            "POST", f"/pools/{pool_id}/leases", {"lease_ttl_seconds": 600}
        )
        lease = response.get("lease")
        if lease is None:
            if response["pool"]["state"] == "consumed" and not leased_attempts:
                print("Initial attempts were already completed by the previous job attempt")
                return
            print(
                "Waiting for expired initial leases to return "
                f"({leased_attempts}/{EXPECTED_ATTEMPTS})"
            )
            time.sleep(5)
            continue
        if any(
            attempt["attempt_index"] >= initial_attempts_for(attempt["selector"])
            for attempt in lease["attempts"]
        ):
            # A retried coordinator can arrive after the first job completed
            # the initial work. Do not consume policy-generated retries here.
            request(
                "POST",
                f"/pools/{pool_id}/leases/release",
                {
                    "leases": [
                        {
                            "id": lease["id"],
                            "reason": "initial attempts already complete",
                        }
                    ]
                },
            )
            if leased_attempts:
                raise RuntimeError("Received a retry after partially leasing initial attempts")
            print("Initial attempts were already completed by the previous job attempt")
            return
        leases.append(lease)
        leased_attempts += len(lease["attempts"])
        print(
            f"Acquired lease {lease['id']}: {len(lease['attempts'])} attempts "
            f"({leased_attempts}/{EXPECTED_ATTEMPTS})"
        )

    attempts = [attempt for lease in leases for attempt in lease["attempts"]]
    if leased_attempts != EXPECTED_ATTEMPTS:
        raise RuntimeError(f"Expected {EXPECTED_ATTEMPTS} attempts, got {leased_attempts}")
    labels = sorted({attempt["selector"] for attempt in attempts})
    if len(labels) != TARGET_COUNT:
        raise RuntimeError(f"Expected {TARGET_COUNT} distinct targets, got {len(labels)}")

    stop = threading.Event()
    heartbeat_errors: list[Exception] = []
    heartbeat_thread = threading.Thread(
        target=heartbeat,
        args=(
            pool_id,
            [lease["id"] for lease in leases],
            stop,
            heartbeat_errors,
        ),
        daemon=True,
    )
    heartbeat_thread.start()
    bep = Path("bep-initial.json")
    try:
        # A remote executor controls the real concurrency. This high Bazel
        # limit only permits every initial action to be submitted at once.
        jobs = EXPECTED_ATTEMPTS if os.environ.get("BUILDBUDDY_API_KEY") else 20
        print(
            f"Sending all {TARGET_COUNT} targets to one Bazel invocation with "
            f"{DEFAULT_INITIAL_ATTEMPTS} default runs and "
            f"{QUALIFICATION_INITIAL_ATTEMPTS} qualification runs per target, "
            f"using --jobs={jobs}"
        )
        result = subprocess.run(
            bazel.command()
            + [
                f"--jobs={jobs}",
                # Bazel's default auto mode ignores cached test results when
                # runs_per_test is greater than one. Force cache use here.
                "--cache_test_results=yes",
                f"--runs_per_test={DEFAULT_INITIAL_ATTEMPTS}",
                "--runs_per_test=//demo:target_9[0-9]@10",
                "--test_output=errors",
                f"--build_event_json_file={bep}",
                *labels,
            ],
            check=False,
        )
    finally:
        stop.set()
        heartbeat_thread.join()
    if heartbeat_errors:
        raise RuntimeError("Failed to heartbeat initial leases") from heartbeat_errors[0]

    statuses = read_bep(bep)
    # Do not complete a lease unless Bazel returned every expected run. A
    # missing result would otherwise become an incorrect failed result.
    missing = [
        (attempt["selector"], attempt["attempt_index"])
        for attempt in attempts
        if (attempt["selector"], attempt["attempt_index"]) not in statuses
    ]
    if missing:
        raise RuntimeError(f"Bazel produced no result for {len(missing)} initial attempts")

    completions = [
        {
            "lease_id": lease["id"],
            "attempts": [
                {
                    "attempt_id": attempt["id"],
                    "result": (
                        "passed"
                        if statuses[(attempt["selector"], attempt["attempt_index"])]
                        == "PASSED"
                        else "failed"
                    ),
                }
                for attempt in lease["attempts"]
            ],
        }
        for lease in leases
    ]
    # The current 190-attempt workload fits in one request. Keep batching so a
    # larger workload still respects the 5,000-attempt API limit.
    for batch_number, batch in enumerate(completion_batches(completions), start=1):
        batch_attempt_count = sum(len(completion["attempts"]) for completion in batch)
        request(
            "POST", f"/pools/{pool_id}/leases/complete", {"leases": batch}
        )
        print(
            f"Completed initial result batch {batch_number}: "
            f"{batch_attempt_count} attempts"
        )
    passed = sum(status == "PASSED" for status in statuses.values())
    print(
        f"Bazel exit={result.returncode}; completed {EXPECTED_ATTEMPTS} "
        f"attempts: passed={passed}, failed={EXPECTED_ATTEMPTS - passed}"
    )


if __name__ == "__main__":
    main()
