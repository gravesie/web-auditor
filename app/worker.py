"""Background worker: a Postgres-backed job queue.

The web request or the scheduler creates a pending AuditRun (the enqueue); this
worker claims pending runs one at a time, executes the full audit pipeline, and
emails the PDF report when the run asked for it. No Redis, no process forking —
durable (state lives in the run rows) and cross-platform. Swap for Redis/RQ if
concurrency is ever needed.

Run with:  python -m app.worker
"""

from __future__ import annotations

import time

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditRun
from app.models.enums import RunStatus
from app.reporting.email import email_report
from app.runner import execute_run

POLL_SECONDS = 3


def _next_pending() -> tuple[str, bool] | None:
    """The oldest pending run as (run_id, email_requested), or None if the queue is empty."""
    session = SessionLocal()
    try:
        run = session.execute(
            select(AuditRun)
            .where(AuditRun.status == RunStatus.pending)
            .order_by(AuditRun.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if run is None:
            return None
        return str(run.id), run.email_requested
    finally:
        session.close()


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
    while True:
        if not process_once():
            time.sleep(poll_seconds)


if __name__ == "__main__":
    run_forever()
