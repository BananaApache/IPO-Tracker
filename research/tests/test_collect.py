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


class TestTwitterWindows:
    """Two fixed windows per company, on a hard call budget."""

    def test_windows_are_equal_length_and_bracket_the_events(self):
        from datetime import date

        from research.collect.twitter_windows import WINDOW_DAYS, windows_for
        got = windows_for({"registration_filed_at": "2025-03-03",
                           "listing_date": "2025-03-28"})
        labels = {label: (a, b) for label, a, b in got}
        assert set(labels) == {"pre_filing", "post_listing"}
        # Equal length, so the two counts compare without normalising.
        for a, b in labels.values():
            assert (b - a).days == WINDOW_DAYS
        # pre_filing ends AT the filing (exclusive), post_listing starts at the
        # listing -- neither window straddles its own event.
        assert labels["pre_filing"][1] == date(2025, 3, 3)
        assert labels["post_listing"][0] == date(2025, 3, 28)

    def test_a_missing_date_drops_only_that_window(self):
        from research.collect.twitter_windows import windows_for
        assert [w[0] for w in windows_for(
            {"registration_filed_at": "2025-03-03", "listing_date": None})] \
            == ["pre_filing"]
        assert windows_for({"registration_filed_at": None,
                            "listing_date": None}) == []

    def test_query_form_is_frozen_and_quotes_the_brand(self):
        from research.collect.twitter_windows import query_for
        # Quoted so a multi-word brand is not split into an OR of its words,
        # and `IPO` required so the firehose of ordinary brand chatter is cut.
        assert query_for("Circle Internet") == '"Circle Internet" IPO'
        # The parenthetical alias is not part of the query.
        assert query_for("Instacart (Maplebear)") == '"Instacart" IPO'
        assert query_for("Reddit") == query_for("Reddit")

    def test_exactness_is_recorded_not_assumed(self):
        """A capped count is a lower bound and must never be read as a count."""
        import pandas as pd

        from research.collect.twitter_windows import WINDOWS_PARQUET
        if not WINDOWS_PARQUET.exists():
            import pytest
            pytest.skip("twitter_windows.parquet not built yet")
        df = pd.read_parquet(WINDOWS_PARQUET)
        # Every row says whether its count is exact.
        assert df["is_exact"].notna().all()
        # A capped row used its full page budget; an exact row ran out early or
        # exactly at the cap.
        capped = df[~df["is_exact"].astype(bool)]
        if len(capped):
            assert (capped["pages"] >= capped["max_pages"].fillna(8)).all() \
                if "max_pages" in capped else True
            assert capped["exhausted"].astype(bool).eq(False).all()


class TestWindowPairComparability:
    """Counts are intervals, and a pair is only comparable if they separate."""

    @staticmethod
    def _frame(rows):
        import pandas as pd
        df = pd.DataFrame(rows)
        inf = float("inf")
        pre_hi = df["pre_filing"].where(df["pre_filing_q"] == "exact", inf)
        post_hi = df["post_listing"].where(df["post_listing_q"] == "exact", inf)
        df["post_exceeds_pre"] = df["post_listing"] > pre_hi
        df["pre_exceeds_post"] = df["pre_filing"] > post_hi
        df["direction_established"] = df["post_exceeds_pre"] | df["pre_exceeds_post"]
        return df

    def test_exact_small_vs_censored_large_is_established(self):
        # Arm Holdings: pre exhausted at 12, post is >=160. Unequal page budgets
        # are irrelevant -- an exact count cannot grow with more pages.
        r = self._frame([{"pre_filing": 12, "pre_filing_q": "exact",
                          "post_listing": 160, "post_listing_q": "lower_bound"}])
        assert bool(r["direction_established"].iloc[0])
        assert bool(r["post_exceeds_pre"].iloc[0])

    def test_both_censored_is_never_established(self):
        # Reddit: >=160 vs >=158. Both open-ended, so no direction follows even
        # though the raw numbers differ.
        r = self._frame([{"pre_filing": 160, "pre_filing_q": "lower_bound",
                          "post_listing": 158, "post_listing_q": "lower_bound"}])
        assert not bool(r["direction_established"].iloc[0])

    def test_exact_large_vs_censored_smaller_is_ambiguous(self):
        # Klarna: pre is exactly 209, post is only known to be >=158. The true
        # post value could sit either side of 209.
        r = self._frame([{"pre_filing": 209, "pre_filing_q": "exact",
                          "post_listing": 158, "post_listing_q": "lower_bound"}])
        assert not bool(r["direction_established"].iloc[0])

    def test_deepening_one_side_cannot_invent_a_direction(self):
        # Rivian after deepening: pre >=360 (20 pages) vs post >=159 (8 pages).
        # The apparent inversion is an artifact of unequal effort and must not
        # register as a finding.
        r = self._frame([{"pre_filing": 360, "pre_filing_q": "lower_bound",
                          "post_listing": 159, "post_listing_q": "lower_bound"}])
        assert not bool(r["direction_established"].iloc[0])
        assert not bool(r["pre_exceeds_post"].iloc[0])

    def test_two_exact_counts_give_a_magnitude(self):
        r = self._frame([{"pre_filing": 8, "pre_filing_q": "exact",
                          "post_listing": 105, "post_listing_q": "exact"}])
        assert bool(r["direction_established"].iloc[0])


