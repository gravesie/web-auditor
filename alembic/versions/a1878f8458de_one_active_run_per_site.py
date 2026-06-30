"""one active run per site

Revision ID: a1878f8458de
Revises: cf562560a605
Create Date: 2026-06-29 18:46:43.057884

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a1878f8458de'
down_revision: str | None = 'cf562560a605'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEX_NAME = "uq_audit_runs_active_per_site"


def upgrade() -> None:
    # Collapse any pre-existing duplicate active runs (the defect this index guards
    # against) so the unique index can be built: keep the newest active run per
    # site, fail the rest.
    op.execute(
        """
        UPDATE audit_runs
        SET status = 'failed',
            error_message = 'Superseded: duplicate active run cleaned up.',
            finished_at = COALESCE(finished_at, now())
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY site_id ORDER BY created_at DESC
                ) AS rn
                FROM audit_runs
                WHERE status IN ('pending', 'running')
            ) ranked
            WHERE ranked.rn > 1
        )
        """
    )
    op.create_index(
        INDEX_NAME,
        "audit_runs",
        ["site_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="audit_runs")
