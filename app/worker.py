"""Background worker: a Postgres-backed job queue.

The web request or the scheduler creates a pending AuditRun (the enqueue); this
worker claims pending runs one at a time, executes the full audit pipeline, and
emails the PDF report when the run asked for it. No Redis, no process forking —
durable (state lives in the run rows) and cross-platform. Swap for Redis/RQ if
true parallel concurrency is ever needed.

Lifecycle: on startup the worker takes a single-instance lock (so only one ever
runs), reaps any run left mid-flight by a dead worker, then polls. It shuts down
on SIGINT/SIGTERM or after a spell with nothing to do; the web app re-spawns one
on demand via ensure_worker().

Run with:  python -m app.worker
"""

from __future__ import annotations

import atexit
import os
import signal
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditRun
from app.models.enums import RunStatus
from app.reporting.email import email_report
from app.runner import execute_run, reap_stuck_runs
from app.worker_control import (
    acquire_single_instance,
    clear_heartbeat,
    release_single_instance,
    write_heartbeat,
)

POLL_SECONDS = 3
HEARTBEAT_SECONDS = 5
# Exit after this long with an empty queue. The web app re-spawns on demand, so an
# idle worker need not sit resident holding memory and a DB connection.
IDLE_SHUTDOWN_SECONDS = 120

# Set by the signal handlers; the poll loop checks it to shut down cleanly.
_stop = threading.Event()


def _next_pending() -> tuple[str, bool] | None:
    """Atomically claim the oldest pending run, flipping it to running.

    The row is locked FOR UPDATE SKIP LOCKED so that if two workers ever run at once
    they can never claim the same run. Returns (run_id, email_requested) or None.
    """
    session = SessionLocal()
    try:
        run = session.execute(
            select(AuditRun)
            .where(AuditRun.status == RunStatus.pending)
            .order_by(AuditRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if run is None:
            return None
        # Claim it within the locked transaction; execute_run re-stamps these too.
        run.status = RunStatus.running
        run.started_at = datetime.now(UTC)
        claimed = (str(run.id), run.email_requested)
        session.commit()
        return claimed
    finally:
        session.close()


def _start_heartbeat() -> None:
    """Tick the heartbeat from a daemon thread so a busy worker stays 'alive'."""
    pid = os.getpid()
    write_heartbeat(pid)

    def beat() -> None:
        while not _stop.is_set():
            write_heartbeat(pid)
            _stop.wait(HEARTBEAT_SECONDS)

    threading.Thread(target=beat, daemon=True).start()
    atexit.register(clear_heartbeat)


def _install_signal_handlers() -> None:
    """Ask the poll loop to stop on interrupt / terminate signals."""

    def handler(signum, frame) -> None:  # noqa: ANN001, ARG001
        _stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # signal.signal only works in the main thread / on supported signals.
            pass


def process_once() -> bool:
    """Execute one pending run if present. Returns True if a run was processed."""
    pending = _next_pending()
    if pending is None:
        return False
    run_id, email = pending
    try:
        execute_run(run_id)
        if email:
            email_report(run_id)
    except Exception as exc:  # noqa: BLE001 -- one bad run must not kill the worker
        print(f"[worker] run {run_id} failed: {exc}")
    return True


def run_forever(
    poll_seconds: int = POLL_SECONDS, idle_shutdown: float = IDLE_SHUTDOWN_SECONDS
) -> None:
    if not acquire_single_instance():
        print("[worker] another worker already holds the lock; exiting")
        return
    print("[worker] started; polling for pending runs")
    _install_signal_handlers()
    _start_heartbeat()

    # We hold the single-instance lock, so any run still marked 'running' was
    # orphaned by a worker that died. Fail them so they don't hang forever.
    reaped = reap_stuck_runs()
    if reaped:
        print(f"[worker] reaped {reaped} stuck run(s) left by a dead worker")

    last_active = time.monotonic()
    try:
        while not _stop.is_set():
            if process_once():
                last_active = time.monotonic()  # drain the queue back-to-back
            elif idle_shutdown and (time.monotonic() - last_active) >= idle_shutdown:
                print(f"[worker] idle for {idle_shutdown:.0f}s; shutting down")
                break
            else:
                _stop.wait(poll_seconds)
    finally:
        clear_heartbeat()
        release_single_instance()
        print("[worker] stopped")


if __name__ == "__main__":
    run_forever()
