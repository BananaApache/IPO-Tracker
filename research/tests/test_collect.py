"""Unit tests for the research module's pure functions.

Only the parts with no I/O. The collectors themselves are exercised by running
them, and their correctness rests on the cached payloads under
`research/data/raw/`, not on a mock of a provider whose real behaviour is the
thing this module had to measure.

No database, so these run anywhere -- unlike the rest of the suite, which
conftest deliberately refuses to run against a non-local database.
"""

from datetime import date, datetime

from research.collect.aliases import query_for
from research.collect.edgar_enrich import MONTHS
from research.collect.finnhub_census import _clean_price, month_windows
from research.collect.nyt import add_months, month_end, month_starts, slug
from research.collect.twitter import month_bounds, parse_created_at


class TestCensusWindows:
    def test_covers_every_month_inclusively(self):
        w = month_windows(date(2019, 1, 1), date(2019, 3, 31))
        assert w == [(date(2019, 1, 1), date(2019, 1, 31)),
                     (date(2019, 2, 1), date(2019, 2, 28)),
                     (date(2019, 3, 1), date(2019, 3, 31))]

    def test_final_window_is_clipped_to_the_end_date(self):
        # The census runs to "today", which is mid-month. Asking Finnhub for
        # dates in the future is harmless but the window must not claim them.
        w = month_windows(date(2026, 8, 1), date(2026, 9, 8))
        assert w[-1] == (date(2026, 9, 1), date(2026, 9, 8))

    def test_crosses_a_year_boundary(self):
        w = month_windows(date(2020, 12, 1), date(2021, 1, 31))
        assert w == [(date(2020, 12, 1), date(2020, 12, 31)),
                     (date(2021, 1, 1), date(2021, 1, 31))]

    def test_leap_february(self):
        assert month_windows(date(2020, 2, 1), date(2020, 2, 29))[0][1] == date(2020, 2, 29)


class TestPriceField:
    """Finnhub's `price` is a number, a range string, or null."""

    def test_single_number(self):
        assert _clean_price("16.00") == (16.0, 16.0, "16.00")

    def test_range_string_keeps_the_original(self):
        # The verbatim string is retained because the module has to reconcile
        # the filed range against the actual first trade; a parsed midpoint
        # would destroy the evidence of which one it was.
        assert _clean_price("18.00-20.00") == (18.0, 20.0, "18.00-20.00")

    def test_null_and_empty(self):
        assert _clean_price(None) == (None, None, None)
        assert _clean_price("") == (None, None, None)

    def test_unparseable_text_is_preserved_not_dropped(self):
        assert _clean_price("TBD") == (None, None, "TBD")


class TestMonthArithmetic:
    def test_month_end(self):
        assert month_end(date(2024, 2, 1)) == date(2024, 2, 29)
        assert month_end(date(2023, 12, 1)) == date(2023, 12, 31)

    def test_add_months_backwards_across_years(self):
        assert add_months(date(2025, 3, 3), -24) == date(2023, 3, 1)

    def test_add_months_forwards(self):
        assert add_months(date(2025, 11, 20), 3) == date(2026, 2, 1)

    def test_month_starts_is_inclusive_of_both_ends(self):
        got = month_starts(date(2025, 1, 1), date(2025, 3, 1))
        assert got == [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)]

    def test_month_bounds_is_half_open(self):
        # [start, end) -- the upper bound is the next month's first day, so a
        # tweet stamped on the 1st of the next month is out of window.
        assert month_bounds(date(2024, 1, 1)) == (date(2024, 1, 1), date(2024, 2, 1))
        assert month_bounds(date(2024, 12, 1)) == (date(2024, 12, 1), date(2025, 1, 1))


