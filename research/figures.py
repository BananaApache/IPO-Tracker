"""Figure frames and plots for the research module.

Two halves, deliberately separated:

  * `build_frames()` reads the collected parquet files and writes the **exact**
    dataframe behind each figure to `data/figures/*.parquet`.
  * the `plot_*` functions read only those files.

Nothing in a plotting function touches the network or the raw cache. So every
figure regenerates with the network off, and the numbers in a figure can be
inspected as a table without re-deriving them from the collectors.

Design constraints from the module brief, and why each one is here:

  * **No dual y-axes, ever.** Counts and price never share an axis. Apparent
    alignment on a twin axis is a function of the scale you happened to pick,
    which is the exact artifact this module exists to test rather than produce.
  * **The price panel starts at the listing date.** Roughly 24 of the 27 months
    in each window are pre-listing, and that region is left blank and shaded
    "not publicly traded". Not interpolated, not backfilled, not zero-filled,
    and not replaced with a private-valuation series. The blank is a fact about
    the company.
  * **News and social are never summed or averaged together.** They are
    different instruments with different coverage, one of them from an
    unofficial scraper, and a blended "attention" number would hide that.
  * **Monthly bars for counts, a daily line for price.** The resolution
    difference is real, so the chart shows it rather than smoothing counts into
    a fake daily curve. No trendlines: 27 monthly points do not support one.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

from research.collect.paths import (
    FIGURES,
    NYT_COUNTS_PARQUET,
    PRICES_PARQUET,
    TWITTER_COUNTS_PARQUET,
    WATCHLIST_CSV,
    ensure_dirs,
)
from research.collect.wikipedia import WIKI_PARQUET

logger = logging.getLogger(__name__)

# Slots 1 and 2 of the reference categorical palette. Two series is the ceiling
# in every figure here, which is also what keeps the pair clear of the
# colour-vision gates: worst all-pairs CVD dE 24.7, contrast >= 3:1 on the light
# surface. Both validated with the palette validator rather than by eye.
BLUE = "#2a78d6"
ORANGE = "#eb6834"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
SURFACE = "#fcfcfb"
GRID = "#e4e3df"
# The pre-listing shade. Deliberately a neutral, not a palette hue: it marks an
# absence of data, and a categorical colour there would read as a third series.
ABSENT = "#eeedea"

FIG_COHORT = FIGURES / "cohort_comparison.parquet"
FIG_PANELS = FIGURES / "company_panels.parquet"
FIG_RELATIVE = FIGURES / "relative_time.parquet"
FIG_POST = FIGURES / "post_listing.parquet"
FIG_WINDOWS = FIGURES / "twitter_windows.parquet"
FIG_REDDIT_WINDOWS = FIGURES / "reddit_windows.parquet"
FIG_UNDERPRICING = FIGURES / "underpricing.parquet"
FIG_MANIFEST = FIGURES / "manifest.json"


def _style() -> None:
    matplotlib.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_SOFT,
        "axes.titlecolor": INK,
        "axes.titlesize": 10,
        "axes.titleweight": "medium",
        "axes.labelsize": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": INK_SOFT,
        "ytick.color": INK_SOFT,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "font.size": 9,
    })


# --------------------------------------------------------------------------
# frame building
# --------------------------------------------------------------------------

def _anchors() -> pd.DataFrame:
    """Per-company event dates, including the confidential DRS.

    DRS is carried alongside the public S-1 because the two are different events
    and the gap between them is the module's sharpest question. Measured across
    the watchlist the median gap is ~96 days and the maximum 1,408, so treating
    the S-1 as "the start" silently backdates a company that had already been in
    registration for years.
    """
    from research.collect.edgar_events import EVENTS_PARQUET

    wl = _watchlist()[["company", "registration_filed_at", "listing_date"]]
    wl = wl.rename(columns={"registration_filed_at": "s1_date"})
    if EVENTS_PARQUET.exists():
        ev = pd.read_parquet(EVENTS_PARQUET)[["company", "drs_first",
                                              "drs_to_s1_days"]]
        ev["drs_date"] = pd.to_datetime(ev["drs_first"], errors="coerce")
        wl = wl.merge(ev[["company", "drs_date", "drs_to_s1_days"]],
                      on="company", how="left")
    else:
        wl["drs_date"] = pd.NaT
        wl["drs_to_s1_days"] = pd.NA
    return wl.set_index("company")


def _watchlist() -> pd.DataFrame:
    # Through the module's own reader, not pd.read_csv: the CIK column must stay
    # text or pandas turns a zero-padded identifier into a float.
    from research.collect.edgar_enrich import read_watchlist_df

    wl = read_watchlist_df()
    for col in ("registration_filed_at", "listing_date"):
        wl[col] = pd.to_datetime(wl[col], errors="coerce")
    return wl


def build_cohort_frame() -> pd.DataFrame:
    """Figure 0: how the Tier B watchlist differs from the Tier A population.

    The brief puts this first rather than in an appendix, because every Tier B
    number is only interpretable against it.
    """
    from research.collect.paths import CENSUS_PARQUET

    census = pd.read_parquet(CENSUS_PARQUET)
    census = census[census["status"] == "priced"].copy()
    census["year"] = pd.to_datetime(census["calendar_date"]).dt.year

    wl = _watchlist()
    tier_b = wl[wl["tier_b"]].copy()
    tier_b["year"] = tier_b["listing_date"].dt.year

    rows = []
    for year in sorted(set(census["year"]) | set(tier_b["year"].dropna())):
        a = int((census["year"] == year).sum())
        b = int((tier_b["year"] == year).sum())
        rows.append({"dimension": "listing year", "bucket": str(int(year)),
                     "tier_a": a, "tier_b": b})

    # Deal size, the dimension on which a hand-picked list of famous names is
    # most obviously unlike the population.
    census["deal_usd"] = pd.to_numeric(census["total_shares_value"], errors="coerce")
    bins = [0, 5e7, 2e8, 1e9, float("inf")]
    labels = ["<$50M", "$50-200M", "$200M-1B", ">$1B"]
    census["size_bucket"] = pd.cut(census["deal_usd"], bins=bins, labels=labels)
    # Joined on symbol AND date, not symbol alone. 52 symbols are duplicated
    # among the priced rows -- SPAC shells recycle a ticker across successive
    # vehicles years apart ("AACIU" is Armada Acquisition Corp I, II and III) --
    # so a symbol-only merge fans out and silently double-counts the watchlist
    # row against every unrelated vehicle that ever held its ticker.
    census["_join_date"] = census["calendar_date"].astype(str)
    tb_sizes = tier_b.merge(
        census[["symbol", "_join_date", "size_bucket"]].drop_duplicates(
            ["symbol", "_join_date"]),
        left_on=["finnhub_symbol", "finnhub_date"],
        right_on=["symbol", "_join_date"], how="left", validate="one_to_one")
    for label in labels:
        rows.append({"dimension": "deal size", "bucket": label,
                     "tier_a": int((census["size_bucket"] == label).sum()),
                     "tier_b": int((tb_sizes["size_bucket"] == label).sum())})

    for exch in ("NASDAQ", "NYSE", "other"):
        def bucket(series: pd.Series) -> int:
            s = series.fillna("").str.upper()
            if exch == "other":
                return int((~s.str.contains("NASDAQ") & ~s.str.contains("NYSE")).sum())
            return int(s.str.contains(exch).sum())
        rows.append({"dimension": "exchange", "bucket": exch,
                     "tier_a": bucket(census["exchange"]),
                     "tier_b": bucket(tier_b["seed_exchange"])})

    df = pd.DataFrame(rows)
    # Shares within each dimension, so a 12-row sample is comparable to a
    # 2,958-row population at all.
    for col in ("tier_a", "tier_b"):
        df[f"{col}_share"] = df.groupby("dimension")[col].transform(
            lambda s: s / s.sum() if s.sum() else 0.0)
    df.to_parquet(FIG_COHORT, index=False)
    return df


def build_panel_frame() -> pd.DataFrame:
    """Figure 1: per-company monthly news, monthly social, daily price.

    One long frame rather than three, so the figure's backing data is a single
    file that can be read as a table. `series` says which panel a row belongs
    to and `resolution` says whether it is a monthly or a daily observation --
    the two are never mixed inside one panel.
    """
    anchors = _anchors()
    frames = []

    if NYT_COUNTS_PARQUET.exists():
        nyt = pd.read_parquet(NYT_COUNTS_PARQUET)
        frames.append(pd.DataFrame({
            "company": nyt["company"],
            "series": "nyt_articles",
            "resolution": "monthly",
            "x": pd.to_datetime(nyt["month"] + "-01"),
            "value": pd.to_numeric(nyt["count"], errors="coerce"),
            "flag": "",
        }))

    if TWITTER_COUNTS_PARQUET.exists():
        tw = pd.read_parquet(TWITTER_COUNTS_PARQUET)
        frames.append(pd.DataFrame({
            "company": tw["company"],
            "series": "x_density_per_day",
            "resolution": "monthly",
            "x": pd.to_datetime(tw["month"] + "-01"),
            "value": pd.to_numeric(tw["density_per_day"], errors="coerce"),
            # Three states, not two. A complete month is a count; a
            # tail-sampled month is a rate estimated from part of the month; a
            # month that exceeded the budget carries NO rate at all and must
            # never be drawn as a value. The figure separates all three.
            "flag": [
                "exceeds-budget" if over else
                ("complete" if reached else "tail-sample")
                for over, reached in zip(
                    tw["volume_exceeds_budget"].fillna(False),
                    tw["reached_month_start"].fillna(False))
            ],
        }))

    # Wikipedia pageviews: the dense, free, official series. `views_valid` is
    # used rather than `views` -- months before the article existed, or before
    # the company had any EDGAR trace, belong to a previous occupant of the
    # title and are null rather than zero. Zero-filling them would manufacture
    # exactly the "attention rose before the filing" shape being tested for.
    if WIKI_PARQUET.exists():
        wiki = pd.read_parquet(WIKI_PARQUET)
        frames.append(pd.DataFrame({
            "company": wiki["company"],
            "series": "wikipedia_views",
            "resolution": "monthly",
            "x": pd.to_datetime(wiki["month"] + "-01"),
            "value": pd.to_numeric(wiki["views_valid"], errors="coerce"),
            "flag": wiki["valid_attention"].map(
                {True: "valid", False: "pre-article or pre-EDGAR"}),
        }))

    if PRICES_PARQUET.exists():
        px = pd.read_parquet(PRICES_PARQUET)
        frames.append(pd.DataFrame({
            "company": px["company"],
            "series": "close",
            "resolution": "daily",
            "x": pd.to_datetime(px["day"]),
            "value": pd.to_numeric(px["close"], errors="coerce"),
            "flag": px["in_90d_window"].map({True: "in_90d", False: "beyond_90d"}),
        }))

    if not frames:
        raise SystemExit("nothing collected yet; run the collectors first.")
    df = pd.concat(frames, ignore_index=True)
    df = df.merge(anchors, left_on="company", right_index=True, how="left")
    df = df.sort_values(["company", "series", "x"]).reset_index(drop=True)
    df.to_parquet(FIG_PANELS, index=False)
    return df


def build_relative_frame() -> pd.DataFrame:
    """Figure 2: monthly counts on an x-axis of months relative to listing.

    Calendar-time overlay would mostly show that 2021 and 2025 were different
    markets, which is not the question. Relative time is the only form in which
    cross-company comparison is legitimate.

    Normalization is **share of that company's own window total**, fixed here
    before looking at any result. Raw counts would make SpaceX and Klaviyo
    incomparable; a z-score within company would centre every company on zero
    and hide the level differences that the share form keeps.
    """
    panels = pd.read_parquet(FIG_PANELS) if FIG_PANELS.exists() else build_panel_frame()
    monthly = panels[panels["resolution"] == "monthly"].copy()
    monthly = monthly[monthly["listing_date"].notna()]

    # Relative month against each of the three anchors, not just the listing.
    # The DRS anchor is what makes the leakage question askable: it aligns
    # companies on the date the registration became real but was still secret.
    for name, col in (("listing", "listing_date"), ("s1", "s1_date"),
                      ("drs", "drs_date")):
        if col in monthly.columns:
            monthly[f"rel_{name}"] = (
                (monthly["x"].dt.year - monthly[col].dt.year) * 12
                + (monthly["x"].dt.month - monthly[col].dt.month))
        else:
            monthly[f"rel_{name}"] = pd.NA
    monthly["rel_month"] = monthly["rel_listing"]

    # How many months the company's window *should* have, so a partially
    # collected company can be excluded rather than silently normalised against
    # a truncated denominator. Share-of-window-total is only meaningful over a
    # complete window: with two months collected, the first month reads as 50%
    # of the company's lifetime coverage, which is an artifact of the run
    # stopping, not a finding.
    from research.collect.edgar_enrich import read_watchlist_df
    from research.collect.nyt import month_starts, window_for
    wl_rows = read_watchlist_df().to_dict("records")
    expected = {}
    for row in wl_rows:
        win = window_for({k: ("" if pd.isna(v) else str(v)) for k, v in row.items()})
        if win is not None:
            expected[row["company"]] = len(month_starts(*win))

    out = []
    for (company, series), grp in monthly.groupby(["company", "series"]):
        total = grp["value"].sum(skipna=True)
        g = grp.copy()
        # A company whose window total is zero has no share to take. Left as NaN
        # rather than 0 so it drops out of the median instead of dragging it.
        g["share"] = g["value"] / total if total and total > 0 else pd.NA
        g["window_total"] = total
        n_expected = expected.get(company)
        g["months_collected"] = grp["value"].notna().sum()
        g["months_expected"] = n_expected
        g["window_coverage"] = (
            round(grp["value"].notna().sum() / n_expected, 4) if n_expected else pd.NA)
        out.append(g)
    df = pd.concat(out, ignore_index=True)
    df["cohort"] = "listed (watchlist)"
    df = df[["company", "cohort", "series", "rel_month", "rel_listing", "rel_s1",
             "rel_drs", "x", "value", "share", "window_total",
             "months_collected", "months_expected", "window_coverage", "flag"]]
    df.to_parquet(FIG_RELATIVE, index=False)
    return df


def build_post_listing_frame() -> pd.DataFrame:
    """Figure 3: the post-listing tail, where price and attention coexist.

    This is the only region where an overlay is defensible, because both series
    exist. Marker size encodes that month's post volume -- **volume only**. The
    reference image this figure imitates encodes sentiment; no sentiment scoring
    is in scope here and none is derived from counts.
    """
    panels = pd.read_parquet(FIG_PANELS) if FIG_PANELS.exists() else build_panel_frame()
    price = panels[panels["series"] == "close"].copy()
    monthly = panels[panels["resolution"] == "monthly"].copy()

    rows = []
    for company, grp in price.groupby("company"):
        grp = grp.sort_values("x").reset_index(drop=True)
        first = grp["x"].iloc[0]
        grp["session"] = range(len(grp))
        att = monthly[monthly["company"] == company]
        for _, bar in grp.iterrows():
            month_key = bar["x"].to_period("M")
            m = att[att["x"].dt.to_period("M") == month_key]
            # `is_month_marker` picks ONE session per calendar month -- the last
            # one -- as the anchor for an attention marker. Attention is measured
            # monthly, so a marker on all ~21 sessions of a month would assert a
            # daily resolution the data does not have.
            rows.append({
                "company": company,
                "day": bar["x"],
                "session": int(bar["session"]),
                "close": bar["value"],
                "first_trade_date": first,
                "month": str(month_key),
                "nyt_articles_that_month": float(
                    m.loc[m["series"] == "nyt_articles", "value"].sum())
                if (m["series"] == "nyt_articles").any() else float("nan"),
                "x_density_that_month": float(
                    m.loc[m["series"] == "x_density_per_day", "value"].sum())
                if (m["series"] == "x_density_per_day").any() else float("nan"),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        # Return from the opening print, which is the module's stated baseline.
        opens = df.groupby("company")["close"].transform("first")
        df["return_from_first_close"] = df["close"] / opens - 1.0
        last_of_month = (df.groupby(["company", "month"])["session"]
                         .transform("max") == df["session"])
        df["is_month_marker"] = last_of_month
    df.to_parquet(FIG_POST, index=False)
    return df


def build_windows_frame() -> pd.DataFrame:
    """Figure 4: paired X post counts, before filing versus after listing.

    Paired data, so a slope chart rather than two bars: what matters is the
    within-company change, and a grouped bar chart invites reading across
    companies instead.

    The exact/censored distinction is carried through and never averaged away. A
    pair where both sides hit the page cap has a ratio pinned near 1.0 purely
    because both were truncated at ~160 -- Reddit reads 160 vs 158, which says
    nothing about direction. Only pairs with two exact counts support a
    magnitude.
    """
    from research.collect.twitter_windows import WINDOWS_PARQUET

    if not WINDOWS_PARQUET.exists():
        return pd.DataFrame()
    d = pd.read_parquet(WINDOWS_PARQUET)
    usable = d[d["usable"]]
    cnt = usable.pivot_table(index="company", columns="window", values="count",
                             aggfunc="first")
    qual = usable.pivot_table(index="company", columns="window", values="quality",
                              aggfunc="first")
    if "pre_filing" not in cnt or "post_listing" not in cnt:
        return pd.DataFrame()
    pages = usable.pivot_table(index="company", columns="window", values="pages",
                               aggfunc="first")
    df = (cnt.dropna().join(qual, rsuffix="_q").join(pages, rsuffix="_pages")
          .reset_index())
    df["pre_filing"] = df["pre_filing"].astype(int)
    df["post_listing"] = df["post_listing"].astype(int)
    df["both_exact"] = ((df["pre_filing_q"] == "exact")
                        & (df["post_listing_q"] == "exact"))
    df["rose"] = df["post_listing"] > df["pre_filing"]

    # Comparability is NOT the same as usability, and conflating them produces a
    # confidently wrong chart. Treat every count as an INTERVAL and ask whether
    # the intervals actually separate:
    #
    #   exact c        -> [c, c]
    #   lower bound c  -> [c, inf)
    #
    # "post exceeds pre" is established iff post_low > pre_high, and vice versa.
    #
    # This is stricter than it sounds in one direction and looser in another. An
    # earlier version keyed on equal page budgets and wrongly excluded pairs like
    # Arm Holdings -- pre-filing exact at 12 (it exhausted in 3 pages) against
    # post-listing >=160. Unequal effort is irrelevant when the smaller side is
    # exact: it ran out, so more pages cannot find more.
    #
    # It correctly refuses the pairs deepening left genuinely ambiguous. Klarna's
    # pre-filing is exactly 209 and its post-listing is only known to be >=158;
    # the true post value could sit either side of 209, so no direction follows.
    inf = float("inf")
    pre_hi = df["pre_filing"].where(df["pre_filing_q"] == "exact", inf)
    post_hi = df["post_listing"].where(df["post_listing_q"] == "exact", inf)
    df["post_exceeds_pre"] = df["post_listing"] > pre_hi
    df["pre_exceeds_post"] = df["pre_filing"] > post_hi
    df["direction_established"] = df["post_exceeds_pre"] | df["pre_exceeds_post"]
    df["magnitude_valid"] = df["both_exact"]
    df["pair_comparable"] = df["direction_established"]

    df["incomparable_reason"] = ""
    amb = ~df["direction_established"]
    df.loc[amb, "incomparable_reason"] = (
        "intervals overlap: neither count's floor clears the other's ceiling")
    df.loc[amb & (df["pre_filing_q"] == "lower_bound")
           & (df["post_listing_q"] == "lower_bound"),
           "incomparable_reason"] = "both sides censored, so both are open-ended"

    df.to_parquet(FIG_WINDOWS, index=False)
    return df


def build_reddit_windows_frame() -> pd.DataFrame:
    """Figure 4b: paired Reddit post counts over the same two windows.

    Only rows where **both** windows are complete survive. Reddit's endpoint has
    no date range at all, so windows are bucketed client-side from a `sort=new`
    sweep; a sweep that never paged back past a window's start has not measured
    that window, and an incomplete count of zero means "never looked", not
    "nothing there". 28 of 39 sweeps are incomplete for that reason -- Reddit
    stopped issuing a pagination cursor, which no budget can fix.
    """
    from research.collect.reddit_windows import REDDIT_PARQUET

    if not REDDIT_PARQUET.exists():
        return pd.DataFrame()
    d = pd.read_parquet(REDDIT_PARQUET)
    df = d[d["counts_complete"]].copy()
    if df.empty:
        return df
    df = df.rename(columns={"pre_filing_count": "pre_filing",
                            "post_listing_count": "post_listing"})
    # A complete sweep gives a real count on both sides, so every surviving pair
    # supports a magnitude -- unlike the X windows, where most are floors.
    df["pre_filing_q"] = "exact"
    df["post_listing_q"] = "exact"
    df["both_exact"] = True
    df["magnitude_valid"] = True
    df["direction_established"] = df["post_listing"] != df["pre_filing"]
    df["rose"] = df["post_listing"] > df["pre_filing"]
    out = df[["company", "pre_filing", "post_listing", "pre_filing_q",
              "post_listing_q", "both_exact", "magnitude_valid",
              "direction_established", "rose", "pages", "oldest_seen"]]
    out.to_parquet(FIG_REDDIT_WINDOWS, index=False)
    return out


def build_underpricing_frame() -> pd.DataFrame:
    """Figure 5: abnormal pre-listing attention against first-day underpricing."""
    from research.collect.underpricing import UNDERPRICING_PARQUET

    if not UNDERPRICING_PARQUET.exists():
        return pd.DataFrame()
    df = pd.read_parquet(UNDERPRICING_PARQUET)
    df.to_parquet(FIG_UNDERPRICING, index=False)
    return df


def build_frames() -> dict[str, int]:
    """Build every figure frame and record what went into them."""
    ensure_dirs()
    counts = {
        "twitter_windows": len(build_windows_frame()),
        "reddit_windows": len(build_reddit_windows_frame()),
        "underpricing": len(build_underpricing_frame()),
        "cohort_comparison": len(build_cohort_frame()),
        "company_panels": len(build_panel_frame()),
        "relative_time": len(build_relative_frame()),
        "post_listing": len(build_post_listing_frame()),
    }
    FIG_MANIFEST.write_text(json.dumps({
        "built_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "rows": counts,
        "note": "Plotting reads only these files. Regenerate with the network off.",
    }, indent=2) + "\n")
    return counts


# --------------------------------------------------------------------------
# plotting -- reads only data/figures/*.parquet
# --------------------------------------------------------------------------

def plot_cohort_comparison():
    """Figure 0. How unlike the population the watchlist is."""
    _style()
    df = pd.read_parquet(FIG_COHORT)
    dims = list(df["dimension"].unique())
    fig, axes = plt.subplots(1, len(dims), figsize=(4.2 * len(dims), 3.4))
    if len(dims) == 1:
        axes = [axes]

    for ax, dim in zip(axes, dims):
        sub = df[df["dimension"] == dim]
        y = range(len(sub))
        h = 0.38
        # A 2px-equivalent gap between the paired bars, per the mark spec.
        ax.barh([i + h / 2 + 0.02 for i in y], sub["tier_a_share"], height=h,
                color=BLUE, label="Tier A (all priced IPOs)")
        ax.barh([i - h / 2 - 0.02 for i in y], sub["tier_b_share"], height=h,
                color=ORANGE, label="Tier B (watchlist)")
        ax.set_yticks(list(y))
        ax.set_yticklabels(sub["bucket"])
        ax.set_xlabel("share within dimension")
        ax.set_title(dim)
        ax.xaxis.grid(True)
        ax.set_axisbelow(True)

    # Below the axes rather than inside: the longest bar in the first panel
    # reaches into every interior corner.
    axes[0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncols=2)
    fig.suptitle("Figure 0 — the Tier B watchlist against the Tier A population",
                 x=0.01, ha="left", fontsize=11, color=INK)
    fig.text(0.01, -0.04,
             "Shares within each dimension, so a 12-company sample is comparable "
             "to 2,958 priced IPOs. The watchlist is hand-assembled from "
             "well-known names and is a biased sample by construction.",
             fontsize=8, color=INK_SOFT, ha="left")
    fig.tight_layout()
    return fig


def plot_company(company: str, *, log_counts: bool = False):
    """Figure 1, for one company. Stacked panels on a shared x-axis.

    The panel set is adaptive: a series with no data for this company gets no
    panel rather than an empty one. X/Twitter in particular is usually absent
    (its account credits are exhausted), and a permanently blank panel trains
    the eye to ignore a region of the figure.

    Three vertical markers, and their order is the point of the figure:
    the **confidential DRS**, the **public S-1**, and the **listing**. Attention
    that rises after the DRS but before the S-1 happened while the filing was
    still secret.

    `log_counts` is one choice applied to every company or to none -- never per
    company, which would make two panels look alike that are not.
    """
    _style()
    df = pd.read_parquet(FIG_PANELS)
    sub = df[df["company"] == company]
    if sub.empty:
        raise KeyError(f"no rows for {company!r}")

    def anchor(col):
        vals = sub[col].dropna() if col in sub.columns else pd.Series(dtype=object)
        return vals.iloc[0] if len(vals) else None

    drs = anchor("drs_date")
    s1 = anchor("s1_date")
    listing = anchor("listing_date")

    # (series key, y label, colour) for the series that actually have data.
    spec = [
        ("nyt_articles", "NYT articles\nper month", BLUE),
        ("wikipedia_views", "Wikipedia views\nper month", ORANGE),
        ("x_density_per_day", "X posts\nper day", ORANGE),
    ]
    present = [(k, lab, col) for k, lab, col in spec
               if sub[sub["series"] == k]["value"].notna().any()]
    price = sub[sub["series"] == "close"].sort_values("x")
    n = len(present) + 1

    heights = [1] * len(present) + [1.3]
    fig, axes = plt.subplots(n, 1, figsize=(10, 2.1 * len(present) + 3.0),
                             sharex=True, gridspec_kw={"height_ratios": heights})
    axes = list(axes) if n > 1 else [axes]

    for ax, (key, label, colour) in zip(axes, present):
        data = sub[sub["series"] == key].sort_values("x")
        good = data[data["flag"].isin(("complete", "valid", ""))]
        weak = data[~data["flag"].isin(("complete", "valid", ""))
                    & data["value"].notna()]
        ax.bar(good["x"], good["value"], width=24, color=colour, align="edge")
        # Hatched, not a new hue: the distinction is data quality, not identity.
        if len(weak):
            ax.bar(weak["x"], weak["value"], width=24, align="edge", color=colour,
                   alpha=0.45, hatch="///", edgecolor=colour, linewidth=0,
                   label="estimated / partial")
            ax.legend(loc="upper left")
        ax.set_ylabel(label)
        if log_counts:
            ax.set_yscale("symlog", linthresh=1)

    axes[0].set_title(f"{company} — attention before and after listing", loc="left")

    # --- price, daily, starting at the listing ---
    ax_price = axes[-1]
    xmin = sub["x"].min()
    if price["value"].notna().any():
        ax_price.plot(price["x"], price["value"], color=BLUE, linewidth=2)
        shade_to = price["x"].min()
    else:
        ax_price.text(0.5, 0.5,
                      "no price series: listing predates the feed's two-year window",
                      transform=ax_price.transAxes, ha="center", color=INK_SOFT)
        shade_to = sub["x"].max()
    # Shaded and labelled, so the blank reads as a fact rather than missing
    # data. Nothing is interpolated into it.
    ax_price.axvspan(xmin, shade_to, color=ABSENT, zorder=0)
    ax_price.text(xmin, ax_price.get_ylim()[1], "  not publicly traded",
                  va="top", ha="left", fontsize=8, color=INK_SOFT)
    ax_price.set_ylabel("close (USD)")
    ax_price.set_xlabel("calendar month")

    # --- the three event markers ---
    marks = [(drs, "--", "DRS filed (confidential)"),
             (s1, "-.", "S-1 filed (public)"),
             (listing, ":", "listed")]
    for ax in axes:
        ax.yaxis.grid(True)
        ax.set_axisbelow(True)
        for when, style, _ in marks:
            if when is not None:
                ax.axvline(when, color=INK, linestyle=style, linewidth=1.2, zorder=5)
    # Staggered heights and alternating anchors: the DRS and S-1 can be weeks
    # apart on a decade-long axis, and centred labels at one height smear.
    for i, (when, _, label) in enumerate(marks):
        if when is not None:
            axes[0].annotate(label, (when, 1.06 + 0.13 * (len(marks) - 1 - i)),
                             xycoords=("data", "axes fraction"),
                             ha="right" if i < 2 else "left", fontsize=8, color=INK)

    gap = sub["drs_to_s1_days"].dropna()
    caption = ("Counts are monthly; price is daily — no dual axes, the panels "
               "share only the x-axis, and the attention series are separate "
               "instruments that are never summed.")
    if len(gap):
        caption += (f"\nThe DRS preceded the public S-1 by {int(gap.iloc[0])} days. "
                    f"Attention rising between those two lines happened while the "
                    f"registration was still confidential.")
    caption += ("\nGNews omitted: 30-day free-tier history. Reddit omitted: no "
                "date-range API. Wikipedia months before the article existed, or "
                "before the company's first EDGAR filing, are null — never zero.")
    if log_counts:
        caption += "  Count panels are symlog."
    fig.text(0.01, -0.06, caption, fontsize=8, color=INK_SOFT, ha="left",
             linespacing=1.5)
    fig.tight_layout()
    return fig


def plot_relative_time(series: str = "nyt_articles", *, anchor: str = "listing",
                       min_coverage: float = 0.9):
    """Figure 2. Cohort overlay in relative time: median, IQR, faint traces.

    `anchor` selects what month zero means:

      * ``listing`` -- the first trading day. The classic event window.
      * ``s1``      -- the public registration, when the market learned.
      * ``drs``     -- the **confidential** registration, which the market did
        not learn. Attention rising before month zero on this anchor is
        pre-registration interest; attention rising between the DRS and the S-1
        is movement while the filing was still secret.

    Calendar-time overlay would mostly show that 2021 and 2025 were different
    markets, which is not the question.

    `min_coverage` drops companies whose window is not fully collected, because
    share-of-window-total is only meaningful over a complete window. It applies
    to quota-limited series; the Wikipedia series is fetched whole in one request
    and is exempt.
    """
    _style()
    col = {"listing": "rel_listing", "s1": "rel_s1", "drs": "rel_drs"}[anchor]
    df = pd.read_parquet(FIG_RELATIVE)
    sub = df[(df["series"] == series) & df["share"].notna() & df[col].notna()].copy()
    if sub.empty:
        raise SystemExit(f"no rows for series {series!r} on anchor {anchor!r}")

    excluded: list[str] = []
    if series != "wikipedia_views":
        cov = pd.to_numeric(sub["window_coverage"], errors="coerce")
        excluded = sorted(sub.loc[cov < min_coverage, "company"].unique())
        sub = sub[cov >= min_coverage]
        if sub.empty:
            raise SystemExit(
                f"no company has >= {min_coverage:.0%} of its window collected "
                f"for {series!r}; {len(excluded)} are partial.")

    sub["rel"] = sub[col].astype(int)
    # Trim to a window wide enough to show the run-up without a long empty tail.
    sub = sub[(sub["rel"] >= -36) & (sub["rel"] <= 12)]

    fig, ax = plt.subplots(figsize=(10, 4.6))
    for _, grp in sub.groupby("company"):
        g = grp.sort_values("rel")
        ax.plot(g["rel"], g["share"], color=BLUE, alpha=0.16, linewidth=1)

    stats = sub.groupby("rel")["share"].agg(
        median="median", q1=lambda v: v.quantile(0.25),
        q3=lambda v: v.quantile(0.75), n="count").reset_index()
    ax.fill_between(stats["rel"], stats["q1"], stats["q3"], color=BLUE,
                    alpha=0.22, linewidth=0, label="interquartile range")
    ax.plot(stats["rel"], stats["median"], color=BLUE, linewidth=2,
            label=f"median, listed (n={sub['company'].nunique()})")

    ax.axvline(0, color=INK, linestyle=":", linewidth=1.4)
    label = {"listing": "listing month", "s1": "public S-1 month",
             "drs": "confidential DRS month"}[anchor]
    ax.annotate(label, (0, 1.13), xycoords=("data", "axes fraction"),
                ha="right", fontsize=8, color=INK)

    # On the DRS anchor, mark where the public S-1 typically lands, so the
    # confidential interval is visible as a region rather than implied.
    if anchor == "drs":
        gaps = pd.to_numeric(
            _anchors()["drs_to_s1_days"], errors="coerce").dropna()
        if len(gaps):
            med_gap_months = float(gaps.median()) / 30.44
            ax.axvline(med_gap_months, color=ORANGE, linestyle="-.", linewidth=1.4)
            ax.annotate("median public S-1", (med_gap_months, 1.02),
                        xycoords=("data", "axes fraction"), ha="left",
                        fontsize=8, color=ORANGE)
            ax.axvspan(0, med_gap_months, color=ABSENT, zorder=0)
            ax.text(med_gap_months / 2, ax.get_ylim()[1] * 0.96,
                    "registration confidential", ha="center", va="top",
                    fontsize=7.5, color=INK_SOFT)

    ax.set_xlabel(f"months relative to {label}")
    ax.set_ylabel("share of the company's\nown window total")
    ax.set_title(f"Figure 2 — {series} in relative time, anchored on {anchor}",
                 loc="left")
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left")

    caption = ("Withdrawn-company comparison group not yet collected — see "
               "README limitation 2.\n"
               "Each company normalised to its own window total before overlay. "
               "Faint traces are individual companies. With this few companies "
               "the band is wide, and that is the finding, not a rendering "
               "problem.")
    if excluded:
        caption += (f"\nExcluded for incomplete collection "
                    f"(<{min_coverage:.0%} of window): {', '.join(excluded)}.")
    fig.text(0.01, -0.08, caption, fontsize=8, color=INK_SOFT, ha="left",
             linespacing=1.5)
    fig.tight_layout()
    return fig


def plot_post_listing(companies: list[str] | None = None):
    """Figure 3. Post-listing overlay, marker size = that month's post volume."""
    _style()
    df = pd.read_parquet(FIG_POST)
    if df.empty:
        raise SystemExit("no post-listing rows; collect prices first.")
    if companies:
        df = df[df["company"].isin(companies)]

    names = sorted(df["company"].unique())
    ncol = 2
    nrow = -(-len(names) // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(11, 2.6 * nrow), squeeze=False)

    markers_all = df[df["is_month_marker"] & df["nyt_articles_that_month"].notna()]
    # An empty selection gives NaN, and `NaN or 1.0` is NaN -- NaN is truthy.
    # Guarded explicitly so a not-yet-collected period degrades to "no markers"
    # rather than raising out of the size legend.
    vmax = markers_all["nyt_articles_that_month"].max()
    vmax = float(vmax) if pd.notna(vmax) and vmax > 0 else 1.0
    for ax, company in zip([a for row in axes for a in row], names):
        g = df[df["company"] == company].sort_values("session")
        ax.plot(g["session"], g["close"], color=BLUE, linewidth=2, zorder=2)
        # One marker per calendar month, not per session. Attention is a monthly
        # measurement; a marker on every session would imply a daily one.
        m = g[g["is_month_marker"] & g["nyt_articles_that_month"].notna()]
        if not m.empty:
            sizes = 16 + 260 * (m["nyt_articles_that_month"] / vmax)
            # A surface-coloured ring so overlapping markers stay separable.
            ax.scatter(m["session"], m["close"], s=sizes, color=ORANGE, alpha=0.7,
                       edgecolor=SURFACE, linewidth=1.0, zorder=3)
        else:
            ax.text(0.99, 0.92, "no NYT months collected for this period",
                    transform=ax.transAxes, ha="right", fontsize=7.5, color=INK_SOFT)
        ax.set_title(company, loc="left")
        ax.set_ylabel("close (USD)")
        ax.yaxis.grid(True)
        ax.set_axisbelow(True)
        if g["session"].max() < 89:
            ax.text(0.99, 0.06, f"only {int(g['session'].max()) + 1} sessions so far",
                    transform=ax.transAxes, ha="right", fontsize=7.5, color=INK_SOFT)
    for ax in [a for row in axes for a in row][len(names):]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("trading session from the opening print")

    fig.suptitle("Figure 3 — post-listing price, marker size = NYT articles that month",
                 x=0.01, ha="left", fontsize=11, color=INK)
    # A size legend, because area is hard to read without a reference. Drawn as
    # proxy handles rather than by reserving an axis.
    from matplotlib.lines import Line2D
    ticks = [t for t in (1, round(vmax / 2), round(vmax))
             if pd.notna(t) and t >= 1] if not markers_all.empty else []
    handles = [Line2D([], [], marker="o", linestyle="none", color=ORANGE,
                      alpha=0.7, markeredgecolor=SURFACE,
                      markersize=(16 + 260 * (t / vmax)) ** 0.5,
                      label=f"{int(t)} article" + ("s" if t != 1 else ""))
               for t in dict.fromkeys(ticks)]
    if handles:
        fig.legend(handles=handles, loc="lower right", bbox_to_anchor=(0.99, -0.04),
                   ncols=len(handles), title="NYT articles that month",
                   title_fontsize=8)
    fig.text(0.01, -0.03,
             "One marker per calendar month, placed on that month's last session. "
             "Marker size encodes VOLUME only.\nThe reference image this imitates "
             "encodes sentiment; no sentiment scoring is in scope and none is "
             "derived from counts.",
             fontsize=8, color=INK_SOFT, ha="left", linespacing=1.5)
    fig.tight_layout()
    return fig


def plot_windows(source: str = "twitter"):
    """Figure 4. Paired social post counts, before filing vs after listing.

    A slope chart, because the data is paired and the within-company change is
    the question. Solid lines are pairs where both counts are exact; dashed
    lines are pairs where at least one side hit the page cap and is a lower
    bound, so its slope understates the true change.
    """
    _style()
    path = {"twitter": FIG_WINDOWS, "reddit": FIG_REDDIT_WINDOWS}[source]
    if not path.exists():
        raise SystemExit(f"no {source} window frame; run its collector first.")
    df = pd.read_parquet(path)
    if df.empty:
        raise SystemExit(f"no {source} window pairs.")
    label = {"twitter": "X", "reddit": "Reddit"}[source]
    provider = {"twitter": "twitterapis.com", "reddit": "redditapis.com"}[source]

    fig, ax = plt.subplots(figsize=(8.4, 6.4))
    # Incomparable pairs are EXCLUDED, not drawn faintly. A slope whose gradient
    # is set by an unequal page budget is not weak evidence, it is an artifact,
    # and drawing it invites exactly the reading it cannot support. They are
    # counted in the caption instead.
    drawn = df[df["direction_established"]]
    for _, r in drawn.sort_values("post_listing").iterrows():
        exact = bool(r["magnitude_valid"])
        ax.plot([0, 1], [r["pre_filing"], r["post_listing"]],
                color=BLUE if exact else INK_SOFT,
                linewidth=2 if exact else 1.2,
                linestyle="-" if exact else (0, (4, 3)),
                alpha=1.0 if exact else 0.5,
                marker="o", markersize=6 if exact else 5,
                markeredgecolor=SURFACE, markeredgewidth=0.8, zorder=3 if exact else 2)
        if exact:
            ax.annotate(f" {r['company'][:20]}", (1, r["post_listing"]),
                        fontsize=7.5, color=INK, va="center")

    ax.set_xticks([0, 1])
    ax.set_xticklabels([f"90 days before\npublic S-1", f"90 days after\nlisting"])
    ax.set_xlim(-0.15, 1.55)
    ax.set_ylabel('posts matching \'"<company>" IPO\'')
    ax.set_title(f"Figure 4 — {label} chatter before filing vs after listing",
                 loc="left")
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)

    from matplotlib.lines import Line2D
    handles = [Line2D([], [], color=BLUE, linewidth=2, marker="o",
                      label="both counts exact")]
    # Only offered when such a line is actually drawn. A legend entry with no
    # corresponding mark tells the reader to look for something that is not there.
    if (~drawn["magnitude_valid"]).any():
        handles.append(Line2D([], [], color=INK_SOFT, linewidth=1.2,
                              linestyle=(0, (4, 3)), alpha=0.5, marker="o",
                              label="direction established, magnitude not "
                                    "(one side a floor)"))
    ax.legend(handles=handles, loc="upper left")

    n_exact = int(df["magnitude_valid"].sum())
    rose_exact = int(df.loc[df["magnitude_valid"], "rose"].sum())
    n_excluded = int((~df["direction_established"]).sum())
    # Assembled as a list and joined. A conditional clause spliced into a run of
    # adjacent f-strings is a syntax error, and the earlier version of this was.
    lines = [
        f"{len(drawn)} pairs drawn of {len(df)} usable — every one of them rises. "
        f"Among the {n_exact} with two exact counts, {rose_exact} of "
        f"{n_exact} rose.",
        "Counts are treated as intervals: exact c is [c, c], a censored count c "
        "is [c, INF). A pair is drawn only where one floor clears the other's "
        "ceiling.",
    ]
    if n_excluded:
        lines.append(f"{n_excluded} pairs are excluded on that test — their "
                     f"intervals overlap, so no direction follows either way.")
        lines.append("Dashed = one side is a floor, so the slope is a lower "
                     "bound on the rise, not its size.")
    lines.append(f"Source: {provider}, an unofficial scraper, on one narrow "
                 f"query — not a count of all mentions.")
    fig.text(0.01, -0.13, "\n".join(lines),
             fontsize=8, color=INK_SOFT, ha="left", linespacing=1.5)
    fig.tight_layout()
    return fig


