#!/usr/bin/env python3
"""Minimal HTTP client for the Test Scheduler demo."""

import json
import os
import sys

import httpx


def main() -> None:
    method, path, *body = sys.argv[1:]
    try:
        response = httpx.request(
            method,
            f"{os.environ['TEST_SCHEDULER_URL']}{path}",
            headers={"Authorization": f"Bearer {os.environ['TEST_SCHEDULER_TOKEN']}"},
            json=json.loads(body[0]) if body else None,
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        if isinstance(error, httpx.HTTPStatusError):
            print(error.response.text, file=sys.stderr)
        else:
            print(error, file=sys.stderr)
        raise SystemExit(1) from None

    print(response.text)


if __name__ == "__main__":
    main()
