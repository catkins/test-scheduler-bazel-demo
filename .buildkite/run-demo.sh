#!/usr/bin/env bash
set -euo pipefail

readonly org="catkins-test"
readonly suite="test-scheduler-bazel-demo"
readonly scheduler="https://api.buildkite.com/v2/organizations/${org}/test-scheduler"
readonly audience="https://buildkite.com/organizations/${org}/analytics/suites/${suite}"

curl --fail --location --silent --show-error \
  --output bazelisk \
  https://github.com/bazelbuild/bazelisk/releases/download/v1.29.0/bazelisk-linux-amd64
curl --fail --location --silent --show-error \
  --output bazelisk.sha256 \
  https://github.com/bazelbuild/bazelisk/releases/download/v1.29.0/bazelisk-linux-amd64.sha256
echo "$(awk '{print $1}' bazelisk.sha256)  bazelisk" | sha256sum --check
chmod +x bazelisk

token="$(buildkite-agent oidc request-token \
  --audience "${audience}" \
  --lifetime 900 \
  --claim organization_id \
  --claim pipeline_id \
  --claim build_id \
  --claim job_id)"

api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local args=(--fail-with-body --silent --show-error --request "${method}"
    --header "Authorization: Bearer ${token}"
    --header "Content-Type: application/json")

  if [[ -n "${body}" ]]; then
    args+=(--data "${body}")
  fi

  curl "${args[@]}" "${scheduler}${path}"
}

selectors=(
  "//demo:parser_test"
  "//demo:planner_test"
  "//demo:executor_test"
  "//demo:flaky_remote_test"
)

pool_body="$(jq -nc \
  --arg suite "${suite}" \
  --arg pipeline "${BUILDKITE_PIPELINE_SLUG}" \
  --arg build_id "${BUILDKITE_BUILD_ID}" \
  --arg key "bazel-demo-${BUILDKITE_BUILD_ID}" \
  '{suite:$suite,pipeline:$pipeline,build_id:$build_id,key:$key,ttl_seconds:1800,
    lease:{costs:{custom:10},max_attempts:300},
    attempt_policy:{max_attempts:2,max_failed:2,max_passed:1,min_attempts:1,min_passed:1,parallel_attempts:1,initial_attempts:1}}')"
pool="$(api POST /pools "${pool_body}")"
pool_id="$(jq -r .id <<<"${pool}")"
echo "Created Test Scheduler pool ${pool_id} for ${#selectors[@]} Bazel targets"

entries='[]'
for selector in "${selectors[@]}"; do
  entries="$(jq -c --arg selector "${selector}" '. + [{selector_type:"custom",selector:$selector,costs:{custom:1},priority:0,meta_data:{framework:"bazel",demo:"customer-local-bazel"}}]' <<<"${entries}")"
done
api POST "/pools/${pool_id}/entries" "$(jq -nc --argjson entries "${entries}" '{entries:$entries}')" >/dev/null
api PATCH "/pools/${pool_id}" '{"populating":false}' >/dev/null
echo "Uploaded and sealed the target pool"

invocation=0
empty_polls=0
while (( empty_polls < 90 )); do
  lease_response="$(api POST "/pools/${pool_id}/leases" '{"lease_ttl_seconds":120}')"
  state="$(jq -r .pool.state <<<"${lease_response}")"

  if [[ "$(jq -r '.lease == null' <<<"${lease_response}")" == "true" ]]; then
    if [[ "${state}" == "consumed" ]]; then
      echo "Pool consumed after ${invocation} Bazel invocations"
      break
    fi

    empty_polls=$((empty_polls + 1))
    sleep 10
    continue
  fi

  empty_polls=0
  invocation=$((invocation + 1))
  lease_id="$(jq -r .lease.id <<<"${lease_response}")"
  attempt_index="$(jq -r '[.lease.attempts[].attempt_index] | unique | if length == 1 then .[0] else error("mixed attempt indexes in one lease") end' <<<"${lease_response}")"
  mapfile -t labels < <(jq -r '.lease.attempts[].selector' <<<"${lease_response}")

  echo "Bazel invocation ${invocation}: attempt_index=${attempt_index}, targets=${#labels[@]}"
  printf '  %s\n' "${labels[@]}"

  bep="bep-${invocation}.json"
  bazel_args=(test --test_output=errors --test_env="DEMO_ATTEMPT_INDEX=${attempt_index}" --build_event_json_file="${bep}")
  if (( attempt_index > 0 )); then
    bazel_args+=(--nocache_test_results)
    echo "Retry invocation: --nocache_test_results enabled"
  fi
  ./bazelisk "${bazel_args[@]}" "${labels[@]}" || true

  completions='[]'
  while IFS= read -r attempt; do
    attempt_id="$(jq -r .id <<<"${attempt}")"
    selector="$(jq -r .selector <<<"${attempt}")"
    status="$(jq -r --arg selector "${selector}" 'select(.id.testResult.label == $selector) | .testResult.status' "${bep}" | tail -1)"

    if [[ "${status}" == "PASSED" ]]; then
      result="passed"
    else
      result="failed"
    fi

    echo "  complete ${selector}: bazel=${status:-missing} scheduler=${result}"
    completions="$(jq -c --arg id "${attempt_id}" --arg result "${result}" '. + [{attempt_id:$id,result:$result}]' <<<"${completions}")"
  done < <(jq -c '.lease.attempts[]' <<<"${lease_response}")

  completion_body="$(jq -nc --arg lease_id "${lease_id}" --argjson attempts "${completions}" '{leases:[{lease_id:$lease_id,attempts:$attempts}]}')"
  api POST "/pools/${pool_id}/leases/complete" "${completion_body}" >/dev/null

  # Policy evaluation is asynchronous, and empty lease polls are rate limited.
  sleep 10
done

if [[ "${state}" != "consumed" ]]; then
  echo "Pool did not reach consumed state before the poll limit" >&2
  exit 1
fi

metrics="$(api GET "/pools/${pool_id}/metrics")"
echo "Final pool metrics: $(jq -c . <<<"${metrics}")"

if [[ "${invocation}" != "2" ]]; then
  echo "Expected exactly two Bazel invocations (initial + failed-target retry), got ${invocation}" >&2
  exit 1
fi
