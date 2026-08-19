# Test Scheduler Bazel demo

This repository shows how Buildkite Test Scheduler can control a large Bazel
test workload. The example uses BuildBuddy for Bazel Remote Execution. It
models a large remote-execution workflow.

The demo has 100 Bazel targets. Each target runs one pytest test. Each test
sleeps for 10 ms and then passes or fails.

## Responsibilities

Each system has one main responsibility:

- Buildkite runs the setup, dispatcher, consumer, and verification jobs.
- Test Scheduler stores work and decides when an entry needs another attempt.
- Bazel converts each target run into a test action.
- BuildBuddy executes Bazel actions and stores cached results.
- pytest runs the test function inside each Bazel action.

The Buildkite jobs keep all Test Scheduler credentials. A remote action only
receives the files and environment values that Bazel declares for that action.

## Terms

| Term | Meaning in this demo |
| --- | --- |
| Pool | The complete set of test work for one build. |
| Entry | One Bazel target in the pool. |
| Selector | The Bazel label stored in an entry, such as `//demo:target_42`. |
| Policy | The rules that decide how many attempts an entry needs. |
| Attempt | One planned execution of one entry. |
| Initial attempt | An attempt that Test Scheduler creates when the entry is added. |
| Retry | An attempt that Test Scheduler creates after it evaluates results. |
| Lease | Temporary ownership of one or more attempts by a Buildkite job. |
| Heartbeat | A request that extends a lease while work continues. |

## What the demo shows

The pipeline does these tasks:

1. The setup job creates a Test Scheduler pool.
2. The setup job adds 100 targets to the pool.
3. Each target selects one of two policies.
4. The setup job seals the pool.
5. The initial dispatcher leases all 550 initial attempts.
6. The dispatcher sends all initial attempts to BuildBuddy in one Bazel
   invocation.
7. The dispatcher sends the results to Test Scheduler.
8. Five parallel consumer jobs lease and run retries.
9. The verification job checks the final pool state and counts.

## Policies

The pool has two immutable policies. An immutable policy cannot change after
the pool is created.

| Policy | Targets | Initial attempts per target | Required result | Maximum attempts |
| --- | ---: | ---: | --- | ---: |
| `default` | 90 | 5 | 5 passes | 6 |
| `qualification` | 10 | 10 | 10 passes | 10 |

Ten targets in the `default` group fail their first attempt. They pass
their other initial attempts. Test Scheduler creates one retry for each of
these targets.

All ten targets in the `qualification` group pass. Each target must pass ten
times. This group does not need retries.

The `default` policy creates five attempts immediately. It requires five
passes. It permits six total attempts. It also permits one failure before the
entry stops. An entry with one initial failure therefore gets one retry.

The `qualification` policy creates ten attempts immediately. It requires ten
passes and permits only ten total attempts. Its `max_failed` value is one. One
failure therefore makes the entry fail without a retry.

An entry selects a policy with the `attempt_policy` key. An entry without a
policy key uses the `default` policy. Policy definitions and entry selections
do not change after the pool is created.

The expected totals are:

- 550 initial executions
- 10 retry executions
- 560 completed executions
- 550 passed attempts
- 10 intentional failed attempts

## Pool lifecycle

The setup job creates the pool in the `populating` state. The pool accepts
entries only in this state. The setup job adds all 100 entries in one request.
It then sets `populating` to false. This action seals the pool and makes the
initial attempts available for leases.

The pool is `consuming` while attempts are waiting, leased, or under policy
evaluation. An entry is finished when its selected policy decides that it
passed or failed. The pool becomes `consumed` when all entries are finished. The
pool is drained when no attempt is waiting or leased.

The pool key includes the Buildkite build ID. This key prevents two builds from
using the same pool. The setup job stores the pool ID in Buildkite build
metadata. Later jobs read the ID from that metadata.

## Limits used by the demo

| Setting | Value | Purpose |
| --- | ---: | --- |
| Pool lifetime | 3,600 seconds | Removes abandoned pool work after one hour. |
| Entry custom cost | 1 | Gives each attempt the same lease cost. |
| Lease custom cost | 300 | Limits one lease to 300 units of work. |
| Attempts per lease | 300 | Limits one lease to 300 attempts. |
| Initial lease lifetime | 600 seconds | Gives the dispatcher time to start its heartbeat. |
| Initial heartbeat interval | 60 seconds | Extends all leases while Bazel runs. |
| Retry lease lifetime | 300 seconds | Returns abandoned retry work after five minutes. |
| Completion request limit | 5,000 attempts | Sets the maximum result batch size in the client. |