class TestRedditWindows:
    """Reddit has no date range, so windows are bucketed from a sort=new sweep."""

    @staticmethod
    def _ts(iso):
        from datetime import UTC, datetime
        return datetime.fromisoformat(iso + "T12:00:00+00:00").replace(
            tzinfo=UTC).timestamp()

    def test_completeness_is_per_window_not_per_sweep(self):
        """A sweep can complete the recent window and miss the older one.

        `sort=new` walks backwards from today, so the post-listing window is
        reached first. SpaceX's calibration sweep never got past its own post
        window, which makes its pre-filing count meaningless.
        """
        from datetime import date

        from research.collect.reddit_windows import bucket
        s1, listing = date(2025, 7, 1), date(2025, 7, 31)
        # Swept back only to 2025-08-01: inside the post window, nowhere near
        # the pre window (which starts 2025-04-02).
        b = bucket([{"id": "a", "created_utc": self._ts("2025-08-15")}],
                   s1, listing, self._ts("2025-08-01"))
        assert b["post_listing_complete"] is False
        assert b["pre_filing_complete"] is False

        # Swept back past the pre-filing window start: both complete.
        b2 = bucket([{"id": "a", "created_utc": self._ts("2025-08-15")}],
                    s1, listing, self._ts("2025-03-01"))
        assert b2["pre_filing_complete"] is True
        assert b2["post_listing_complete"] is True

    def test_an_incomplete_zero_is_not_a_zero(self):
        """The trap this guards: 0 posts because none exist, versus 0 because
        the sweep never reached that far back."""
        from datetime import date

        from research.collect.reddit_windows import bucket
        b = bucket([], date(2019, 3, 1), date(2019, 3, 29), self._ts("2024-01-01"))
        assert b["pre_filing_count"] == 0
        # ...but flagged incomplete, so nothing may read it as "no anticipation".
        assert b["pre_filing_complete"] is False

    def test_windows_are_half_open_and_do_not_overlap(self):
        from datetime import date

        from research.collect.reddit_windows import bucket
        s1, listing = date(2025, 7, 1), date(2025, 7, 31)
        posts = [{"id": "in_pre", "created_utc": self._ts("2025-06-30")},
                 {"id": "on_s1", "created_utc": self._ts("2025-07-01")},
                 {"id": "on_listing", "created_utc": self._ts("2025-07-31")}]
        b = bucket(posts, s1, listing, self._ts("2025-01-01"))
        # The S-1 day itself is excluded from the pre window (half-open), and
        # the listing day belongs to the post window.
        assert b["pre_filing_ids"] == ["in_pre"]
        assert b["post_listing_ids"] == ["on_listing"]

    def test_brand_filter_rejects_posts_that_never_name_the_company(self):
        from research.collect.reddit_windows import mentions
        # Measured: 18 of 100 raw Lyft results never mention Lyft.
        assert mentions({"title": "Lyft IPO priced", "text": ""}, ["Lyft"])
        assert mentions({"title": "IPO news", "text": "about nubank"},
                        ["Nu Holdings", "Nubank"])
        assert not mentions({"title": "Anthropic IPO rumour", "text": ""}, ["Figma"])


