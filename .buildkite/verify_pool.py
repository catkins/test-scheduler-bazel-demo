#!/usr/bin/env python3
"""Verify the final Test Scheduler pool metrics."""

import json

from test_scheduler_client import configure_auth, metadata, request


TARGET_COUNT = 1_000
EXPECTED_FAILURES = 100


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
        "attempt count": attempts["total"] == TARGET_COUNT + EXPECTED_FAILURES,
        "completed count": (
            attempts["states"]["completed"]["count"]
            == TARGET_COUNT + EXPECTED_FAILURES
        ),
        "waiting count": attempts["states"]["waiting"]["count"] == 0,
        "leased count": attempts["states"]["leased"]["count"] == 0,
        "passed count": attempts["results"]["passed"] == TARGET_COUNT,
        "failed count": attempts["results"]["failed"] == EXPECTED_FAILURES,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    if failed_checks:
        raise RuntimeError(f"Pool verification failed: {', '.join(failed_checks)}")
    print(
        f"Verified {TARGET_COUNT} initial attempts and {EXPECTED_FAILURES} "
        "policy-generated retries"
    )
    print(
        f"Verified final results: {TARGET_COUNT} passed attempts, "
        f"{EXPECTED_FAILURES} intentional failed attempts"
    )


if __name__ == "__main__":
    main()
