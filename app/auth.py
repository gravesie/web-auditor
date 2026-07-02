"""Session-based authentication for the web app.

Login state lives in a signed session cookie (Starlette SessionMiddleware), holding
only the user id. current_user reads it back; require_user gates a route and redirects
to /login when there's no session; require_admin further restricts to Goyande staff.

This is the login wall the tenancy seam was built for: get_current_account now derives
the account from the logged-in user (see app/tenancy.py). Customer magic-link access is
added later with the onboarding funnel; this covers email + password sign-in for staff.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN

from app.db import get_session
from app.models import User

SESSION_USER_KEY = "user_id"
# Roles that may see the admin dashboard and use the admin controls.
ADMIN_ROLES = {"owner", "admin"}


def login_session(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = str(user.id)
    # Email and role are stored for display only (header link, greeting). Authorisation
    # always re-reads the user from the database via current_user, never the session.
    request.session["email"] = user.email
    request.session["role"] = user.role


def clear_session(request: Request) -> None:
    request.session.clear()


def current_user(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    """The signed-in user, or None. Never raises; a stale or bad session reads as None."""
    raw = request.session.get(SESSION_USER_KEY)
    if not raw:
        return None
    try:
        user_id = uuid.UUID(raw)
    except (ValueError, TypeError):
        return None
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    """Gate a route to signed-in users; redirect to /login otherwise."""
    if user is None:
        raise HTTPException(
            status_code=HTTP_303_SEE_OTHER, detail="login required", headers={"Location": "/login"}
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    """Gate a route to Goyande staff."""
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Admins only")
    return user
