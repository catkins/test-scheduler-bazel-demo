#!/usr/bin/env python3
"""Run the single pytest test represented by a Bazel target."""

import os
from pathlib import Path
import sys


index = sys.argv[1]
test_file = Path(os.environ["TEST_SRCDIR"]) / os.environ["TEST_WORKSPACE"] / "demo/test_demo.py"
python = os.environ["PYTHON_BIN"]
os.execv(
    python,
    [python, "-m", "pytest", "--quiet", "--tb=short", f"{test_file}::test_target_{index}"],
)
