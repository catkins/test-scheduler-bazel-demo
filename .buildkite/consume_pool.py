#!/usr/bin/env python3
"""Lease Test Scheduler attempts and execute their Bazel targets."""

import json
import os
from pathlib import Path
import subprocess
import time

import httpx

import bazel
from test_scheduler_client import configure_auth, metadata, request


# Default-policy retries start at index five. Qualification entries do not
# retry in this demo because all ten qualification runs pass.
INITIAL_ATTEMPTS = 5


def read_bep(path: Path) -> dict[str, str]:
    """Map each Bazel label to the result from one retry invocation."""
    statuses: dict[str, str] = {}
    with path.open() as events:
        for line in events:
            event = json.loads(line)
            label = event.get("id", {}).get("testResult", {}).get("label")
            if label:
                statuses[label] = event["testResult"]["status"]
    return statuses


def run_attempts(
    worker: int, invocation: int, attempt_index: int, labels: list[str]
) -> tuple[int, dict[str, str]]:
    invocation += 1
    bep = Path(f"bep-runner-{worker}-{invocation}.json")
    args = bazel.command() + [
        "--test_output=errors",
        f"--test_env=DEMO_ATTEMPT_INDEX={attempt_index}",
        f"--build_event_json_file={bep}",
    ]
    kind = "INITIAL"
    if attempt_index >= INITIAL_ATTEMPTS:
        # A retry must execute again even when the initial result is cached.
        args.append("--nocache_test_results")
        kind = "RETRY (--nocache_test_results enabled)"
    print(
        f"Runner {worker}, invocation {invocation}: {kind} "
        f"attempt={attempt_index}, targets={len(labels)}"
    )

    result = subprocess.run(args + labels, check=False)
    statuses = read_bep(bep)
    counts = {
        status: list(statuses.values()).count(status) for status in set(statuses.values())
    }
    print(
        f"Runner {worker}, invocation {invocation}: "
        f"Bazel exit={result.returncode}, results={counts}"
    )
    return invocation, statuses


def main() -> None:
    configure_auth()
    pool_id = metadata("get")
    worker = int(os.environ["BUILDKITE_PARALLEL_JOB"]) + 1
    worker_count = int(os.environ["BUILDKITE_PARALLEL_JOB_COUNT"])
    invocation = 0
    empty_polls = 0
    state = "unknown"
    print(f"Runner {worker}/{worker_count} consuming pool {pool_id}")
    # A short stagger prevents all five jobs from polling at the same instant.
    time.sleep((worker - 1) * 2)

    while empty_polls < 90:
        try:
            lease_response = request(
                "POST", f"/pools/{pool_id}/leases", {"lease_ttl_seconds": 300}
            )
        except httpx.HTTPError as error:
            print(f"Runner {worker}: lease request unavailable ({error}); retrying")
            empty_polls += 1
            time.sleep(10)
            continue

        state = lease_response["pool"]["state"]
        lease = lease_response.get("lease")
        if lease is None:
            if state == "consumed":
                print(
                    f"Runner {worker}: pool consumed after {invocation} "
                    "local Bazel invocations"
                )
                break
            empty_polls += 1
            print(
                f"Runner {worker}: no work yet (pool state {state}); "
                "waiting for policy evaluation"
            )
            time.sleep(10)
            continue

        empty_polls = 0
        attempts = lease["attempts"]
        # The dispatcher must finish every initial attempt before this step.
        # Fail if a retry consumer crosses that ownership boundary.
        if any(attempt["attempt_index"] < INITIAL_ATTEMPTS for attempt in attempts):
            raise RuntimeError("Retry consumer received an unfinished initial attempt")
        initial_count = sum(
            attempt["attempt_index"] < INITIAL_ATTEMPTS for attempt in attempts
        )
        print(
            f"Runner {worker}: leased {len(attempts)} targets "
            f"(initial={initial_count}, retry={len(attempts) - initial_count})"
        )

        all_statuses: dict[str, str] = {}
        # DEMO_ATTEMPT_INDEX applies to one Bazel invocation. Keep attempts
        # with different indexes in separate invocations.
        for attempt_index in sorted({attempt["attempt_index"] for attempt in attempts}):
            labels = [
                attempt["selector"]
                for attempt in attempts
                if attempt["attempt_index"] == attempt_index
            ]
            invocation, statuses = run_attempts(
                worker, invocation, attempt_index, labels
            )
            all_statuses.update(statuses)

        missing_results = [
            attempt["selector"]
            for attempt in attempts
            if attempt["selector"] not in all_statuses
        ]
        if missing_results:
            raise RuntimeError(
                f"Bazel produced no test result for {len(missing_results)} leased targets"
            )

        completions = [
            {
                "attempt_id": attempt["id"],
                "result": (
                    "passed"
                    if all_statuses.get(attempt["selector"]) == "PASSED"
                    else "failed"
                ),
            }
            for attempt in attempts
        ]
        request(
            "POST",
            f"/pools/{pool_id}/leases/complete",
            {"leases": [{"lease_id": lease["id"], "attempts": completions}]},
        )
        passed = sum(completion["result"] == "passed" for completion in completions)
        print(
            f"Runner {worker}: completed lease {lease['id']} with {passed} passed "
            f"and {len(completions) - passed} failed"
        )
        time.sleep(10)

    if state != "consumed":
        raise RuntimeError(
            f"Runner {worker}: pool did not reach consumed state before the poll limit"
        )


if __name__ == "__main__":
    main()
