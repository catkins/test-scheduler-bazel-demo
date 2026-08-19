#!/usr/bin/env python3
"""Create, populate, and seal the demo Test Scheduler pool."""

import os

from test_scheduler_client import SUITE, configure_auth, metadata, request


TARGET_COUNT = 100
# Targets 0-89 use the default policy. Targets 90-99 use qualification.
QUALIFICATION_START = 90


def main() -> None:
    configure_auth()
    pool = request(
        "POST",
        "/pools",
        {
            "suite": SUITE,
            "pipeline": os.environ["BUILDKITE_PIPELINE_SLUG"],
            "build_id": os.environ["BUILDKITE_BUILD_ID"],
            "key": f"bazel-demo-{os.environ['BUILDKITE_BUILD_ID']}",
            "ttl_seconds": 3_600,
            # Each entry costs one unit. A lease has 300 units and can contain
            # at most 300 attempts.
            "lease": {"costs": {"custom": 300}, "max_attempts": 300},
            "attempt_policy": {
                # One pass completes an entry. One initial failure leaves room
                # for one policy-generated retry.
                "default": {
                    "max_attempts": 2,
                    "max_failed": 2,
                    "max_passed": 1,
                    "min_attempts": 1,
                    "min_passed": 1,
                    "parallel_attempts": 1,
                    "initial_attempts": 1,
                },
                # Qualification requires ten passes. One failure ends the
                # entry because max_failed is one.
                "qualification": {
                    "max_attempts": 10,
                    "max_failed": 1,
                    "max_passed": 10,
                    "min_attempts": 10,
                    "min_passed": 10,
                    "parallel_attempts": 10,
                    "initial_attempts": 10,
                },
            },
        },
    )
    pool_id = pool["id"]
    metadata("set", pool_id)
    print(f"Created Test Scheduler pool {pool_id} for {TARGET_COUNT} distinct Bazel targets")

    for start in range(0, TARGET_COUNT, 100):
        entries = [
            {
                "selector_type": "custom",
                "selector": f"//demo:target_{index}",
                "costs": {"custom": 1},
                "priority": 0,
                "attempt_policy": (
                    "qualification" if index >= QUALIFICATION_START else "default"
                ),
                "meta_data": {
                    "framework": "bazel",
                    "demo": "test-scheduler-bazel",
                    # Lease responses include metadata but not the policy key.
                    # Keep a copy here so a consumer can inspect the choice.
                    "attempt_policy": (
                        "qualification" if index >= QUALIFICATION_START else "default"
                    ),
                },
            }
            for index in range(start, start + 100)
        ]
        request("POST", f"/pools/{pool_id}/entries", {"entries": entries})
        print(f"Uploaded targets {start}-{start + 99}")

    request("PATCH", f"/pools/{pool_id}", {"populating": False})
    print(f"Uploaded all {TARGET_COUNT} entries and sealed the pool")


if __name__ == "__main__":
    main()
