"""Worker liveness + auto-start gating. Heartbeat path is redirected to a tmp file
so these never touch (or disturb) a real running worker's heartbeat."""

import json
import time

import pytest

from app import worker_control as wc


@pytest.fixture(autouse=True)
def _tmp_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "HEARTBEAT_FILE", tmp_path / ".worker_state.json")


def test_status_not_running_without_heartbeat():
    assert wc.worker_status()["running"] is False


def test_write_then_status_running():
    wc.write_heartbeat(1234)
    status = wc.worker_status()
    assert status["running"] is True
    assert status["pid"] == 1234
    assert status["age_seconds"] < wc.STALE_AFTER_SECONDS


def test_clear_heartbeat_flips_to_not_running():
    wc.write_heartbeat(1234)
    wc.clear_heartbeat()
    assert wc.worker_status()["running"] is False


def test_stale_heartbeat_is_not_running():
    wc.HEARTBEAT_FILE.write_text(
        json.dumps({"pid": 1234, "ts": time.time() - (wc.STALE_AFTER_SECONDS + 5)}),
        encoding="utf-8",
    )
    assert wc.worker_status()["running"] is False


def test_corrupt_heartbeat_is_not_running():
    wc.HEARTBEAT_FILE.write_text("not json", encoding="utf-8")
    assert wc.worker_status()["running"] is False


def test_ensure_worker_skips_spawn_when_alive(monkeypatch):
    spawned = []
    monkeypatch.setattr(wc, "_spawn_worker", lambda: spawned.append(True))
    wc.write_heartbeat(1234)
    assert wc.ensure_worker() is False
    assert spawned == []


def test_ensure_worker_spawns_when_dead(monkeypatch):
    monkeypatch.setattr(wc, "_spawn_worker", lambda: 4321)
    assert wc.ensure_worker() is True
    # It claims liveness immediately so a second call does not spawn again.
    status = wc.worker_status()
    assert status["running"] is True
    assert status["pid"] == 4321
