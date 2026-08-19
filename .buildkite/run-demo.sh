#!/usr/bin/env bash
set -euo pipefail

readonly org="catkins-test"
readonly suite="test-scheduler-bazel-demo"
readonly target_count=1000
readonly expected_failures=100
readonly scheduler="https://api.buildkite.com/v2/organizations/${org}/test-scheduler"
readonly audience="https://buildkite.com/organizations/${org}/analytics/suites/${suite}"

export TEST_SCHEDULER_URL="${scheduler}"
TEST_SCHEDULER_TOKEN="$(buildkite-agent oidc request-token \
  --audience "${audience}" \
  --lifetime 900 \
  --claim organization_id \
  --claim pipeline_id \
  --claim build_id \
  --claim job_id)"
export TEST_SCHEDULER_TOKEN
export BUILDKITE_ANALYTICS_TOKEN="${TEST_SCHEDULER_TOKEN}"

api() {
  .venv/bin/python .buildkite/test_scheduler_client.py "$@"
}

setup() {
  local pool_body pool pool_id start entries

  pool_body="$(jq -nc \
    --arg suite "${suite}" \
    --arg pipeline "${BUILDKITE_PIPELINE_SLUG}" \
    --arg build_id "${BUILDKITE_BUILD_ID}" \
    --arg key "bazel-demo-${BUILDKITE_BUILD_ID}" \
    '{suite:$suite,pipeline:$pipeline,build_id:$build_id,key:$key,ttl_seconds:1800,
      lease:{costs:{custom:300},max_attempts:300},
      attempt_policy:{max_attempts:2,max_failed:2,max_passed:1,min_attempts:1,min_passed:1,parallel_attempts:1,initial_attempts:1}}')"
  pool="$(api POST /pools "${pool_body}")"
  pool_id="$(jq -r .id <<<"${pool}")"
  buildkite-agent meta-data set test-scheduler-pool-id "${pool_id}"
  echo "Created Test Scheduler pool ${pool_id} for ${target_count} distinct Bazel targets"

  # The entries API accepts at most 100 entries per request.
  for (( start = 0; start < target_count; start += 100 )); do
    entries="$(jq -nc --argjson start "${start}" '{entries:[
      range($start; $start + 100) as $i |
      {selector_type:"custom",selector:("//demo:target_" + ($i | tostring)),costs:{custom:1},priority:0,
       meta_data:{framework:"bazel",demo:"customer-local-bazel"}}
    ]}')"
    api POST "/pools/${pool_id}/entries" "${entries}" >/dev/null
    echo "Uploaded targets ${start}-$((start + 99))"
  done

  api PATCH "/pools/${pool_id}" '{"populating":false}' >/dev/null
  echo "Uploaded all ${target_count} entries and sealed the pool"
}