Each initial attempt costs one unit. One lease can therefore hold at most 300
attempts. The dispatcher normally uses one 300-attempt lease and one
250-attempt lease for the 550 initial attempts.

## Build graph

The setup job and the initial dispatcher run once. The retry step has five
parallel jobs. The verification job starts after all retry jobs finish.

```mermaid
flowchart LR
    setup["Create and seal pool<br/>100 entries"]
    initial["Dispatch initial attempts<br/>1 job · 550 executions"]
    retry1["Retry consumer 1"]
    retry2["Retry consumer 2"]
    retry3["Retry consumer 3"]
    retry4["Retry consumer 4"]
    retry5["Retry consumer 5"]
    verify["Verify pool<br/>state and counts"]

    setup --> initial
    initial --> retry1
    initial --> retry2
    initial --> retry3
    initial --> retry4
    initial --> retry5
    retry1 --> verify
    retry2 --> verify
    retry3 --> verify
    retry4 --> verify
    retry5 --> verify
```

The five retry jobs compete for available work. Test Scheduler can give all
retries to one job. The other jobs continue to poll until the pool is consumed.
This behavior is expected. The extra jobs give spare consumer capacity.

The pipeline defines the `hosted` agent queue once at the top level. It also
defines `BUILDBUDDY_API_KEY` once as a top-level secret. All command steps
inherit these settings.

## Request sequence

```mermaid
sequenceDiagram
    participant S as Setup job
    participant TS as Test Scheduler
    participant D as Initial dispatcher
    participant BB as BuildBuddy
    participant R as Retry consumers
    participant V as Verification job

    S->>TS: Create pool with two policies
    S->>TS: Add 100 entries with policy keys
    S->>TS: Seal pool

    loop Until all 550 initial attempts are held
        D->>TS: Lease up to 300 attempts
        TS-->>D: Return a lease
    end
    D->>+BB: Start 100 targets with 5 or 10 runs per target
    loop Every 60 seconds while Bazel runs
        D->>TS: Heartbeat all leases
    end
    BB-->>-D: Return Build Event Protocol results
    D->>TS: Complete 550 attempts

    TS->>TS: Evaluate each entry policy
    TS->>TS: Create 10 retries

    par Five Buildkite jobs poll the pool
        R->>TS: Lease retries
    end
    R->>BB: Run retry targets without the test-result cache
    BB-->>R: Return retry results
    R->>TS: Complete retry leases

    V->>TS: Get pool metrics
    TS-->>V: Return final state and counts
    V->>V: Check that the pool is consumed and drained
```

## Bazel execution

The dispatcher requests initial leases sequentially. Each request can return up
to 300 attempts. The dispatcher does not start Bazel until it holds all 550
initial attempts. This loop drains only the initial queue. Test Scheduler
creates retries after it receives the initial results.

The dispatcher uses this wait point intentionally. It lets one Bazel invocation
submit the complete initial workload. BuildBuddy can then schedule the actions
across its workers. The lease requests do not run in parallel.

The initial dispatcher uses one Bazel invocation. It uses `--runs_per_test=5`
for the `default` targets. It uses `--runs_per_test=10` for the
`qualification` targets. It sets `--jobs=550` so that Bazel can submit all
initial actions without a small local concurrency limit.

Initial runs use `--cache_test_results=yes`. This setting lets Bazel use cached
results when `--runs_per_test` requests multiple runs. Retry runs use
`--nocache_test_results`. This setting makes each retry execute again.

A cached initial result still produces a Build Event Protocol result. The
dispatcher can therefore complete the matching Test Scheduler attempt whether
BuildBuddy executes the action or returns a cached result. A cold cache still
requires remote execution for all initial actions. A cold cache has no stored
result for the requested action.

The dispatcher reads the Bazel Build Event Protocol file. It matches each
result to a Test Scheduler attempt. The dispatcher sends all 550 initial
results in one completion request.

Bazel numbers runs from one. Test Scheduler numbers attempts from zero. The
dispatcher subtracts one from each Bazel run number before it matches a result.
It stops without completing leases if any expected result is missing.

The completion client keeps all attempts from one lease in the same request.
It can split a larger workload into requests of at most 5,000 attempts. The
current workload needs only one completion request.

