"""Runtime settings, read from environment variables (and .env in development).

Secrets never get defaults. Model IDs default to the plan's split: Sonnet for intake,
root cause and triage; Haiku for dedup, briefing and follow-up.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://campus:campus@localhost:5432/campus"
    test_database_url: str = "postgresql+psycopg://campus:campus@localhost:5432/campus_test"

    anthropic_api_key: SecretStr | None = None
    intake_model: str = SONNET
    rootcause_model: str = SONNET
    triage_model: str = SONNET
    dedup_model: str = HAIKU
    briefing_model: str = HAIKU
    followup_model: str = HAIKU

    voyage_api_key: SecretStr | None = None
    socrata_app_token: SecretStr | None = None

    # Ed25519 private key for receipt signing, PEM (PKCS#8). Public keys are committed.
    ice_signing_key: SecretStr | None = None
    ice_public_key_id: str = "campustrace-dev-1"

    # Sent in the User-Agent of every request to a data portal or the weather API.
    contact_email: str | None = None

    campus_data_dir: str = Field(default="campus_data")
    campus_timezone: str = "America/Los_Angeles"


@lru_cache
def get_settings() -> Settings:
    return Settings()