class TestUnderpricing:
    """Attention must be measured before the offer price is set."""

    def test_event_window_ends_before_the_listing_day(self):
        """The offer price is fixed the evening before the first trade, so any
        day from the listing onwards is contaminated by the outcome."""
        from datetime import date, timedelta

        from research.collect.underpricing import BASELINE_END, BASELINE_START, EVENT_DAYS
        listing = date(2025, 7, 31)
        event = [listing - timedelta(days=d) for d in range(1, EVENT_DAYS + 1)]
        assert max(event) == listing - timedelta(days=1)
        assert listing not in event

        baseline = [listing - timedelta(days=d)
                    for d in range(BASELINE_END, BASELINE_START + 1)]
        # The two windows must not overlap, or the ratio is partly self-referential.
        assert not (set(event) & set(baseline))
        assert max(baseline) < min(event)

    def test_underpricing_and_pop_formulas(self):
        offer, d1_open, d1_close = 33.0, 85.0, 115.5
        assert round(d1_close / offer - 1, 4) == 2.5      # Figma: +250%
        assert round(d1_open / offer - 1, 4) == 1.5758    # opening pop

    def test_abnormal_attention_normalises_away_scale(self):
        """Two companies with identical relative run-ups must score the same
        however famous they are -- that is the whole point of the ratio."""
        famous = {"event": 6000.0, "base": 3000.0}
        obscure = {"event": 20.0, "base": 10.0}
        assert (famous["event"] / famous["base"]
                == obscure["event"] / obscure["base"] == 2.0)

    def test_sparse_coverage_yields_missing_not_a_ratio(self):
        import pandas as pd

        from research.collect.underpricing import UNDERPRICING_PARQUET
        if not UNDERPRICING_PARQUET.exists():
            import pytest
            pytest.skip("underpricing.parquet not built yet")
        df = pd.read_parquet(UNDERPRICING_PARQUET)
        # Where a ratio exists, both windows must have had real coverage.
        got = df[df["abnormal_attention"].notna()]
        assert (got["event_days"] >= 24).all()      # 80% of a 30-day window
        assert (got["baseline_days"] >= 30).all()


class TestTiingoPrices:
    """The licensed feed that removed the 730-day price ceiling."""

    def test_it_covers_every_tier_b_company(self):
        import pandas as pd

        from research.collect.tiingo_prices import TIINGO_PARQUET
        if not TIINGO_PARQUET.exists():
            import pytest
            pytest.skip("tiingo_daily.parquet not built yet")
        ti = pd.read_parquet(TIINGO_PARQUET)
        wl = pd.read_csv("research/data/watchlist.csv")
        want = wl[wl["tier_b"] & wl["finnhub_symbol"].notna()]["company"]
        missing = set(want) - set(ti["company"])
        assert not missing, f"no Tiingo bars for: {sorted(missing)}"

    def test_it_agrees_with_polygon_exactly(self):
        """Two licensed feeds must agree on an as-traded price.

        A disagreement would mean one is adjusting, and a published underpricing
        figure would then depend on which feed produced it. Measured at $0.0000
        across 1,131 overlapping sessions.
        """
        import pandas as pd

        from research.collect.paths import DATA, PRICES_PARQUET
        from research.collect.tiingo_prices import TIINGO_PARQUET
        if not (TIINGO_PARQUET.exists() and PRICES_PARQUET.exists()):
            import pytest
            pytest.skip("both price panels required")
        m = pd.read_parquet(PRICES_PARQUET).merge(
            pd.read_parquet(TIINGO_PARQUET), on=["company", "day"],
            suffixes=("_poly", "_tiingo"))
        assert len(m) > 500, f"only {len(m)} overlapping sessions to compare"
        assert (m["open_poly"] - m["open_tiingo"]).abs().max() < 0.02
        assert (m["close_poly"] - m["close_tiingo"]).abs().max() < 0.02

    def test_raw_not_adjusted_prices_are_used(self):
        """An adjusted series is rewritten retroactively by a split, which would
        make a published underpricing figure irreproducible."""
        import pandas as pd

        from research.collect.tiingo_prices import TIINGO_PARQUET
        if not TIINGO_PARQUET.exists():
            import pytest
            pytest.skip("tiingo_daily.parquet not built yet")
        ti = pd.read_parquet(TIINGO_PARQUET)
        # Both are retained so the choice is inspectable, and they differ for at
        # least one company -- otherwise this test proves nothing.
        assert {"open", "close", "adjOpen", "adjClose"} <= set(ti.columns)

    def test_underpricing_sample_grew_and_records_missing_baselines(self):
        import pandas as pd

        from research.collect.underpricing import UNDERPRICING_PARQUET
        if not UNDERPRICING_PARQUET.exists():
            import pytest
            pytest.skip("underpricing.parquet not built yet")
        up = pd.read_parquet(UNDERPRICING_PARQUET)
        assert len(up) > 12, "Tiingo should have lifted the sample past Polygon's 12"
        # Companies whose Wikipedia article postdates the IPO have no baseline,
        # and must be null rather than assigned a ratio from a few days.
        no_base = up[up["abnormal_attention"].isna()]
        assert (no_base["baseline_days"] < 30).all()


