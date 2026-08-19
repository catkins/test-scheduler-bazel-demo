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


TARGET_COUNT = 1_000
DEFAULT_INITIAL_ATTEMPTS = 5
QUALIFICATION_INITIAL_ATTEMPTS = 10
QUALIFICATION_START = 900
QUALIFICATION_TARGET_COUNT = TARGET_COUNT - QUALIFICATION_START
EXPECTED_ATTEMPTS = (
    QUALIFICATION_START * DEFAULT_INITIAL_ATTEMPTS
    + QUALIFICATION_TARGET_COUNT * QUALIFICATION_INITIAL_ATTEMPTS
)


def target_index(label: str) -> int:
    return int(label.rsplit("_", 1)[1])


def initial_attempts_for(label: str) -> int:
    if target_index(label) >= QUALIFICATION_START:
        return QUALIFICATION_INITIAL_ATTEMPTS
    return DEFAULT_INITIAL_ATTEMPTS


def read_bep(path: Path) -> dict[tuple[str, int], str]:
    statuses: dict[tuple[str, int], str] = {}
    with path.open() as events:
        for line in events:
            event = json.loads(line)
            test_result = event.get("id", {}).get("testResult")
            if test_result:
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
                f"--runs_per_test={DEFAULT_INITIAL_ATTEMPTS}",
                "--runs_per_test=//demo:target_9[0-9][0-9]@10",
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
    request(
        "POST", f"/pools/{pool_id}/leases/complete", {"leases": completions}
    )
    passed = sum(status == "PASSED" for status in statuses.values())
    print(
        f"Bazel exit={result.returncode}; atomically completed {EXPECTED_ATTEMPTS} "
        f"attempts: passed={passed}, failed={EXPECTED_ATTEMPTS - passed}"
    )


if __name__ == "__main__":
    main()
