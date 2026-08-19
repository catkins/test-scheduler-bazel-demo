# Test Scheduler Bazel demo

This repository is a small end-to-end example of using Buildkite Test
Scheduler to orchestrate Bazel test targets.

The pipeline:

1. creates 1,000 distinct Bazel test targets and uploads one Test Scheduler
   pool entry per target, in API-sized batches of 100;
2. runs exactly two parallel Buildkite scheduler jobs, each leasing at most 300
   targets at a time;
3. reports each target's Build Event Protocol result to Test Scheduler;
4. leases policy-generated retries and runs them in separate Bazel invocations
   with `--nocache_test_results`; and
5. verifies the pool is consumed with the expected entry, attempt, and result
   metrics.

Each test sleeps for 10 ms. Every tenth target intentionally fails its initial
attempt and passes its retry. This produces 1,000 initial attempts and 100
policy-generated retries, while keeping the workload deterministic.

The example uses local Bazel execution. It models the orchestration intended
for customer's Bazel remote execution integration: a setup job populates and
seals the pool, two simple consumers drain it, and failed targets return as
separate retry invocations. It does not configure EngFlow or a remote cache.

## Buildkite resources

- [Pipeline](https://buildkite.com/catkins-test/test-scheduler-bazel-demo)
- [Test Engine suite](https://buildkite.com/organizations/catkins-test/analytics/suites/test-scheduler-bazel-demo)