The Buildkite job owns the Test Scheduler leases. Remote Bazel actions do not
receive Buildkite credentials or the BuildBuddy API key.

## Retry consumption

The initial dispatcher finishes before the five retry consumers start. Test
Scheduler evaluates the 100 entries after it receives the initial results. It
creates ten retries for the ten entries that have only four passes.

Each consumer waits for a lease. The jobs start with a short stagger so that
they do not all send their first request at the same instant. One consumer can
lease all ten retries. The other consumers keep polling until the pool is
consumed.

A consumer rejects an initial attempt. This check keeps the ownership boundary
clear: the dispatcher owns initial attempts, and consumers own retries. A
consumer groups leased attempts by attempt index because one Bazel invocation
uses one `DEMO_ATTEMPT_INDEX` value.

The consumers poll every ten seconds when no work is available. They stop when
the pool is consumed. They fail after 90 empty or unavailable polls. This gives
policy evaluation and expired leases up to 15 minutes to produce work.

## Failure recovery

The initial dispatcher sends a heartbeat every 60 seconds. This heartbeat
keeps its Test Scheduler leases active while Bazel runs.

Buildkite can retry the dispatcher if its agent stops. If the dispatcher no
longer sends heartbeats, its leases expire. Test Scheduler then makes the
attempts available again.

The dispatcher checks the attempt index in each lease. A restarted dispatcher
releases a lease if it contains policy-generated retries. This prevents the
initial job from taking work that belongs to the retry consumers.

The demo does not reconnect to an existing BuildBuddy invocation. A retried
dispatcher starts Bazel again. Bazel can use cached results when they are
available.

If one retry consumer stops, its lease expires after 300 seconds. Another
consumer can lease that work while it continues to poll. The failed Buildkite
job still makes the parallel step fail unless Buildkite retries that job. If all
consumers stop, the verification job does not start.

## Tools and authentication

The pipeline uses the Buildkite mise plugin. Mise installs these tools:

- Bazelisk
- Python
- uv
- Zig

Zig provides the C and archive tools that `rules_python` resolves during Bazel
analysis. The pytest targets do not compile C or C++ code.

uv creates the Python dependency lock. `rules_python` provides the Python
toolchain and installs the locked packages for Bazel. A test action does not
resolve packages from the network. This makes the Python test environment
repeatable.

The pipeline gets `BUILDBUDDY_API_KEY` from a Buildkite cluster secret. The
Test Scheduler client uses a short-lived Buildkite Agent OpenID Connect (OIDC)
token. The small `httpx` client in `.buildkite/test_scheduler_client.py` sends
Test Scheduler requests. The demo does not use `bktec`.

The OIDC token has a 30-minute lifetime. Its claims identify the organization,
pipeline, build, and job. Test Scheduler uses these claims to limit each
request to the current Buildkite context.

The demo expects these Buildkite resources:

- A pipeline that reads `.buildkite/pipeline.yml` from this repository.
- A Test Engine suite with the slug `test-scheduler-bazel-demo`.
- Test Scheduler access for the organization and pipeline.
- Multiple attempt policies enabled for the organization.
- A cluster secret named `BUILDBUDDY_API_KEY`.

The scheduler scripts must run in a Buildkite job. They call
`buildkite-agent oidc request-token` and use Buildkite build metadata. A local
developer can run the Bazel tests, but cannot run the complete scheduler flow
without equivalent Buildkite job context.

All orchestration scripts are Python files. Mise tasks run them with this
command form:

```text
uv run --frozen python <script>
```

## Important files

| File | Purpose |
| --- | --- |
| `.buildkite/pipeline.yml` | Defines the Buildkite graph. |
| `.buildkite/setup_pool.py` | Creates, fills, and seals the pool. |
| `.buildkite/dispatch_initial.py` | Leases and runs all initial attempts. |
| `.buildkite/consume_pool.py` | Leases and runs policy-generated retries. |
| `.buildkite/verify_pool.py` | Checks the final pool metrics. |
| `.buildkite/test_scheduler_client.py` | Sends authenticated API requests. |
| `demo/test_demo.py` | Defines the 100 pytest tests. |
| `demo/BUILD.bazel` | Defines the 100 Bazel test targets. |

## Update Python dependencies

After you change Python dependencies, run this command:

```shell
mise run lock-bazel
```

This command exports the uv lock data to `requirements_lock.txt`.
`rules_python` uses this file for the Bazel dependency lock.