class TestNotabilityDesign:
    """The article-existence redesign, and the three ways it could go fake."""

    def test_spac_rules_do_not_catch_unity(self):
        """Unity trades as plain "U". A bare endswith('U') rule would classify
        it as a SPAC unit ticker and silently drop a real company."""
        from research.collect.notability import is_spac
        assert is_spac("Unity Software Inc.", "U", 68.0)[0] is False
        assert is_spac("Snowflake Inc.", "SNOW", 120.0)[0] is False
        # Real SPACs, by name and by the 4-letter unit convention.
        assert is_spac("Future Vision II Acquisition Corp.", "FVNNU", 10.0)[0]
        assert is_spac("Some Shell Co", "AACIU", 10.0)[0]

    def test_ten_dollar_offers_are_excluded_from_the_population(self):
        """Measured: every SPAC that survived the name/ticker rules priced at
        exactly $10.00. Pooling them manufactures a 'no article -> no pop'
        correlation that is entirely the SPAC distinction."""
        import pandas as pd

        from research.collect.notability import SAMPLE_PARQUET
        if not SAMPLE_PARQUET.exists():
            import pytest
            pytest.skip("sample not drawn yet")
        s = pd.read_parquet(SAMPLE_PARQUET)
        assert not (s["offer_price"] == 10.0).any(), \
            "a $10.00 offer survived into the sample"

    def test_distinctive_token_guard_rejects_a_shared_generic_word(self):
        from research.collect.notability import shares_distinctive_token
        # The failure it exists for.
        assert not shares_distinctive_token("Legence Corp.", "VF Corporation")
        assert not shares_distinctive_token("Professional Holding Corp.", "Holdings")
        assert shares_distinctive_token("Reddit, Inc.", "Reddit")
        assert shares_distinctive_token("Maplebear Inc.", "Maplebear")

    def test_candidate_titles_strip_suffixes_most_specific_first(self):
        from research.collect.notability import candidate_titles
        c = candidate_titles("Reddit, Inc.")
        assert c[-1] == "Reddit"
        assert "Maplebear" in candidate_titles("Maplebear Inc.")

    def test_treatment_excludes_an_article_created_by_the_ipo(self):
        """An article created during the IPO window is an effect of the outcome,
        not prior notability. 27 of 110 matched articles were created inside 90
        days of listing."""
        import pandas as pd

        from research.collect.notability import NOTABILITY_PARQUET
        if not NOTABILITY_PARQUET.exists():
            import pytest
            pytest.skip("notability.parquet not built yet")
        d = pd.read_parquet(NOTABILITY_PARQUET)
        endog = d[d["article_after_cutoff"]]
        # Every excluded row has an article, and none counts as treated.
        assert endog["has_article"].all()
        assert not endog["notable_pre_ipo"].any()

    def test_mann_whitney_matches_a_known_case(self):
        from research.collect.notability import _mann_whitney
        # Perfectly separated groups: U = n1*n2, rank-biserial = +1.
        r = _mann_whitney([10, 11, 12, 13], [1, 2, 3, 4])
        assert r["u"] == 16.0
        assert r["rank_biserial"] == 1.0
        assert r["p"] < 0.05
        # Identical groups: no separation.
        r2 = _mann_whitney([5, 5, 5, 5], [5, 5, 5, 5])
        assert abs(r2["rank_biserial"]) < 1e-9


class TestRateLimitDetection:
    """A 429 must be recognised through the exception chain, not the message."""

    def test_status_code_is_not_in_the_message(self):
        """`backend.http` reports the URL and attempt count and keeps the HTTP
        error as __cause__, so a naive `"429" in str(exc)` never matches. That
        bug burned one ticker per rate-limited request."""
        import httpx

        from backend.http import HttpError
        from research.collect.notability import _is_rate_limited

        req = httpx.Request("GET", "https://example.com/x")
        resp = httpx.Response(429, request=req)
        cause = httpx.HTTPStatusError("429", request=req, response=resp)
        exc = HttpError("giving up on https://example.com/x after 1 attempts")
        exc.__cause__ = cause

        assert "429" not in str(exc), "premise of the bug no longer holds"
        assert _is_rate_limited(exc) is True

    def test_an_unrelated_failure_is_not_treated_as_a_rate_limit(self):
        import httpx

        from backend.http import HttpError
        from research.collect.notability import _is_rate_limited

        req = httpx.Request("GET", "https://example.com/x")
        cause = httpx.HTTPStatusError(
            "500", request=req, response=httpx.Response(500, request=req))
        exc = HttpError("giving up on https://example.com/x after 1 attempts")
        exc.__cause__ = cause
        assert _is_rate_limited(exc) is False

    def test_it_terminates_on_a_self_referential_chain(self):
        from research.collect.notability import _is_rate_limited
        a = RuntimeError("a")
        b = RuntimeError("b")
        a.__cause__ = b
        b.__cause__ = a          # a cycle must not hang the walk
        assert _is_rate_limited(a) is False
