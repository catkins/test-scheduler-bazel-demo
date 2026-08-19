#!/usr/bin/env python3
"""Run the single pytest test represented by a Bazel target."""

from pathlib import Path
import sys

import pytest


def main() -> int:
    index = sys.argv[1]
    test_file = Path(__file__).with_name("test_demo.py")
    return pytest.main(
        ["--quiet", "--tb=short", f"{test_file}::test_target_{index}"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
