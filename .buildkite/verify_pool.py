#!/usr/bin/env python3
"""Verify the final Test Scheduler pool metrics."""

import json

from test_scheduler_client import configure_auth, metadata, request


TARGET_COUNT = 100
DEFAULT_TARGET_COUNT = 90
DEFAULT_INITIAL_ATTEMPTS = 5
QUALIFICATION_TARGET_COUNT = 10
QUALIFICATION_INITIAL_ATTEMPTS = 10
EXPECTED_FAILURES = 10
EXPECTED_INITIAL_EXECUTIONS = (
    DEFAULT_TARGET_COUNT * DEFAULT_INITIAL_ATTEMPTS
    + QUALIFICATION_TARGET_COUNT * QUALIFICATION_INITIAL_ATTEMPTS
)
EXPECTED_EXECUTIONS = EXPECTED_INITIAL_EXECUTIONS + EXPECTED_FAILURES
EXPECTED_PASSES = EXPECTED_INITIAL_EXECUTIONS


def main() -> None:
    configure_auth()
    pool_id = metadata("get")
    metrics = request("GET", f"/pools/{pool_id}/metrics")
    print(f"Final pool metrics: {json.dumps(metrics, separators=(',', ':'))}")

    attempts = metrics["attempts"]
    checks = {
        "pool is consumed": metrics["pool"]["state"] == "consumed",
        "pool is drained": metrics["pool"]["drained"],
        "entry count": metrics["entries"]["total"] == TARGET_COUNT,
        "attempt count": attempts["total"] >= EXPECTED_EXECUTIONS,
        "completed count": (
            attempts["states"]["completed"]["count"] == EXPECTED_EXECUTIONS
        ),
        "canceled count": (
            attempts["states"]["canceled"]["count"]
            == attempts["total"] - EXPECTED_EXECUTIONS
        ),
        "waiting count": attempts["states"]["waiting"]["count"] == 0,
        "leased count": attempts["states"]["leased"]["count"] == 0,
        "passed count": attempts["results"]["passed"] == EXPECTED_PASSES,
        "failed count": attempts["results"]["failed"] == EXPECTED_FAILURES,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    if failed_checks:
        raise RuntimeError(f"Pool verification failed: {', '.join(failed_checks)}")
    print(
        f"Verified {EXPECTED_INITIAL_EXECUTIONS} initial attempts and "
        f"{EXPECTED_FAILURES} policy-generated retries"
    )
    print(
        f"Verified {EXPECTED_EXECUTIONS} completed executions and "
        f"{attempts['states']['canceled']['count']} canceled speculative attempts"
    )
    print(
        f"Verified final results: {EXPECTED_PASSES} passed attempts, "
        f"{EXPECTED_FAILURES} intentional failed attempts"
    )


if __name__ == "__main__":
    main()
