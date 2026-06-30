"""Tenancy: accounts own sites, users belong to an account.

This is the foundation for the multi-user SaaS model (architecture doc section 13).
It is deliberately plumbing only: the tables and relationships exist and every site
is owned by an account, but there is no login wall yet. A single default account is
bootstrapped and selected for every request until real authentication is added.

The user carries an email and a password hash so the model is ready for an email and
password login, but the hashing itself is added with the login wall, not here, so the
default user has no usable password until then.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin


class Account(IdMixin, TimestampMixin, Base):
    """A tenant: the billing unit that owns sites and holds the concurrency quota."""

    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(String(255))
    plan_tier: Mapped[str] = mapped_column(
        String(50), default="internal", server_default="internal"
    )
    # How many audits this account may run at once. Enforced once the worker becomes a
    # pool (architecture doc section 13.3); stored now so the model is ready.
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    users: Mapped[list["User"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    sites: Mapped[list["Site"]] = relationship(  # noqa: F821 (resolved via registry)
        back_populates="account", cascade="all, delete-orphan"
    )


class User(IdMixin, TimestampMixin, Base):
    """A person who signs in. Belongs to exactly one account."""

    __tablename__ = "users"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # Set when the login wall is added; null means this user cannot sign in yet.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="owner", server_default="owner")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    account: Mapped["Account"] = relationship(back_populates="users")
