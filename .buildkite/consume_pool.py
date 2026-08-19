#!/usr/bin/env python3
"""Lease Test Scheduler attempts and execute their Bazel targets."""

import json
from pathlib import Path
import subprocess
import time

import httpx

import bazel
from test_scheduler_client import configure_auth, metadata, request


DEFAULT_INITIAL_ATTEMPTS = 1
QUALIFICATION_INITIAL_ATTEMPTS = 10
# EX_TEMPFAIL tells Buildkite that a replacement consumer can safely try again.
EX_TEMPFAIL = 75


def is_temporary_http_error(error: httpx.HTTPError) -> bool:
    """Return whether a scheduler request can reasonably succeed later."""
    if not isinstance(error, httpx.HTTPStatusError):
        return True
    return error.response.status_code in {408, 429} or error.response.status_code >= 500


def initial_attempts_for(attempt: dict) -> int:
    """Return the initial allocation for the attempt's policy."""
    if attempt["meta_data"]["attempt_policy"] == "qualification":
        return QUALIFICATION_INITIAL_ATTEMPTS
    return DEFAULT_INITIAL_ATTEMPTS


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
    invocation: int, attempt_index: int, labels: list[str]
) -> tuple[int, dict[str, str]]:
    invocation += 1
    bep = Path(f"bep-retry-{invocation}.json")
    args = bazel.command() + [
        "--test_output=errors",
        f"--test_env=DEMO_ATTEMPT_INDEX={attempt_index}",
        f"--build_event_json_file={bep}",
        # The consumer runs only policy-generated retries. Do not return the
        # cached result from the failed initial attempt.
        "--nocache_test_results",
    ]
    print(
        f"Retry invocation {invocation}: "
        f"attempt={attempt_index}, targets={len(labels)}"
    )

    result = subprocess.run(args + labels, check=False)
    statuses = read_bep(bep)
    counts = {
        status: list(statuses.values()).count(status) for status in set(statuses.values())
    }
    print(
        f"Retry invocation {invocation}: "
        f"Bazel exit={result.returncode}, results={counts}"
    )
    return invocation, statuses


def main() -> None:
    configure_auth()
    pool_id = metadata("get")
    invocation = 0
    empty_polls = 0
    state = "unknown"
    print(f"Retry consumer is consuming pool {pool_id}")

    while empty_polls < 90:
        try:
            lease_response = request(
                "POST", f"/pools/{pool_id}/leases", {"lease_ttl_seconds": 300}
            )
        except httpx.HTTPError as error:
            if not is_temporary_http_error(error):
                raise
            print(f"Lease request is temporarily unavailable ({error}); retrying")
            empty_polls += 1
            time.sleep(10)
            continue

        state = lease_response["pool"]["state"]
        lease = lease_response.get("lease")
        if lease is None:
            if state == "consumed":
                print(
                    f"Pool consumed after {invocation} "
                    "local Bazel invocations"
                )
                break
            empty_polls += 1
            print(
                f"No work yet (pool state {state}); "
                "waiting for policy evaluation"
            )
            time.sleep(10)
            continue

        empty_polls = 0
        attempts = lease["attempts"]
        # The dispatcher must finish every initial attempt before this step.
        # Fail if a retry consumer crosses that ownership boundary.
        if any(
            attempt["attempt_index"] < initial_attempts_for(attempt)
            for attempt in attempts
        ):
            raise RuntimeError("Retry consumer received an unfinished initial attempt")
        print(f"Leased {len(attempts)} retry targets")

        all_statuses: dict[str, str] = {}
        # DEMO_ATTEMPT_INDEX applies to one Bazel invocation. Keep attempts
        # with different indexes in separate invocations.
        for attempt_index in sorted({attempt["attempt_index"] for attempt in attempts}):
            labels = [
                attempt["selector"]
                for attempt in attempts
                if attempt["attempt_index"] == attempt_index
            ]
            invocation, statuses = run_attempts(invocation, attempt_index, labels)
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
            f"Completed lease {lease['id']} with {passed} passed "
            f"and {len(completions) - passed} failed"
        )
        time.sleep(10)

    if state != "consumed":
        print("Pool did not reach consumed state before the poll limit")
        raise SystemExit(EX_TEMPFAIL)


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as error:
        if is_temporary_http_error(error):
            print(f"Scheduler request failed temporarily: {error}")
            raise SystemExit(EX_TEMPFAIL) from None
        raise
