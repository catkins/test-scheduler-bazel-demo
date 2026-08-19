# Test Scheduler Bazel demo

This repository is a small end-to-end example of using Buildkite Test
Scheduler to orchestrate Bazel test targets.

The pipeline:

1. creates 1,000 distinct Bazel test targets and uploads one Test Scheduler
   pool entry per target, in API-sized batches of 100;
2. assigns 900 targets to a five-run default policy and 100 new targets to a
   ten-run qualification policy;
3. uses one Buildkite job to acquire and heartbeat every 300-attempt lease
   needed to hold all 5,500 initial attempts;
4. sends one cacheable, 1,000-target Bazel invocation with policy-specific
   `--runs_per_test` values, using `--jobs=5500` to submit all 5,500 test-action
   executions to Bazel Remote Execution together;
5. reports every initial Build Event Protocol result to Test Scheduler, then leases
   policy-generated retries in separate Bazel invocations with
   `--nocache_test_results`; and
6. verifies the pool is consumed with the expected entry, attempt, and result
   metrics.

The pipeline uses the Buildkite mise plugin to install Bazelisk, Python, and
uv. It also installs Zig to satisfy the optional C++ toolchain resolution that
`rules_python` performs during analysis; these pure-Python tests do not compile
C++. Each Bazel target runs a distinct pytest test with a hermetic Python
toolchain and dependencies managed by `rules_python`. Each test sleeps for 10
ms. One hundred default-policy targets intentionally fail their first initial
attempt and pass their other four, so the five-pass policy gives each one a
retry. The 100 qualification targets must pass ten out of ten executions. This
produces 5,500 initial attempts and 100 policy-generated retries while keeping
the workload deterministic. The coordinator reports the initial leases in
API-sized batches after the full-target Bazel invocation finishes. If its agent
is lost, Buildkite retries the job and expired leases return to the pool.

Test Engine and Test Scheduler both authenticate with the same short-lived
Buildkite Agent OIDC token. Scheduler requests go through the small `httpx`
client in `.buildkite/test_scheduler_client.py`; the demo does not use bktec.
All pipeline orchestration and Bazel test launchers are Python. Pipeline steps
run mise tasks, which invoke them through `uv run --frozen python ...` in the
mise-managed tool environment.

The execution steps read `BUILDBUDDY_API_KEY` from a Buildkite cluster secret
and use BuildBuddy's remote execution and cache endpoints. The Bazel actions do
not receive Buildkite or BuildBuddy credentials: the Buildkite coordinator
holds the scheduler leases, sends actions through Bazel, parses the resulting
BEP file, and reports completions. This models customer's EngFlow integration:
a setup job populates and seals the pool, one coordinator sends the full target
set to Bazel Remote Execution, and five simple retry consumers drain only
policy-generated failures.

After changing Python dependencies, run `mise run lock-bazel` to export the
committed uv lock into the hashed requirements file consumed by
`rules_python`. The pipeline never resolves dependencies during a test action.

## Buildkite resources

- [Pipeline](https://buildkite.com/catkins-test/test-scheduler-bazel-demo)
- [Test Engine suite](https://buildkite.com/organizations/catkins-test/analytics/suites/test-scheduler-bazel-demo)
