#!/usr/bin/env python3
"""Minimal HTTP client for the Test Scheduler demo."""

import json
import os
import subprocess
import sys

import httpx


ORG = "catkins-test"
SUITE = "test-scheduler-bazel-demo"
SCHEDULER_URL = f"https://api.buildkite.com/v2/organizations/{ORG}/test-scheduler"
AUDIENCE = f"https://buildkite.com/organizations/{ORG}/analytics/suites/{SUITE}"
POOL_METADATA_KEY = "test-scheduler-pool-id"


def configure_auth() -> None:
    # The requested claims bind the token to this organization, pipeline,
    # build, and job. The token expires after 30 minutes.
    token = subprocess.run(
        [
            "buildkite-agent",
            "oidc",
            "request-token",
            "--audience",
            AUDIENCE,
            "--lifetime",
            "1800",
            "--claim",
            "organization_id",
            "--claim",
            "pipeline_id",
            "--claim",
            "build_id",
            "--claim",
            "job_id",
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    os.environ["TEST_SCHEDULER_URL"] = SCHEDULER_URL
    os.environ["TEST_SCHEDULER_TOKEN"] = token
    os.environ["BUILDKITE_ANALYTICS_TOKEN"] = token


def metadata(action: str, value: str | None = None) -> str:
    """Share the pool ID between jobs through Buildkite build metadata."""
    args = ["buildkite-agent", "meta-data", action, POOL_METADATA_KEY]
    if value is not None:
        args.append(value)
    return subprocess.run(
        args, check=True, text=True, capture_output=True
    ).stdout.strip()


def request(method: str, path: str, body: object | None = None) -> object:
    """Make an authenticated scheduler request and return its JSON body."""
    response = httpx.request(
        method,
        f"{os.environ['TEST_SCHEDULER_URL']}{path}",
        headers={"Authorization": f"Bearer {os.environ['TEST_SCHEDULER_TOKEN']}"},
        json=body,
        timeout=30,
    )
    if not response.is_success:
        print(response.text, file=sys.stderr)
    response.raise_for_status()
    return response.json() if response.content else None


def main() -> None:
    method, path, *body = sys.argv[1:]
    try:
        result = request(method, path, json.loads(body[0]) if body else None)
    except httpx.HTTPError as error:
        if isinstance(error, httpx.HTTPStatusError):
            print(error.response.text, file=sys.stderr)
        else:
            print(error, file=sys.stderr)
        raise SystemExit(1) from None

    print(json.dumps(result))


if __name__ == "__main__":
    main()