consume() {
  local pool_id worker invocation empty_polls lease_response state lease_id
  local initial_count retry_count all_statuses attempt_index bep statuses
  local completions completion_body bazel_result
  local -a labels bazel_args

  pool_id="$(buildkite-agent meta-data get test-scheduler-pool-id)"
  worker="$((BUILDKITE_PARALLEL_JOB + 1))"
  invocation=0
  empty_polls=0
  state="unknown"
  echo "Runner ${worker}/2 consuming pool ${pool_id}"

  # Stagger polling so both jobs do not make empty lease requests together.
  sleep "$((BUILDKITE_PARALLEL_JOB * 2))"

  while (( empty_polls < 90 )); do
    if ! lease_response="$(api POST "/pools/${pool_id}/leases" '{"lease_ttl_seconds":120}')"; then
      echo "Runner ${worker}: lease request was rate limited or temporarily unavailable; retrying"
      empty_polls=$((empty_polls + 1))
      sleep 10
      continue
    fi
    state="$(jq -r .pool.state <<<"${lease_response}")"

    if [[ "$(jq -r '.lease == null' <<<"${lease_response}")" == "true" ]]; then
      if [[ "${state}" == "consumed" ]]; then
        echo "Runner ${worker}: pool consumed after ${invocation} local Bazel invocations"
        break
      fi

      empty_polls=$((empty_polls + 1))
      echo "Runner ${worker}: no work yet (pool state ${state}); waiting for policy evaluation"
      sleep 10
      continue
    fi

    empty_polls=0
    lease_id="$(jq -r .lease.id <<<"${lease_response}")"
    initial_count="$(jq '[.lease.attempts[] | select(.attempt_index == 0)] | length' <<<"${lease_response}")"
    retry_count="$(jq '[.lease.attempts[] | select(.attempt_index > 0)] | length' <<<"${lease_response}")"
    echo "Runner ${worker}: leased $(jq '.lease.attempts | length' <<<"${lease_response}") targets (initial=${initial_count}, retry=${retry_count})"

    # A lease may contain both initial attempts and retries. Bazel needs one
    # invocation per attempt index so each target gets the correct test input.
    all_statuses='{}'
    while IFS= read -r attempt_index; do
      mapfile -t labels < <(jq -r --argjson attempt_index "${attempt_index}" \
        '.lease.attempts[] | select(.attempt_index == $attempt_index) | .selector' <<<"${lease_response}")
      invocation=$((invocation + 1))
      bep="bep-runner-${worker}-${invocation}.json"
      bazel_args=(test
        --test_output=errors
        --test_env="DEMO_ATTEMPT_INDEX=${attempt_index}"
        --test_env="PYTEST_BIN=${PWD}/.venv/bin/pytest"
        --test_env=BUILDKITE_ANALYTICS_TOKEN
        --test_env=BUILDKITE_BRANCH
        --test_env=BUILDKITE_BUILD_ID
        --test_env=BUILDKITE_BUILD_NUMBER
        --test_env=BUILDKITE_BUILD_URL
        --test_env=BUILDKITE_COMMIT
        --test_env=BUILDKITE_JOB_ID
        --test_env=BUILDKITE_PIPELINE_SLUG
        --build_event_json_file="${bep}")

      if (( attempt_index > 0 )); then
        bazel_args+=(--nocache_test_results)
        echo "Runner ${worker}, invocation ${invocation}: RETRY attempt=${attempt_index}, targets=${#labels[@]}, --nocache_test_results enabled"
      else
        echo "Runner ${worker}, invocation ${invocation}: INITIAL attempt=${attempt_index}, targets=${#labels[@]}"
      fi

      bazel_result=0
      bazelisk "${bazel_args[@]}" "${labels[@]}" || bazel_result=$?
      statuses="$(jq -sc '
        map(select(.id.testResult.label))
        | group_by(.id.testResult.label)
        | map({key:.[0].id.testResult.label,value:.[-1].testResult.status})
        | from_entries' "${bep}")"
      all_statuses="$(jq -c --argjson statuses "${statuses}" '. + $statuses' <<<"${all_statuses}")"
      echo "Runner ${worker}, invocation ${invocation}: Bazel exit=${bazel_result}, results=$(jq -c 'group_by(.) | map({(.[0]):length}) | add' <<<"$(jq '[.[]]' <<<"${statuses}")")"
    done < <(jq -r '[.lease.attempts[].attempt_index] | unique[]' <<<"${lease_response}")

    completions="$(jq -c --argjson statuses "${all_statuses}" '[
      .lease.attempts[] |
      {attempt_id:.id,result:(if $statuses[.selector] == "PASSED" then "passed" else "failed" end)}
    ]' <<<"${lease_response}")"
    completion_body="$(jq -nc --arg lease_id "${lease_id}" --argjson attempts "${completions}" '{leases:[{lease_id:$lease_id,attempts:$attempts}]}')"
    api POST "/pools/${pool_id}/leases/complete" "${completion_body}" >/dev/null
    echo "Runner ${worker}: completed lease ${lease_id} with $(jq '[.[] | select(.result == "passed")] | length' <<<"${completions}") passed and $(jq '[.[] | select(.result == "failed")] | length' <<<"${completions}") failed"

    # Policy evaluation is asynchronous, and empty lease polls are rate limited.
    sleep 10
  done

  if [[ "${state}" != "consumed" ]]; then
    echo "Runner ${worker}: pool did not reach consumed state before the poll limit" >&2
    exit 1
  fi
}

verify() {
  local pool_id metrics

  pool_id="$(buildkite-agent meta-data get test-scheduler-pool-id)"
  metrics="$(api GET "/pools/${pool_id}/metrics")"
  echo "Final pool metrics: $(jq -c . <<<"${metrics}")"

  jq -e \
    --argjson targets "${target_count}" \
    --argjson failures "${expected_failures}" \
    '.pool.state == "consumed"
      and .pool.drained
      and .entries.total == $targets
      and .attempts.total == ($targets + $failures)
      and .attempts.states.completed.count == ($targets + $failures)
      and .attempts.states.waiting.count == 0
      and .attempts.states.leased.count == 0
      and .attempts.results.passed == $targets
      and .attempts.results.failed == $failures' <<<"${metrics}" >/dev/null

  echo "Verified ${target_count} initial attempts and ${expected_failures} policy-generated retries"
  echo "Verified final results: ${target_count} passed attempts, ${expected_failures} intentional failed attempts"
}

case "${1:-}" in
  setup) setup ;;
  consume) consume ;;
  verify) verify ;;
  *)
    echo "Usage: $0 {setup|consume|verify}" >&2
    exit 2
    ;;
esac