class TestTweetTimestamps:
    def test_parses_x_format(self):
        got = parse_created_at("Fri Feb 01 04:05:59 +0000 2019")
        assert got == datetime.fromisoformat("2019-02-01T04:05:59+00:00")

    def test_bad_input_is_none_not_an_exception(self):
        # A collector must not die on one malformed row in a page of twenty.
        assert parse_created_at(None) is None
        assert parse_created_at("") is None
        assert parse_created_at("not a date") is None

    def test_the_out_of_window_case_the_probe_found(self):
        # The service returned this stamp for a query bounded until:2019-01-31,
        # which is why counts are re-filtered client-side rather than trusting
        # the operator.
        ts = parse_created_at("Fri Feb 01 04:05:59 +0000 2019")
        start, end = month_bounds(date(2019, 1, 1))
        assert not (start <= ts.date() < end)


class TestFrozenQueries:
    def test_unambiguous_name_is_a_quoted_phrase(self):
        nyt_q, tw_q, note, ambiguous = query_for("Instacart")
        assert nyt_q == '"Instacart"'
        assert ambiguous is False
        assert note == ""

    def test_ordinary_word_brand_is_narrowed_and_flagged(self):
        nyt_q, _, note, ambiguous = query_for("Circle Internet")
        assert ambiguous is True
        assert "stablecoin" in nyt_q     # the frozen qualifier
        assert note                       # the reason is recorded, not implied

    def test_narrowing_is_stable_across_calls(self):
        # The rule is data, not a heuristic recomputed per run: a query form
        # that drifted between runs would make two months incomparable.
        assert query_for("Unity") == query_for("Unity")


class TestSlug:
    def test_apostrophes_and_spaces_collapse(self):
        assert slug("Jersey Mike's Subs") == "jersey-mike-s-subs"

    def test_is_stable_for_cache_keys(self):
        assert slug("Circle Internet") == slug("Circle Internet")


def test_month_name_table_is_complete():
    assert len(MONTHS) == 12
    assert MONTHS["January"] == 1 and MONTHS["December"] == 12


class TestWatchlistReader:
    """The CIK must survive a round trip as text.

    `pd.read_csv` infers `0001769628` as the float 1769628.0, which drops the
    zero padding. That is invisible until an EDGAR lookup misses, so the module
    has one reader and this test is what keeps it honest.
    """

    def test_cik_stays_a_zero_padded_string(self):
        import pandas as pd

        from research.collect.edgar_enrich import read_watchlist_df
        from research.collect.paths import WATCHLIST_CSV

        if not WATCHLIST_CSV.exists():
            import pytest
            pytest.skip("watchlist.csv not built yet")

        df = read_watchlist_df()
        ciks = df["cik"].dropna()
        assert len(ciks) > 0
        assert all(isinstance(c, str) for c in ciks)
        assert all(len(c) == 10 and c.isdigit() for c in ciks), ciks.unique()[:5]

        # And demonstrate the failure the reader exists to prevent.
        naive = pd.read_csv(WATCHLIST_CSV)["cik"].dropna()
        assert naive.dtype != object, "if this fails the hazard is gone; simplify the reader"


class TestIntervalUnion:
    """The density denominator is a union of covered intervals, not a sum.

    Anchors overlap by construction when volume is low, so summing their spans
    counts the same calendar twice. An earlier version produced a 63-day span
    for a 31-day month that way, which understated the rate by half.
    """

    @staticmethod
    def _union_days(intervals):
        from datetime import datetime
        merged = []
        for lo, hi in sorted(intervals):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        return sum((hi - lo).total_seconds() for lo, hi in merged) / 86400.0

    def test_fully_overlapping_intervals_count_once(self):
        from datetime import UTC, datetime
        a = (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 31, tzinfo=UTC))
        b = (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 21, tzinfo=UTC))
        c = (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 11, tzinfo=UTC))
        assert self._union_days([a, b, c]) == 30.0     # not 30 + 20 + 10

    def test_disjoint_intervals_add(self):
        from datetime import UTC, datetime
        a = (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 6, tzinfo=UTC))
        b = (datetime(2024, 1, 20, tzinfo=UTC), datetime(2024, 1, 25, tzinfo=UTC))
        assert self._union_days([a, b]) == 10.0

    def test_partially_overlapping_intervals_merge(self):
        from datetime import UTC, datetime
        a = (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 15, tzinfo=UTC))
        b = (datetime(2024, 1, 10, tzinfo=UTC), datetime(2024, 1, 20, tzinfo=UTC))
        assert self._union_days([a, b]) == 19.0


