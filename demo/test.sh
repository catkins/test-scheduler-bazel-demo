#!/usr/bin/env bash
set -euo pipefail

index="$1"
attempt="${DEMO_ATTEMPT_INDEX:-0}"

sleep 0.01
echo "target=${TEST_TARGET:-unknown} scheduler_attempt=${attempt} index=${index}"

if (( index % 10 == 0 && attempt == 0 )); then
  echo "Intentional initial failure; Test Scheduler should generate a retry."
  exit 1
fi
