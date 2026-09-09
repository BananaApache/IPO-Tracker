"""Render the analysis figures to PNGs for the README.

Reuses `research.figures` rather than reimplementing any plotting, so a README
image cannot drift from what `notebooks/02_analysis.ipynb` produces. Rerun after
changing a figure or collecting more data:

    uv run --group research python -m research.render_figures
"""

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"
logger = logging.getLogger(__name__)


def main() -> int:
    import warnings

    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--company", default=None,
                    help="which company for the per-company panel figure")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    import pandas as pd

    from research import figures

    OUT.mkdir(parents=True, exist_ok=True)
    figures.build_frames()

    # Pick the richest company for the per-company panel: the one with the most
    # non-null observations across every series, so the figure shows all panels
    # populated rather than an empty one.
    panels = pd.read_parquet(figures.FIG_PANELS)
    if args.company:
        company = args.company
    else:
        have = (panels[panels["value"].notna()]
                .groupby("company")["series"].nunique().sort_values())
        rich = have[have == have.max()].index.tolist()
        company = rich[0] if rich else panels["company"].iloc[0]
    logger.info("per-company panel: %s", company)

    jobs = [
        ("abnormal-attention", lambda: figures.plot_abnormal_attention()),
        ("cohort-comparison", lambda: figures.plot_cohort_comparison()),
        ("company-panels", lambda: figures.plot_company(company)),
        ("relative-time-drs", lambda: figures.plot_relative_time(
            "wikipedia_views", anchor="drs")),
        ("relative-time-listing", lambda: figures.plot_relative_time(
            "wikipedia_views", anchor="listing")),
        ("windows-twitter", lambda: figures.plot_windows("twitter")),
        ("windows-reddit", lambda: figures.plot_windows("reddit")),
        ("underpricing", lambda: figures.plot_underpricing()),
        ("post-listing", lambda: figures.plot_post_listing()),
    ]
    written = 0
    for name, fn in jobs:
        try:
            fig = fn()
        except Exception as exc:
            # A figure with no data yet is skipped, not fatal: the price
            # collection for the notability study runs for hours.
            logger.warning("%-22s skipped: %s", name, str(exc)[:80])
            continue
        path = OUT / f"{name}.png"
        fig.savefig(path, dpi=args.dpi, bbox_inches="tight",
                    facecolor=figures.SURFACE)
        matplotlib.pyplot.close(fig)
        written += 1
        logger.info("%-22s -> %s (%.0f KB)", name, path.name,
                    path.stat().st_size / 1024)
    logger.info("wrote %d figures to %s", written, OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