def plot_underpricing():
    """Figure 5. Abnormal pre-listing attention vs first-day underpricing.

    One series, so no legend: the title names it and the points are directly
    labelled. Attention is on a log axis because it is a ratio spanning 0.9x to
    5.2x, and a linear axis would compress ten of the twelve points into a
    third of the width.

    The line is drawn only if the rank correlation survives -- and it is drawn
    as a rank-order guide, not a fitted regression, because the Pearson
    coefficient here is near zero while Spearman is moderate: the relationship
    is monotonic but not linear, and a least-squares line would misrepresent it.
    """
    import numpy as np

    _style()
    df = pd.read_parquet(FIG_UNDERPRICING).dropna(
        subset=["abnormal_attention", "underpricing"])
    if df.empty:
        raise SystemExit("no underpricing rows; run collect.underpricing first.")

    fig, ax = plt.subplots(figsize=(9, 5.8))
    x = df["abnormal_attention"].astype(float)
    y = df["underpricing"].astype(float) * 100

    ax.axhline(0, color=INK_SOFT, linewidth=1, zorder=1)
    ax.scatter(x, y, s=90, color=BLUE, alpha=0.85, edgecolor=SURFACE,
               linewidth=1.2, zorder=3)

    # Labels are nudged apart and flipped to the left near the right edge.
    # Without this Fervo and Firefly overprint each other and CoreWeave's label
    # runs off the axis.
    span = float(y.max() - y.min()) or 1.0
    pts = sorted(zip(x.tolist(), y.tolist(), df["company"].tolist()),
                 key=lambda t: t[1])
    x_hi = float(x.max())
    last_y = None
    for px_, py, name in pts:
        dy = 0.0
        if last_y is not None and abs(py - last_y) < span * 0.035:
            dy = span * 0.035 - (py - last_y)
        right_edge = px_ > x_hi * 0.75
        ax.annotate(f"{name[:20]}  " if right_edge else f"  {name[:20]}",
                    (px_, py), xytext=(0, dy * 0.9), textcoords="offset points",
                    fontsize=7.5, color=INK, va="center",
                    ha="right" if right_edge else "left", zorder=4)
        last_y = py + dy

    rho = x.rank().corr(y.rank())
    pearson = x.corr(y)
    n = len(x)
    z, se = np.arctanh(rho), 1 / np.sqrt(n - 3)
    lo, hi = np.tanh(z - 1.96 * se), np.tanh(z + 1.96 * se)

    ax.set_xscale("log")
    ax.set_xticks([1, 1.5, 2, 3, 5])
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    # A log axis draws its own minor labels ("4 x 10^0") alongside the explicit
    # ticks, which reads as two different scales on one axis.
    ax.get_xaxis().set_minor_formatter(matplotlib.ticker.NullFormatter())
    # Head-room on the right so the rightmost label has somewhere to sit.
    ax.set_xlim(float(x.min()) * 0.82, float(x.max()) * 1.18)
    ax.set_xlabel("abnormal attention: mean daily Wikipedia views in the 30 days "
                  "before listing,\ndivided by the same company's days -120 to -31")
    ax.set_ylabel("first-day underpricing (%)")
    ax.set_title("Figure 5 — pre-listing attention vs IPO underpricing", loc="left")
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)

    caption = [
        f"n={n}. Spearman rho = {rho:+.3f}, 95% CI "
        f"[{lo:+.3f}, {hi:+.3f}]"
        + ("  — the interval spans zero." if lo < 0 < hi else "."),
        f"Pearson r = {pearson:+.3f}. The gap between the two says the relation is "
        f"monotonic but not linear, so no fitted line is drawn.",
        "Attention is measured strictly BEFORE the offer price is set (the "
        "evening before the first trade), so it cannot be an effect of the "
        "outcome.",
        f"At n={n} only |r| >= {1.96 / np.sqrt(n - 2 + 1.96 ** 2):.2f} could reach "
        f"significance. This is underpowered by construction — see the README.",
    ]
    fig.text(0.01, -0.19, "\n".join(caption), fontsize=8, color=INK_SOFT,
             ha="left", linespacing=1.5)
    fig.tight_layout()
    return fig
