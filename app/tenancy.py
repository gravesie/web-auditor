"""The account-scoping seam for the web app.

Every request runs in the context of one account, and every site query is filtered
by it, so a user only ever sees their own account's data. There is no login yet, so
get_current_account returns a single bootstrapped default account. This module is the
one place real authentication slots in later: get_current_account will read the
logged-in user's account from the session instead of returning the default, and
nothing else in the app needs to change.
"""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account, Site, User

DEFAULT_ACCOUNT_NAME = "Goyande"
DEFAULT_OWNER_EMAIL = "peter.graves@pggi.co.uk"


def get_or_create_default_account(session: Session) -> Account:
    """The default account, created with an owner user if the table is empty.

    Post-migration there is always exactly one, so this is a plain read. The create
    branch is a safety net for a fresh database reached outside the migration.
    """
    account = session.execute(select(Account).order_by(Account.created_at)).scalars().first()
    if account is None:
        account = Account(name=DEFAULT_ACCOUNT_NAME)
        session.add(account)
        session.flush()
        session.add(User(account_id=account.id, email=DEFAULT_OWNER_EMAIL, role="owner"))
        session.commit()
    return account


def get_current_account(session: Session = Depends(get_session)) -> Account:
    """The account for the current request. Always the default until login exists."""
    return get_or_create_default_account(session)


def owned_site(session: Session, site_id: uuid.UUID, account_id: uuid.UUID) -> Site | None:
    """Return the site only if it belongs to this account, else None (treat as 404)."""
    site = session.get(Site, site_id)
    if site is None or site.account_id != account_id:
        return None
    return site
