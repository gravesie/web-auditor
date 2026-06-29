"""Worker poll-loop lifecycle: lock-gated startup and idle shutdown.

The DB-touching pieces (process_once, reaper, heartbeat) are stubbed so these run
offline and deterministically.
"""

import time

import pytest

from app import worker


@pytest.fixture(autouse=True)
def _reset_stop():
    worker._stop.clear()
    yield
    worker._stop.clear()


def _stub_lifecycle(monkeypatch, *, acquired=True):
    monkeypatch.setattr(worker, "acquire_single_instance", lambda: acquired)
    monkeypatch.setattr(worker, "release_single_instance", lambda: None)
    monkeypatch.setattr(worker, "_start_heartbeat", lambda: None)
    monkeypatch.setattr(worker, "clear_heartbeat", lambda: None)
    monkeypatch.setattr(worker, "reap_stuck_runs", lambda: 0)
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda: None)


def test_exits_immediately_when_lock_held(monkeypatch):
    _stub_lifecycle(monkeypatch, acquired=False)
    started = []
    monkeypatch.setattr(worker, "_start_heartbeat", lambda: started.append(1))
    worker.run_forever()
    assert started == []  # never got past the lock


def test_idle_shutdown_exits(monkeypatch):
    _stub_lifecycle(monkeypatch)
    monkeypatch.setattr(worker, "process_once", lambda: False)
    start = time.monotonic()
    worker.run_forever(poll_seconds=0, idle_shutdown=0.05)
    assert time.monotonic() - start < 5  # exited, did not block forever


def test_reaper_runs_on_startup(monkeypatch):
    _stub_lifecycle(monkeypatch)
    reaped = []
    monkeypatch.setattr(worker, "reap_stuck_runs", lambda: reaped.append(1) or 0)
    # Stop the loop on the first poll so the test can't hang.
    monkeypatch.setattr(worker, "process_once", lambda: worker._stop.set() or False)
    worker.run_forever(poll_seconds=0, idle_shutdown=0.0)
    assert reaped == [1]  # reaper ran once, at startup before the loop


def test_drains_then_idles(monkeypatch):
    _stub_lifecycle(monkeypatch)
    calls = {"n": 0}

    def process_once() -> bool:
        calls["n"] += 1
        return calls["n"] <= 3  # three runs available, then empty

    monkeypatch.setattr(worker, "process_once", process_once)
    worker.run_forever(poll_seconds=0, idle_shutdown=0.05)
    assert calls["n"] >= 4  # drained the three, then saw empty and idled out
