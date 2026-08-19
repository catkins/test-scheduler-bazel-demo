#!/usr/bin/env python3
"""Populate, consume, and verify the Test Scheduler Bazel demo pool."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx

from test_scheduler_client import request


ORG = "catkins-test"
SUITE = "test-scheduler-bazel-demo"
TARGET_COUNT = 1_000
EXPECTED_FAILURES = 100
SCHEDULER_URL = f"https://api.buildkite.com/v2/organizations/{ORG}/test-scheduler"
AUDIENCE = f"https://buildkite.com/organizations/{ORG}/analytics/suites/{SUITE}"
POOL_METADATA_KEY = "test-scheduler-pool-id"


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, capture_output=True)


def configure_auth() -> None:
    token = command(
        "buildkite-agent",
        "oidc",
        "request-token",
        "--audience",
        AUDIENCE,
        "--lifetime",
        "900",
        "--claim",
        "organization_id",
        "--claim",
        "pipeline_id",
        "--claim",
        "build_id",
        "--claim",
        "job_id",
    ).stdout.strip()
    os.environ["TEST_SCHEDULER_URL"] = SCHEDULER_URL
    os.environ["TEST_SCHEDULER_TOKEN"] = token
    os.environ["BUILDKITE_ANALYTICS_TOKEN"] = token


def metadata(action: str, value: str | None = None) -> str:
    args = ["buildkite-agent", "meta-data", action, POOL_METADATA_KEY]
    if value is not None:
        args.append(value)
    return command(*args).stdout.strip()


def setup() -> None:
    pool = request(
        "POST",
        "/pools",
        {
            "suite": SUITE,
            "pipeline": os.environ["BUILDKITE_PIPELINE_SLUG"],
            "build_id": os.environ["BUILDKITE_BUILD_ID"],
            "key": f"bazel-demo-{os.environ['BUILDKITE_BUILD_ID']}",
            "ttl_seconds": 1_800,
            "lease": {"costs": {"custom": 300}, "max_attempts": 300},
            "attempt_policy": {
                "max_attempts": 2,
                "max_failed": 2,
                "max_passed": 1,
                "min_attempts": 1,
                "min_passed": 1,
                "parallel_attempts": 1,
                "initial_attempts": 1,
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


def read_bep(path: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    with path.open() as events:
        for line in events:
            event = json.loads(line)
            label = event.get("id", {}).get("testResult", {}).get("label")
            if label:
                statuses[label] = event["testResult"]["status"]
    return statuses


def run_attempts(worker: int, invocation: int, attempt_index: int, labels: list[str]) -> tuple[int, dict[str, str]]:
    invocation += 1
    bep = Path(f"bep-runner-{worker}-{invocation}.json")
    args = [
        "bazelisk",
        "test",
        "--test_output=errors",
        f"--test_env=DEMO_ATTEMPT_INDEX={attempt_index}",
        f"--test_env=PYTHON_BIN={sys.executable}",
        "--test_env=BUILDKITE_ANALYTICS_TOKEN",
        "--test_env=BUILDKITE_BRANCH",
        "--test_env=BUILDKITE_BUILD_ID",
        "--test_env=BUILDKITE_BUILD_NUMBER",
        "--test_env=BUILDKITE_BUILD_URL",
        "--test_env=BUILDKITE_COMMIT",
        "--test_env=BUILDKITE_JOB_ID",
        "--test_env=BUILDKITE_PIPELINE_SLUG",
        f"--build_event_json_file={bep}",
    ]
    kind = "INITIAL"
    if attempt_index > 0:
        args.append("--nocache_test_results")
        kind = "RETRY (--nocache_test_results enabled)"
    print(f"Runner {worker}, invocation {invocation}: {kind} attempt={attempt_index}, targets={len(labels)}")

    result = command(*args, *labels, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    statuses = read_bep(bep)
    counts = {status: list(statuses.values()).count(status) for status in set(statuses.values())}
    print(f"Runner {worker}, invocation {invocation}: Bazel exit={result.returncode}, results={counts}")
    return invocation, statuses


def consume() -> None:
    pool_id = metadata("get")
    worker = int(os.environ["BUILDKITE_PARALLEL_JOB"]) + 1
    invocation = 0
    empty_polls = 0
    state = "unknown"
    print(f"Runner {worker}/2 consuming pool {pool_id}")
    time.sleep((worker - 1) * 2)

    while empty_polls < 90:
        try:
            lease_response = request(
                "POST", f"/pools/{pool_id}/leases", {"lease_ttl_seconds": 120}
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
                print(f"Runner {worker}: pool consumed after {invocation} local Bazel invocations")
                break
            empty_polls += 1
            print(f"Runner {worker}: no work yet (pool state {state}); waiting for policy evaluation")
            time.sleep(10)
            continue

        empty_polls = 0
        attempts = lease["attempts"]
        initial_count = sum(attempt["attempt_index"] == 0 for attempt in attempts)
        print(
            f"Runner {worker}: leased {len(attempts)} targets "
            f"(initial={initial_count}, retry={len(attempts) - initial_count})"
        )

        all_statuses: dict[str, str] = {}
        for attempt_index in sorted({attempt["attempt_index"] for attempt in attempts}):
            labels = [
                attempt["selector"]
                for attempt in attempts
                if attempt["attempt_index"] == attempt_index
            ]
            invocation, statuses = run_attempts(worker, invocation, attempt_index, labels)
            all_statuses.update(statuses)

        completions = [
            {
                "attempt_id": attempt["id"],
                "result": "passed" if all_statuses.get(attempt["selector"]) == "PASSED" else "failed",
            }
            for attempt in attempts
        ]
        request(
            "POST",
            f"/pools/{pool_id}/leases/complete",
            {"leases": [{"lease_id": lease["id"], "attempts": completions}]},
        )
        passed = sum(completion["result"] == "passed" for completion in completions)
        print(f"Runner {worker}: completed lease {lease['id']} with {passed} passed and {len(completions) - passed} failed")
        time.sleep(10)

    if state != "consumed":
        raise RuntimeError(f"Runner {worker}: pool did not reach consumed state before the poll limit")


def verify() -> None:
    pool_id = metadata("get")
    metrics = request("GET", f"/pools/{pool_id}/metrics")
    print(f"Final pool metrics: {json.dumps(metrics, separators=(',', ':'))}")

    attempts = metrics["attempts"]
    checks = {
        "pool is consumed": metrics["pool"]["state"] == "consumed",
        "pool is drained": metrics["pool"]["drained"],
        "entry count": metrics["entries"]["total"] == TARGET_COUNT,
        "attempt count": attempts["total"] == TARGET_COUNT + EXPECTED_FAILURES,
        "completed count": attempts["states"]["completed"]["count"] == TARGET_COUNT + EXPECTED_FAILURES,
        "waiting count": attempts["states"]["waiting"]["count"] == 0,
        "leased count": attempts["states"]["leased"]["count"] == 0,
        "passed count": attempts["results"]["passed"] == TARGET_COUNT,
        "failed count": attempts["results"]["failed"] == EXPECTED_FAILURES,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    if failed_checks:
        raise RuntimeError(f"Pool verification failed: {', '.join(failed_checks)}")
    print(f"Verified {TARGET_COUNT} initial attempts and {EXPECTED_FAILURES} policy-generated retries")
    print(f"Verified final results: {TARGET_COUNT} passed attempts, {EXPECTED_FAILURES} intentional failed attempts")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("setup", "consume", "verify"))
    args = parser.parse_args()
    configure_auth()
    globals()[args.action]()


if __name__ == "__main__":
    main()
