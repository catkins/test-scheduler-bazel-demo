#!/usr/bin/env bash
set -euo pipefail

kind="$1"
attempt="${DEMO_ATTEMPT_INDEX:-0}"

echo "target=${TEST_TARGET:-unknown} scheduler_attempt=${attempt} kind=${kind}"

if [[ "${kind}" == "fail-first" && "${attempt}" == "0" ]]; then
  echo "Intentional initial failure; this target should be retried alone."
  exit 1
fi
