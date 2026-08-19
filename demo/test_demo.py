import os
import time


def _test_target(index: int) -> None:
    # Bazel sets TEST_RUN_NUMBER for runs_per_test. Retry consumers set
    # DEMO_ATTEMPT_INDEX because each retry uses a separate Bazel invocation.
    attempt = int(
        os.environ.get(
            "DEMO_ATTEMPT_INDEX", str(int(os.environ.get("TEST_RUN_NUMBER", "1")) - 1)
        )
    )
    time.sleep(0.01)

    # Default targets 0-9 fail attempt zero and pass their retry.
    # All other default targets and qualification targets always pass.
    assert index >= 10 or attempt > 0, (
        f"target {index} intentionally fails scheduler attempt {attempt}"
    )


def _make_test(index: int):
    def test_target() -> None:
        _test_target(index)

    test_target.__name__ = f"test_target_{index}"
    test_target.__qualname__ = test_target.__name__
    return test_target


for _index in range(500):
    globals()[f"test_target_{_index}"] = _make_test(_index)

del _index
