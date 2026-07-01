"""The auth dependencies: session reading and role gating (no DB needed)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import SESSION_USER_KEY, current_user, require_admin, require_user


def _request(session_data: dict) -> SimpleNamespace:
    return SimpleNamespace(session=session_data)


class _FakeSession:
    """Minimal stand-in for a SQLAlchemy Session with just .get."""

    def __init__(self, users: dict):
        self._users = users

    def get(self, _model, key):
        return self._users.get(key)


def _user(role="owner", active=True):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=active)


def test_current_user_none_when_no_session():
    assert current_user(_request({}), _FakeSession({})) is None


def test_current_user_none_on_bad_uuid():
    assert current_user(_request({SESSION_USER_KEY: "not-a-uuid"}), _FakeSession({})) is None


def test_current_user_loads_active_user():
    u = _user()
    req = _request({SESSION_USER_KEY: str(u.id)})
    assert current_user(req, _FakeSession({u.id: u})) is u


def test_current_user_none_for_inactive_user():
    u = _user(active=False)
    req = _request({SESSION_USER_KEY: str(u.id)})
    assert current_user(req, _FakeSession({u.id: u})) is None


def test_require_user_redirects_when_absent():
    with pytest.raises(HTTPException) as exc:
        require_user(user=None)
    assert exc.value.status_code == 303
    assert exc.value.headers["Location"] == "/login"


def test_require_user_returns_signed_in_user():
    u = _user()
    assert require_user(user=u) is u


def test_require_admin_allows_owner_and_admin():
    for role in ("owner", "admin"):
        u = _user(role=role)
        assert require_admin(user=u) is u


def test_require_admin_forbids_other_roles():
    with pytest.raises(HTTPException) as exc:
        require_admin(user=_user(role="member"))
    assert exc.value.status_code == 403
