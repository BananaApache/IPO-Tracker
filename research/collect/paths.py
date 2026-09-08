"""Where the module's files live. One place, so nothing hardcodes a path.

Everything is under `research/data/`. Nothing here writes to Postgres: the
module brief keeps this work off the Neon database entirely, so the outputs are
files and the notebooks read files.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# Raw provider payloads, one file per cache key. Kept so every count in the
# analysis can be recomputed with the network off, which is also what makes a
# provider changing its response shape detectable rather than silent.
RAW = DATA / "raw"
RAW_FINNHUB = RAW / "finnhub_ipo"
RAW_NYT = RAW / "nyt"
RAW_TWITTER = RAW / "twitter"
RAW_EDGAR = RAW / "edgar"
RAW_PRICES = RAW / "prices"

FIGURES = DATA / "figures"

SEED_CSV = DATA / "companies_private_to_public_2019_2026.csv"
WATCHLIST_CSV = DATA / "watchlist.csv"

CENSUS_PARQUET = DATA / "census.parquet"
CENSUS_DROPPED_CSV = DATA / "census_dropped.csv"
SOURCE_PROBE_JSON = DATA / "source_probe.json"

NYT_COUNTS_PARQUET = DATA / "nyt_monthly_counts.parquet"
TWITTER_COUNTS_PARQUET = DATA / "twitter_monthly_counts.parquet"
PRICES_PARQUET = DATA / "prices_daily.parquet"


def ensure_dirs() -> None:
    for d in (RAW_FINNHUB, RAW_NYT, RAW_TWITTER, RAW_EDGAR, RAW_PRICES, FIGURES):
        d.mkdir(parents=True, exist_ok=True)
