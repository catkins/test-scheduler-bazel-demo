# Test Scheduler Bazel demo

This repository is a small end-to-end example of using Buildkite Test
Scheduler to orchestrate Bazel test targets.

The pipeline:

1. creates 1,000 distinct Bazel test targets and uploads one Test Scheduler
   pool entry per target, in API-sized batches of 100;
2. runs exactly two parallel Buildkite scheduler jobs, each leasing at most 300
   targets at a time;
3. reports each target's Build Event Protocol result to Test Scheduler;
4. runs five cacheable initial attempts per target, then leases
   policy-generated retries in separate Bazel invocations with
   `--nocache_test_results`; and
5. verifies the pool is consumed with the expected entry, attempt, and result
   metrics.

The pipeline uses the Buildkite mise plugin to install Bazelisk, Python, and
uv. Each Bazel target runs a distinct pytest test through uv's virtual
environment and uploads its result with `buildkite-test-collector`. Each test
sleeps for 10 ms. Every tenth target intentionally fails its first initial
attempt and passes its other four. The five-pass policy gives those targets one
retry. This produces 5,000 initial attempts and 100 policy-generated retries,
while keeping the workload deterministic.

Test Engine and Test Scheduler both authenticate with the same short-lived
Buildkite Agent OIDC token. Scheduler requests go through the small `httpx`
client in `.buildkite/test_scheduler_client.py`; the demo does not use bktec.
All pipeline orchestration and Bazel test launchers are Python. Pipeline steps
run mise tasks, which invoke them through `uv run --frozen python ...` in the
mise-managed tool environment.

The example uses local Bazel execution. It models the orchestration intended
for customer's Bazel remote execution integration: a setup job populates and
seals the pool, two simple consumers drain it, and failed targets return as
separate retry invocations. It does not configure EngFlow or a remote cache.

## Buildkite resources

- [Pipeline](https://buildkite.com/catkins-test/test-scheduler-bazel-demo)
- [Test Engine suite](https://buildkite.com/organizations/catkins-test/analytics/suites/test-scheduler-bazel-demo)
