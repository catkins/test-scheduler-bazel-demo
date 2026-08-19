#!/usr/bin/env python3
"""Minimal HTTP client for the Test Scheduler demo."""

import json
import os
import sys

import httpx


def request(method: str, path: str, body: object | None = None) -> object:
    """Make an authenticated scheduler request and return its JSON body."""
    response = httpx.request(
        method,
        f"{os.environ['TEST_SCHEDULER_URL']}{path}",
        headers={"Authorization": f"Bearer {os.environ['TEST_SCHEDULER_TOKEN']}"},
        json=body,
        timeout=30,
    )
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
