#!/usr/bin/env python3
"""Create, populate, and seal the demo Test Scheduler pool."""

import os

from test_scheduler_client import SUITE, configure_auth, metadata, request


TARGET_COUNT = 1_000


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
            "lease": {"costs": {"custom": 300}, "max_attempts": 300},
            "attempt_policy": {
                "max_attempts": 6,
                "max_failed": 2,
                "max_passed": 5,
                "min_attempts": 5,
                "min_passed": 5,
                "parallel_attempts": 5,
                "initial_attempts": 5,
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
                "meta_data": {"framework": "bazel", "demo": "customer-local-bazel"},
            }
            for index in range(start, start + 100)
        ]
        request("POST", f"/pools/{pool_id}/entries", {"entries": entries})
        print(f"Uploaded targets {start}-{start + 99}")

    request("PATCH", f"/pools/{pool_id}", {"populating": False})
    print(f"Uploaded all {TARGET_COUNT} entries and sealed the pool")


if __name__ == "__main__":
    main()
