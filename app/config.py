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


settings = Settings()
