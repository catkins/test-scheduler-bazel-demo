# Test Scheduler Bazel demo

This repository is a small end-to-end example of using Buildkite Test
Scheduler to orchestrate Bazel test targets.

The pipeline:

1. uploads one Test Scheduler pool entry per Bazel target;
2. leases and runs all initial targets in one Bazel invocation;
3. reports each target's Build Event Protocol result to Test Scheduler;
4. leases policy-generated retries and runs them in a separate invocation with
   `--nocache_test_results`; and
5. waits for the pool to reach the `consumed` state.

`//demo:flaky_remote_test` intentionally fails its initial attempt and passes
its retry. This gives the pipeline a stable way to verify that only the failed
target is retried.

The example uses local Bazel execution. It models the orchestration intended
for a Bazel remote execution integration, but it does not configure EngFlow or
a remote cache.

## Buildkite resources

- [Pipeline](https://buildkite.com/catkins-test/test-scheduler-bazel-demo)
- [Test Engine suite](https://buildkite.com/organizations/catkins-test/analytics/suites/test-scheduler-bazel-demo)
