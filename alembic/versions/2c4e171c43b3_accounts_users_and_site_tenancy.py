"""accounts users and site tenancy

Revision ID: 2c4e171c43b3
Revises: a1878f8458de
Create Date: 2026-06-30 04:45:51.380981

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '2c4e171c43b3'
down_revision: str | None = 'a1878f8458de'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("plan_tier", sa.String(length=50), server_default="internal", nullable=False),
        sa.Column("concurrency_limit", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=30), server_default="owner", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_account_id", "users", ["account_id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # Add the site owner, nullable for now so existing rows can be backfilled.
    op.add_column("sites", sa.Column("account_id", sa.Uuid(), nullable=True))

    # Bootstrap a default account and owner, then assign every existing site to it.
    # gen_random_uuid() is built in to PostgreSQL 13+.
    op.execute(
        """
        INSERT INTO accounts (id, name, plan_tier, concurrency_limit, created_at, updated_at)
        VALUES (gen_random_uuid(), 'Goyande', 'internal', 1, now(), now())
        """
    )
    op.execute(
        """
        INSERT INTO users (id, account_id, email, role, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), a.id, 'peter.graves@pggi.co.uk', 'owner', true, now(), now()
        FROM accounts a
        """
    )
    op.execute(
        "UPDATE sites SET account_id = (SELECT id FROM accounts ORDER BY created_at LIMIT 1)"
    )

    op.alter_column("sites", "account_id", nullable=False)
    op.create_index("ix_sites_account_id", "sites", ["account_id"])
    op.create_foreign_key(
        "fk_sites_account_id", "sites", "accounts", ["account_id"], ["id"], ondelete="CASCADE"
    )

    # Domain was globally unique. Make it unique per account instead, keeping a plain
    # index on domain for lookups.
    op.drop_index("ix_sites_domain", table_name="sites")
    op.create_index("ix_sites_domain", "sites", ["domain"])
    op.create_unique_constraint("uq_sites_account_domain", "sites", ["account_id", "domain"])


def downgrade() -> None:
    op.drop_constraint("uq_sites_account_domain", "sites", type_="unique")
    op.drop_index("ix_sites_domain", table_name="sites")
    op.create_index("ix_sites_domain", "sites", ["domain"], unique=True)
    op.drop_constraint("fk_sites_account_id", "sites", type_="foreignkey")
    op.drop_index("ix_sites_account_id", table_name="sites")
    op.drop_column("sites", "account_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_account_id", table_name="users")
    op.drop_table("users")
    op.drop_table("accounts")
