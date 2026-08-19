#!/usr/bin/env bash
set -euo pipefail

index="$1"
test_file="${TEST_SRCDIR}/${TEST_WORKSPACE}/demo/test_demo.py"

exec "${PYTEST_BIN:?PYTEST_BIN must point to the uv-managed pytest}" \
  --quiet \
  --tb=short \
  "${test_file}::test_target_${index}"
