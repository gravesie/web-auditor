"""Enqueue an audit for every site — for recurring monitoring.

    python -m app.scheduler

Creates a pending run (emailing the report on completion) for each site; the worker
picks them up. Drive it on a cadence with cron or Windows Task Scheduler.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Site
from app.runner import create_pending_run


def enqueue_all(email: bool = True) -> int:
    """Queue a scheduled run for every site. Returns the number queued."""
    session = SessionLocal()
    try:
        domains = [s.domain for s in session.execute(select(Site)).scalars().all()]
    finally:
        session.close()
    for domain in domains:
        create_pending_run(domain, email=email, scheduled=True)
    return len(domains)


if __name__ == "__main__":
    count = enqueue_all()
    print(f"enqueued {count} site(s)")
