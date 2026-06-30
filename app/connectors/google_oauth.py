"""Google OAuth 2.0 web flow (authorisation-code grant).

One consent covers both connectors: the Search Console (`webmasters.readonly`) and
GA4 (`analytics.readonly`) scopes are requested together, yielding a single refresh
token that both clients use. The refresh token is the only long-lived secret; access
tokens are fetched fresh per audit run and never stored.

This module is transport only — building the consent URL, exchanging the code, and
refreshing access tokens over httpx. Persisting the grant and picking which GSC
property / GA4 property to bind lives in the web routes.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings
from app.security import crypto

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 (public URL, not a secret)

# Read-only scopes. Search Console search-analytics + sitemaps, and GA4 reporting.
SCOPES = (
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
)

# How long an outstanding consent (the signed state) stays valid.
STATE_TTL_SECONDS = 600

_TIMEOUT = httpx.Timeout(30.0)


class OAuthError(Exception):
    """A Google OAuth request failed or returned an unexpected payload."""


@dataclass(frozen=True)
class TokenGrant:
    """The result of exchanging an authorisation code."""

    access_token: str
    refresh_token: str | None  # only returned on first consent / with prompt=consent
    expires_in: int
    scope: str


def is_configured() -> bool:
    """True when the OAuth client credentials are present, so the flow can run."""
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def encode_state(site_id: str) -> str:
    """Sign the target site id into an opaque, tamper-proof state value."""
    return crypto.encrypt_json({"site_id": site_id})


def decode_state(state: str) -> str:
    """Recover the site id from a state value, rejecting forged or expired ones."""
    try:
        payload = crypto.decrypt_json_ttl(state, STATE_TTL_SECONDS)
    except crypto.DecryptionError as exc:
        raise OAuthError("invalid or expired OAuth state") from exc
    site_id = payload.get("site_id")
    if not site_id:
        raise OAuthError("OAuth state missing site id")
    return str(site_id)


def authorization_url(state: str) -> str:
    """Build the Google consent URL the browser is redirected to."""
    if not is_configured():
        raise OAuthError("Google OAuth client is not configured")
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # offline + consent prompt so Google returns a refresh token every time.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return str(httpx.URL(AUTH_ENDPOINT, params=params))


def exchange_code(code: str) -> TokenGrant:
    """Exchange an authorisation code for an access + refresh token."""
    data = {
        "code": code,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "grant_type": "authorization_code",
    }
    payload = _post_token(data)
    return TokenGrant(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_in=int(payload.get("expires_in", 0)),
        scope=payload.get("scope", ""),
    )


def refresh_access_token(refresh_token: str) -> str:
    """Exchange a stored refresh token for a fresh access token."""
    data = {
        "refresh_token": refresh_token,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "grant_type": "refresh_token",
    }
    payload = _post_token(data)
    token = payload.get("access_token")
    if not token:
        raise OAuthError("token refresh returned no access token")
    return str(token)


def _post_token(data: dict[str, str | None]) -> dict:
    """POST to the token endpoint and return the JSON, raising OAuthError on failure."""
    try:
        response = httpx.post(TOKEN_ENDPOINT, data=data, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise OAuthError(f"token request failed: {exc}") from exc
    if response.status_code != 200:
        # Google returns {error, error_description} on failure; surface it without
        # leaking the request body (which holds the client secret).
        detail = _safe_error(response)
        raise OAuthError(f"token endpoint returned {response.status_code}: {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise OAuthError("token endpoint returned non-JSON") from exc


def _safe_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body.get("error_description") or body.get("error") or body)
    except ValueError:
        return response.text[:200]
