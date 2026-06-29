"""Background worker: a Postgres-backed job queue.

The web request or the scheduler creates a pending AuditRun (the enqueue); this
worker claims pending runs one at a time, executes the full audit pipeline, and
emails the PDF report when the run asked for it. No Redis, no process forking —
durable (state lives in the run rows) and cross-platform. Swap for Redis/RQ if
concurrency is ever needed.

Run with:  python -m app.worker
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditRun
from app.models.enums import RunStatus
from app.reporting.email import email_report
from app.runner import execute_run
from app.worker_control import clear_heartbeat, write_heartbeat

POLL_SECONDS = 3
HEARTBEAT_SECONDS = 5


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
        while True:
            write_heartbeat(pid)
            time.sleep(HEARTBEAT_SECONDS)

    threading.Thread(target=beat, daemon=True).start()
    atexit.register(clear_heartbeat)


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


def run_forever(poll_seconds: int = POLL_SECONDS) -> None:
    print("[worker] started; polling for pending runs")
    _start_heartbeat()
    try:
        while True:
            if not process_once():
                time.sleep(poll_seconds)
    finally:
        clear_heartbeat()


if __name__ == "__main__":
    run_forever()
