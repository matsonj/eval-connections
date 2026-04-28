"""Tests for the AIMD rate limiter."""

import threading
import time

import pytest

from connections_eval.utils.rate_limiter import RateLimiter, _Policy


def test_acquire_paces_calls_at_configured_rps():
    rl = RateLimiter(default_policy=_Policy(rps=10.0, burst=1.0, concurrency=4))
    model = "test/model"
    # First acquire is immediate (burst=1).
    t0 = time.monotonic()
    rl.acquire(model)
    rl.release(model)
    # Second acquire should wait ~0.1s for token refill (rps=10).
    rl.acquire(model)
    rl.release(model)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.08, f"expected pacing delay, got {elapsed:.3f}s"


def test_on_429_halves_rps_and_floors_at_min():
    rl = RateLimiter(default_policy=_Policy(rps=8.0, rps_min=1.0, concurrency=4))
    model = "test/model"
    rl.acquire(model); rl.release(model)
    assert rl.snapshot(model)["rps"] == pytest.approx(8.0)
    rl.on_429(model)
    assert rl.snapshot(model)["rps"] == pytest.approx(4.0)
    rl.on_429(model)
    assert rl.snapshot(model)["rps"] == pytest.approx(2.0)
    rl.on_429(model)
    assert rl.snapshot(model)["rps"] == pytest.approx(1.0)
    rl.on_429(model)
    # Should not drop below floor.
    assert rl.snapshot(model)["rps"] == pytest.approx(1.0)


def test_on_success_grows_rps_up_to_max():
    rl = RateLimiter(default_policy=_Policy(rps=2.0, rps_max=5.0, aimd_step=1.0, concurrency=4))
    model = "test/model"
    rl.acquire(model); rl.release(model)
    rl.on_success(model)
    assert rl.snapshot(model)["rps"] == pytest.approx(3.0)
    for _ in range(10):
        rl.on_success(model)
    assert rl.snapshot(model)["rps"] == pytest.approx(5.0)


def test_concurrency_cap_limits_in_flight():
    rl = RateLimiter(default_policy=_Policy(rps=100.0, burst=100.0, concurrency=2))
    model = "test/model"
    in_flight = []
    in_flight_lock = threading.Lock()
    peak = [0]

    def worker():
        rl.acquire(model)
        with in_flight_lock:
            in_flight.append(1)
            peak[0] = max(peak[0], len(in_flight))
        time.sleep(0.05)
        with in_flight_lock:
            in_flight.pop()
        rl.release(model)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert peak[0] <= 2, f"concurrency cap of 2 was exceeded: peak={peak[0]}"


def test_pattern_resolution_picks_free_suffix():
    rl = RateLimiter(default_policy=_Policy(rps=10.0, concurrency=8))
    rl.configure(":free", _Policy(rps=1.0, concurrency=2))
    rl.acquire("poolside/laguna-m.1:free"); rl.release("poolside/laguna-m.1:free")
    rl.acquire("openai/gpt-5"); rl.release("openai/gpt-5")
    assert rl.snapshot("poolside/laguna-m.1:free")["rps"] == pytest.approx(1.0)
    assert rl.snapshot("openai/gpt-5")["rps"] == pytest.approx(10.0)


def test_acquire_release_balanced_under_exception():
    rl = RateLimiter(default_policy=_Policy(rps=100.0, burst=100.0, concurrency=1))
    model = "test/model"
    rl.acquire(model)
    rl.release(model)
    # If acquire/release leaked, this second pair would block forever; bound it with a timer.
    done = threading.Event()

    def go():
        rl.acquire(model)
        rl.release(model)
        done.set()

    t = threading.Thread(target=go)
    t.start()
    t.join(timeout=1.0)
    assert done.is_set(), "rate limiter leaked a permit across acquire/release"
