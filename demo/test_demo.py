import os
import time


def _test_target(index: int) -> None:
    attempt = int(os.environ.get("DEMO_ATTEMPT_INDEX", "0"))
    time.sleep(0.01)

    assert index % 10 != 0 or attempt > 0, (
        f"target {index} intentionally fails scheduler attempt {attempt}"
    )


def _make_test(index: int):
    def test_target() -> None:
        _test_target(index)

    test_target.__name__ = f"test_target_{index}"
    test_target.__qualname__ = test_target.__name__
    return test_target


for _index in range(1000):
    globals()[f"test_target_{_index}"] = _make_test(_index)

del _index
