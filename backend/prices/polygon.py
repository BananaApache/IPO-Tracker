"""Daily bars from Polygon.

Licensed, keyed, rate-limited to the free tier's 5 requests/minute. Chosen over
Yahoo's chart endpoint specifically because Yahoo grants no permission for this
use -- see docs/sources.md for the four sources rejected on that basis.

One request returns the whole date range for a ticker, so a 90-day window plus
the history needed to confirm a first listing costs one call.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from backend.config import Settings
from backend.http import RetryingClient

logger = logging.getLogger(__name__)

_AGGS = "https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class PolygonClient:
    source = "polygon"

    def __init__(self, settings: Settings, client: RetryingClient | None = None) -> None:
        if not settings.market_data_api_key:
            raise RuntimeError("MARKET_DATA_API_KEY is not set")
        self._key = settings.market_data_api_key
        self._client = client or RetryingClient(
            user_agent=settings.sec_user_agent,
            per_second=settings.market_data_rate_limit_per_second,
            max_retries=4,
            base_backoff=15.0,   # free tier answers 429 with a hard minute
            max_backoff=90.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        """Bars in [start, end]. Empty list means the provider has no coverage.

        An empty result is returned rather than raised: "this ticker has no data"
        is a fact the caller records as `no_price_data`, not an error.
        """
        payload = await self._client.get_json(
            _AGGS.format(ticker=ticker.upper(), start=start.isoformat(), end=end.isoformat()),
            params={"adjusted": "false", "sort": "asc", "limit": 50000, "apiKey": self._key},
        )
        # adjusted=false on purpose: an adjusted series changes retroactively on
        # a corporate action, which would make a published return irreproducible.
        results = payload.get("results") or []
        bars = []
        for row in results:
            try:
                bars.append(
                    Bar(
                        # Polygon timestamps are epoch ms at midnight ET for the
                        # session; take the UTC date of the session open.
                        day=datetime.fromtimestamp(row["t"] / 1000, tz=UTC).date(),
                        open=row["o"], high=row["h"], low=row["l"],
                        close=row["c"], volume=int(row.get("v") or 0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return bars
