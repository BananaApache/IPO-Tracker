"""Settings for the research module, separate from `backend.config`.

A separate class rather than new fields on `backend.config.Settings`, because
the deployed API and worker must not gain a reason to require these keys. The
research keys are read only by code under `research/`; a deployment with none of
them set is unaffected.

Env var names here match what is already in `.env` (`NYTAPI_KEY`,
`GNEWSAPI_KEY`, ...). The module brief referred to `NYT_API_KEY` /
`FINNHUB_API_KEY` / `GNEWS_API_KEY`; those names are not what the file uses, so
the file wins and the aliases below accept both.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ResearchSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Finnhub. Same key the deployed pipeline calls `NEWS_API_KEY`; the IPO
    # calendar and the news endpoints are the same account.
    finnhub_key: str = Field(
        default="", validation_alias=AliasChoices("NEWS_API_KEY", "FINNHUB_API_KEY")
    )
    # Published free-tier ceiling is 60/min. Held at 30/min: the census pages
    # month by month over ~7 years, and there is no hurry.
    finnhub_per_second: float = 0.5

    nyt_key: str = Field(
        default="", validation_alias=AliasChoices("NYTAPI_KEY", "NYT_API_KEY")
    )
    # NYT's documented limits are 5/min and 500/day. 12s spacing keeps the
    # per-minute limit satisfied with one request to spare; the daily cap is
    # enforced separately by the collector's own counter, since spacing alone
    # cannot express it.
    nyt_per_second: float = 1.0 / 12.0
    nyt_daily_cap: int = 500

    # Third-party scraper services. Priced per call, so the collectors take an
    # explicit call budget rather than running until done -- see collect/twitter.py.
    twitter_key: str = Field(
        default="", validation_alias=AliasChoices("TWITTERAPI_KEY", "TWITTER_API_KEY")
    )
    twitter_per_second: float = 2.0
    reddit_key: str = Field(
        default="", validation_alias=AliasChoices("REDDITAPI_KEY", "REDDIT_API_KEY")
    )
    reddit_per_second: float = 2.0

    gnews_key: str = Field(
        default="", validation_alias=AliasChoices("GNEWSAPI_KEY", "GNEWS_API_KEY")
    )
    # Measured at roughly one request per 20s before a block; see
    # research/data/source_probe.json.
    gnews_per_second: float = 1.0 / 20.0

    # Prices. Polygon, the licensed feed the deployed pipeline already uses.
    polygon_key: str = Field(
        default="", validation_alias=AliasChoices("MARKET_DATA_API_KEY", "POLYGON_API_KEY")
    )
    polygon_per_second: float = 0.08  # 5/min, the free tier

    # SEC requires a descriptive User-Agent carrying a real contact address and
    # 403s anything else. Reused from the deployed config's variable.
    sec_user_agent: str = Field(
        default="IPOTracker-research/0.1 (contact-not-configured)",
        validation_alias=AliasChoices("SEC_USER_AGENT",),
    )
    sec_per_second: float = 6.0


@lru_cache
def get_research_settings() -> ResearchSettings:
    return ResearchSettings()
