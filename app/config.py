"""Application settings, loaded from environment / .env.

Everything configurable lives here so nothing reads os.environ directly.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"

    # SQLAlchemy URL. The psycopg (v3) driver is used.
    database_url: str = (
        "postgresql+psycopg://webauditor:webauditor@localhost:5432/webauditor"
    )

    # Used to encrypt stored connector credentials. Must be overridden outside dev.
    secret_key: str = "change-me-in-production"

    # Google PageSpeed Insights API key (performance audit). Optional: PSI works
    # keyless at a lower quota.
    pagespeed_api_key: str | None = None

    # Emailed PDF report. Sent via Resend when resend_api_key is set; otherwise the
    # email is written to the outbox as a .eml file (PDF attached) to send by hand.
    # A send failure also falls back to the outbox.
    resend_api_key: str | None = None
    # From must be on a Resend-verified domain. updates.pggi.co.uk is verified
    # (DKIM + SPF + DMARC), so reports send from there. The subdomain is send-only;
    # set a Reply-To to a monitored inbox if replies are wanted later.
    email_from: str = "Goyande AI <audits@updates.pggi.co.uk>"
    email_to: str = "admin@pggi.co.uk"
    outbox_dir: str = "outbox"

    # Public base URL, used to build absolute links in emails (magic sign-in links,
    # report links). Override for any non-local deployment.
    base_url: str = "http://127.0.0.1:8000"

    # Headline on the public landing page. Editable here (an admin edit screen is a
    # planned follow-up).
    landing_cta: str = "Audit your website. GEO, SEO, and everything you need to know."

    # Anthropic API key for the LLM judgement passes (GEO answerability, and content
    # quality later). Optional: those checks degrade to needs-connection without it.
    anthropic_api_key: str | None = None
    # Model for the LLM passes. Opus 4.8 by default; override to trade cost/latency.
    llm_model: str = "claude-opus-4-8"

    # Google OAuth client (Search Console + GA4 connectors). The client id/secret come
    # from a Google Cloud OAuth 2.0 client; without them the connect flow is disabled
    # and every audit keeps running its public/inferred path.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    # Must exactly match an "Authorised redirect URI" on the OAuth client in Google
    # Cloud. Override if the app is not served on localhost:8000.
    google_oauth_redirect_uri: str = "http://localhost:8000/connections/google/callback"


settings = Settings()
