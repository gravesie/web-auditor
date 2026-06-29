"""Liveness tracking and on-demand start for the background worker.

The worker is a separate process (so a multi-minute audit never blocks the web
server). To avoid the user having to start it by hand, the web app can ensure one
is running: the worker writes a heartbeat file every few seconds, and ensure_worker()
spawns a detached worker only when no fresh heartbeat exists.

Heartbeat is a small JSON file in the project root (gitignored). It carries the pid
and a timestamp; "alive" means the timestamp is fresh. A dedicated heartbeat thread
in the worker keeps it fresh even while a long audit is executing, so a busy worker
is never mistaken for a dead one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Project root (this file lives in app/). The heartbeat and worker log sit here.
ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_FILE = ROOT / ".worker_state.json"
WORKER_LOG = ROOT / "worker.log"

# A heartbeat older than this means no worker is alive. Must comfortably exceed the
# worker's heartbeat interval so a busy worker is never declared dead.
STALE_AFTER_SECONDS = 15.0


def write_heartbeat(pid: int) -> None:
    """Record that the worker (pid) is alive right now."""
    payload = {"pid": pid, "ts": time.time()}
    try:
        HEARTBEAT_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        # A failed heartbeat is not fatal; the worst case is a spurious extra worker.
        pass


def clear_heartbeat() -> None:
    """Remove the heartbeat so status flips to not-running immediately on stop."""
    try:
        HEARTBEAT_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def worker_status() -> dict:
    """Return {running: bool, pid: int | None, age_seconds: float | None}."""
    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"running": False, "pid": None, "age_seconds": None}

    ts = data.get("ts")
    if not isinstance(ts, (int, float)):
        return {"running": False, "pid": None, "age_seconds": None}

    age = time.time() - ts
    return {
        "running": age <= STALE_AFTER_SECONDS,
        "pid": data.get("pid"),
        "age_seconds": age,
    }


def ensure_worker() -> bool:
    """Start a detached worker if none is alive. Returns True if one was started."""
    if worker_status()["running"]:
        return False
    pid = _spawn_worker()
    # Claim liveness immediately so a near-simultaneous call doesn't spawn a second.
    if pid:
        write_heartbeat(pid)
    return True


def _spawn_worker() -> int | None:
    """Launch `python -m app.worker` detached from the web process. Returns its pid."""
    try:
        log = open(WORKER_LOG, "a", encoding="utf-8")  # noqa: SIM115 (lives with the child)
    except OSError:
        log = subprocess.DEVNULL

    kwargs: dict = {"cwd": str(ROOT), "stdout": log, "stderr": log, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        # DETACHED_PROCESS + new group so the worker outlives the web server / reloads.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen([sys.executable, "-m", "app.worker"], **kwargs)
    except OSError as exc:
        print(f"[worker_control] failed to start worker: {exc}")
        return None
    return proc.pid
