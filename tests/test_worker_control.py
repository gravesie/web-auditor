"""Worker liveness, single-instance locking and auto-start gating.

The heartbeat and lock paths are redirected to tmp files so these never touch (or
disturb) a real running worker. Liveness now checks the pid is actually alive, so
the tests use os.getpid() where they want a 'live' worker and a never-allocated pid
where they want a dead one.
"""

import json
import os
import time

import pytest

from app import worker_control as wc

DEAD_PID = 999_999  # never allocated on this machine


@pytest.fixture(autouse=True)
def _tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "HEARTBEAT_FILE", tmp_path / ".worker_state.json")
    monkeypatch.setattr(wc, "LOCK_FILE", tmp_path / ".worker.lock")


def test_status_not_running_without_heartbeat():
    assert wc.worker_status()["running"] is False


def test_write_then_status_running():
    wc.write_heartbeat(os.getpid())
    status = wc.worker_status()
    assert status["running"] is True
    assert status["pid"] == os.getpid()
    assert status["age_seconds"] < wc.STALE_AFTER_SECONDS


def test_status_not_running_when_pid_is_dead():
    # Fresh timestamp but a dead pid -> the worker is gone, report stopped at once.
    wc.write_heartbeat(DEAD_PID)
    assert wc.worker_status()["running"] is False


def test_clear_heartbeat_flips_to_not_running():
    wc.write_heartbeat(os.getpid())
    wc.clear_heartbeat()
    assert wc.worker_status()["running"] is False


def test_clear_heartbeat_leaves_another_live_workers_file(monkeypatch):
    # A heartbeat owned by a *different* live pid must not be wiped.
    monkeypatch.setattr(wc, "_pid_alive", lambda pid: True)
    wc.write_heartbeat(DEAD_PID)  # stands in for another live worker
    wc.clear_heartbeat()
    assert wc.HEARTBEAT_FILE.exists()


def test_clear_heartbeat_removes_dead_workers_file(monkeypatch):
    monkeypatch.setattr(wc, "_pid_alive", lambda pid: False)
    wc.write_heartbeat(DEAD_PID)
    wc.clear_heartbeat()
    assert not wc.HEARTBEAT_FILE.exists()


def test_stale_heartbeat_is_not_running():
    wc.HEARTBEAT_FILE.write_text(
        json.dumps({"pid": os.getpid(), "ts": time.time() - (wc.STALE_AFTER_SECONDS + 5)}),
        encoding="utf-8",
    )
    assert wc.worker_status()["running"] is False


def test_corrupt_heartbeat_is_not_running():
    wc.HEARTBEAT_FILE.write_text("not json", encoding="utf-8")
    assert wc.worker_status()["running"] is False


def test_pid_alive_self_and_dead():
    assert wc._pid_alive(os.getpid()) is True
    assert wc._pid_alive(DEAD_PID) is False
    assert wc._pid_alive(None) is False
    assert wc._pid_alive(0) is False


def test_single_instance_lock_is_exclusive():
    assert wc.acquire_single_instance() is True
    try:
        # A second, independent attempt on the same file must be blocked.
        assert _external_lock_blocked(wc.LOCK_FILE) is True
    finally:
        wc.release_single_instance()
    # Once released, the file can be locked again.
    assert _external_lock_blocked(wc.LOCK_FILE) is False


def test_ensure_worker_skips_spawn_when_alive(monkeypatch):
    spawned = []
    monkeypatch.setattr(wc, "_spawn_worker", lambda: spawned.append(True))
    wc.write_heartbeat(os.getpid())
    assert wc.ensure_worker() is False
    assert spawned == []


def test_ensure_worker_spawns_when_dead(monkeypatch):
    monkeypatch.setattr(wc, "_spawn_worker", lambda: os.getpid())
    assert wc.ensure_worker() is True
    status = wc.worker_status()
    assert status["running"] is True
    assert status["pid"] == os.getpid()


def _external_lock_blocked(path) -> bool:
    """True if an independent handle cannot lock the file (i.e. it is held)."""
    fh = open(path, "a+")
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()
