"""Liveness tracking, single-instance locking, and on-demand start for the worker.

The worker is a separate process (so a multi-minute audit never blocks the web
server). Three mechanisms keep exactly one healthy worker alive:

* Single-instance lock — an OS file lock the worker holds for its whole life.
  Only one process can hold it, and the OS releases it automatically when that
  process dies (even on a hard kill), so a crashed worker never blocks the next
  one and two workers can never run at once.
* Heartbeat — a small JSON file (pid + timestamp) the worker refreshes every few
  seconds. "Alive" means a fresh timestamp *and* a pid that is actually running.
  ensure_worker() uses it as a cheap pre-check before spawning.
* ensure_worker() — the web app calls this to start a worker on demand only when
  none is alive.

Heartbeat and lock files live in the project root (both gitignored).
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Project root (this file lives in app/). The heartbeat, lock and log sit here.
ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_FILE = ROOT / ".worker_state.json"
LOCK_FILE = ROOT / ".worker.lock"
WORKER_LOG = ROOT / "worker.log"

# A heartbeat older than this means no worker is alive. Must comfortably exceed the
# worker's heartbeat interval so a busy worker is never declared dead.
STALE_AFTER_SECONDS = 15.0

# Holds the OS lock for this process's lifetime once acquired. Module-level so the
# handle is not garbage-collected (which would release the lock).
_lock_handle = None


def _pid_alive(pid: int | None) -> bool:
    """True if a process with this pid is currently running."""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        # PROCESS_QUERY_LIMITED_INFORMATION; OpenProcess returns NULL once the pid
        # has fully exited and been reaped.
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def write_heartbeat(pid: int) -> None:
    """Record that the worker (pid) is alive right now."""
    payload = {"pid": pid, "ts": time.time()}
    try:
        HEARTBEAT_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        # A failed heartbeat is not fatal; the worst case is a spurious extra worker.
        pass


def _read_heartbeat() -> dict | None:
    """Parsed heartbeat, or None if missing/corrupt/without a usable timestamp."""
    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data.get("ts"), (int, float)):
        return None
    return data


def clear_heartbeat() -> None:
    """Remove the heartbeat — but never wipe a *different* live worker's heartbeat.

    Deletes only when the recorded pid is our own or is no longer running, so a
    worker exiting cannot blank out another live worker, yet a stale file from a
    dead process still gets cleaned up.
    """
    data = _read_heartbeat()
    if data is not None:
        pid = data.get("pid")
        if pid not in (None, os.getpid()) and _pid_alive(pid):
            return
    try:
        HEARTBEAT_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def worker_status() -> dict:
    """Return {running: bool, pid: int | None, age_seconds: float | None}.

    Running requires both a fresh heartbeat and a pid that is actually alive, so a
    hard-killed worker is reported stopped immediately rather than after the
    staleness window.
    """
    data = _read_heartbeat()
    if data is None:
        return {"running": False, "pid": None, "age_seconds": None}
    pid = data.get("pid")
    age = time.time() - data["ts"]
    running = age <= STALE_AFTER_SECONDS and _pid_alive(pid)
    return {"running": running, "pid": pid, "age_seconds": age}


def acquire_single_instance() -> bool:
    """Become the one and only worker. Returns False if another already holds it.

    The lock is an OS file lock released automatically when this process dies, so
    a crashed worker never blocks its successor.
    """
    global _lock_handle
    try:
        handle = open(LOCK_FILE, "a+")  # noqa: SIM115 (held for the process lifetime)
    except OSError:
        # Cannot open the lock file; degrade to allowing the worker rather than
        # blocking all audits. The heartbeat pre-check still limits double spawns.
        return True
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _lock_handle = handle
    return True


def release_single_instance() -> None:
    """Release the single-instance lock if we hold it."""
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        _lock_handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        _lock_handle.close()
    except OSError:
        pass
    _lock_handle = None


def ensure_worker() -> bool:
    """Start a detached worker if none is alive. Returns True if one was started."""
    if worker_status()["running"]:
        return False
    pid = _spawn_worker()
    # Claim liveness immediately so a near-simultaneous call doesn't spawn a second.
    # The single-instance lock is the hard guarantee; this just trims wasted spawns.
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