class TestEdgarTimeline:
    """The DRS/S-1 timeline, built from filing lists."""

    @staticmethod
    def _t(filings):
        from research.collect.edgar_events import build_timeline
        return build_timeline("Test Co", "0000000001", "Test Co Inc.", filings)

    def test_drs_precedes_s1_and_the_gap_is_computed(self):
        t = self._t([("DRS", "2024-06-17"), ("S-1", "2024-09-30"),
                     ("S-1/A", "2024-10-15"), ("424B4", "2024-11-01")])
        assert t.drs_first == "2024-06-17"
        assert t.s1_first == "2024-09-30"
        assert t.drs_to_s1_days == 105
        assert t.s1_amendments == 1

    def test_earliest_filing_wins_not_the_first_listed(self):
        # submissions feeds are newest-first, so taking the first row would
        # report the LAST amendment as the registration date.
        t = self._t([("S-1", "2025-03-03"), ("S-1", "2024-11-01"),
                     ("DRS", "2024-08-01")])
        assert t.s1_first == "2024-11-01"

    def test_a_long_gap_is_flagged_against_the_24_month_window(self):
        t = self._t([("DRS", "2021-12-16"), ("S-1", "2024-02-22")])
        assert t.drs_to_s1_days == 798
        assert any("does not reach it" in n for n in t.notes)

    def test_no_drs_is_recorded_rather_than_assumed(self):
        t = self._t([("S-1", "2020-01-15")])
        assert t.drs_first is None
        assert t.drs_to_s1_days is None
        assert any("no DRS" in n for n in t.notes)

    def test_form_d_timeline_and_comment_rounds(self):
        t = self._t([("D", "2017-07-31"), ("D", "2019-02-01"), ("D/A", "2019-03-01"),
                     ("UPLOAD", "2024-01-05"), ("UPLOAD", "2024-02-05"),
                     ("CORRESP", "2024-01-20")])
        assert t.form_d_count == 2
        assert t.form_d_first == "2017-07-31"
        assert t.form_d_last == "2019-02-01"
        assert t.form_d_dates == "2017-07-31|2019-02-01"
        assert t.comment_rounds == 2       # UPLOAD only: rounds the SEC opened
        assert t.corresp_count == 1

    def test_withdrawal_is_captured(self):
        t = self._t([("S-1", "2022-01-10"), ("RW", "2022-08-01")])
        assert t.withdrawn_at == "2022-08-01"
        assert t.withdraw_form == "RW"


class TestWikipediaValidity:
    """Absent months must never become zero months."""

    def test_pre_creation_months_are_invalid_not_zero(self):
        import pandas as pd

        from research.collect.wikipedia import WIKI_PARQUET
        if not WIKI_PARQUET.exists():
            import pytest
            pytest.skip("wikipedia_monthly.parquet not built yet")

        df = pd.read_parquet(WIKI_PARQUET)
        invalid = df[~df["valid_attention"]]
        # The gated column must be NULL for invalid months. A zero there would
        # read as "nobody looked" and manufacture a pre-filing quiet period.
        assert invalid["views_valid"].isna().all()
        # And the raw column is retained, so nothing is destroyed.
        assert invalid["views"].notna().any()

    def test_a_repurposed_title_is_clipped_by_the_edgar_floor(self):
        import pandas as pd

        from research.collect.wikipedia import WIKI_PARQUET
        if not WIKI_PARQUET.exists():
            import pytest
            pytest.skip("wikipedia_monthly.parquet not built yet")

        df = pd.read_parquet(WIKI_PARQUET)
        # "Bullish": the article dates from 2005 when the title held the market
        # term; the company was founded in 2021. Its valid window must start
        # well after the article's own creation date.
        b = df[df["company"] == "Bullish"]
        if b.empty:
            import pytest
            pytest.skip("Bullish not in this cohort")
        assert not b["valid_attention"].all(), "no clipping applied to Bullish"
        first_valid = b.loc[b["valid_attention"], "month"].min()
        assert first_valid >= "2021-01", first_valid
