"""The account-scoping guard. owned_site is what stops one account reaching another's
sites, so its account_id check is verified here without needing a database."""

import uuid

from app import tenancy


class _Site:
    def __init__(self, account_id):
        self.account_id = account_id


class _Session:
    """Minimal stand-in: get() returns whatever site it was given."""

    def __init__(self, site):
        self._site = site

    def get(self, _model, _site_id):
        return self._site


ACCOUNT_A = uuid.uuid4()
ACCOUNT_B = uuid.uuid4()
SITE_ID = uuid.uuid4()


def test_owned_site_returns_site_for_its_own_account():
    session = _Session(_Site(ACCOUNT_A))
    assert tenancy.owned_site(session, SITE_ID, ACCOUNT_A) is not None


def test_owned_site_rejects_a_different_account():
    session = _Session(_Site(ACCOUNT_A))
    assert tenancy.owned_site(session, SITE_ID, ACCOUNT_B) is None


def test_owned_site_handles_missing_site():
    session = _Session(None)
    assert tenancy.owned_site(session, SITE_ID, ACCOUNT_A) is None
