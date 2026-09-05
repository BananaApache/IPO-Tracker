"""Application settings, loaded from the environment (and .env in dev).

pydantic-settings reads each field from an env var of the same name,
case-insensitively, and validates it. A missing or unparseable value fails at
import time with a clear error, rather than at 3am as a TypeError deep in a
query.
"""

from functools import lru_cache
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # The same .env feeds docker compose (POSTGRES_* for the postgres
        # image), so it holds keys this class does not declare. Ignore them
        # instead of erroring.
        extra="ignore",
    )

    # Discrete parts rather than one DATABASE_URL so a single .env can drive
    # both the postgres container and this app. compose overrides only the host.
    postgres_db: str = "ipo"
    postgres_user: str = "ipo"
    postgres_password: str = "ipo"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Pool sizing. min_size connections are opened eagerly at startup so the
    # first request does not pay TCP + TLS + auth latency.
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    # Browser origins allowed to call this API. Only consulted by the CORS
    # middleware, which only affects browser-initiated requests -- see the note
    # in main.py.
    cors_origins: list[str] = ["http://localhost:3000"]

    # SEC requires every automated request to identify a real contact, and
    # 403s anything that does not. Configurable rather than hardcoded so a fork
    # cannot accidentally send someone else's address to sec.gov.
    sec_user_agent: str = "IPOTracker/0.1 (contact-not-configured)"

    # SEC's published ceiling is 10 requests/second. Default sits under it: the
    # limit is enforced per process, and the worker is not the only thing that
    # might be talking to EDGAR.
    sec_rate_limit_per_second: float = 6.0
    sec_max_retries: int = 5
    # How far back each poll re-reads EDGAR's daily indexes. The poller keeps no
    # watermark -- idempotency comes from filings.accession_no being UNIQUE --
    # so this window is what lets it self-heal after downtime.
    sec_lookback_days: int = 7
    sec_poll_interval_minutes: int = 60

    # Salt for mentions.author_hash. Kept out of the database on purpose: with
    # the salt, hashes are reversible for any username you can guess, so
    # database access alone must not be enough to re-identify authors. Changing
    # it orphans every existing hash, which breaks unique-author continuity --
    # treat it as write-once per deployment.
    mention_hash_salt: str = "dev-only-change-me"

    @property
    def database_dsn(self) -> str:
        """asyncpg connection string.

        The password is percent-encoded: an unescaped '@' or '/' in a password
        silently reparses the DSN into the wrong host.
        """
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once per process, not per request."""
    return Settings()
