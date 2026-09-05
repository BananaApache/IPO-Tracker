"""Scores backend.extract.prospectus against the hand-labelled set.

    uv run python -m tests.evaluate_extraction
"""

import json
import pathlib
from decimal import Decimal

from backend.extract.prospectus import extract
from backend.extract.underwriters import match_underwriter
import sys

from tests.fixtures.extraction_labels import LABELS as DEV_LABELS
from tests.fixtures.extraction_labels_holdout import LABELS as HOLDOUT_LABELS

SCRATCH = pathlib.Path(
    "/private/tmp/claude-501/-Users-daniel-Documents-coding-stuff-IPOTracker/"
    "1fbd3569-2463-4bbf-a251-86351352c9e6/scratchpad"
)
SETS = {"dev": (SCRATCH / "eval", DEV_LABELS), "holdout": (SCRATCH / "holdout", HOLDOUT_LABELS)}


def _dec(value):
    return None if value is None else Decimal(str(value))


def main(which: str = "dev") -> None:
    EVAL_DIR, LABELS = SETS[which]
    print(f"\n########## {which.upper()} SET ({len(LABELS)} filings) ##########")
    manifest = {m["slug"]: m for m in json.loads((EVAL_DIR / "manifest.json").read_text())}

    disclosure_ok = price_value_ok = 0
    tp = fp = fn = 0
    dict_covered = dict_missing = 0
    rows = []

    for slug, label in LABELS.items():
        text = (EVAL_DIR / f"{slug}.txt").read_text()
        result = extract(text)

        d_ok = result.price.disclosure == label["price_disclosure"]
        v_ok = (
            result.price.price_low == _dec(label["price_low"])
            and result.price.price_high == _dec(label["price_high"])
            and result.price.price_final == _dec(label["price_final"])
        )
        disclosure_ok += d_ok
        price_value_ok += v_ok

        # Underwriters: compare canonical forms so a dictionary alias is not
        # scored as a miss.
        truth = {c for c in (match_underwriter(n) for n in label["underwriters"]) if c}
        dict_covered += len(truth)
        dict_missing += len(label["underwriters"]) - len(truth)
        predicted = {u.name for u in result.underwriters}
        tp += len(truth & predicted)
        fp += len(predicted - truth)
        fn += len(truth - predicted)

        rows.append({
            "slug": slug,
            "name": manifest[slug]["legal_name"][:26],
            "form": manifest[slug]["form_type"],
            "d_ok": d_ok, "v_ok": v_ok,
            "got": result.price.disclosure, "want": label["price_disclosure"],
            "method": result.price.method,
            "uw_tp": len(truth & predicted), "uw_fp": len(predicted - truth),
            "uw_fn": len(truth - predicted),
            "missed": sorted(truth - predicted), "spurious": sorted(predicted - truth),
        })

    n = len(LABELS)
    print(f"{'slug':22} {'form':7} {'company':27} {'disc':5} {'val':4} {'method':22} uw(tp/fp/fn)")
    print("-" * 118)
    for r in rows:
        print(f"{r['slug']:22} {r['form']:7} {r['name']:27} "
              f"{'ok' if r['d_ok'] else 'MISS':5} {'ok' if r['v_ok'] else 'MISS':4} "
              f"{r['method']:22} {r['uw_tp']}/{r['uw_fp']}/{r['uw_fn']}")
        if r["missed"]:
            print(f"{'':58} missed:   {', '.join(r['missed'])[:70]}")
        if r["spurious"]:
            print(f"{'':58} spurious: {', '.join(r['spurious'])[:70]}")

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    print("\n" + "=" * 60)
    print(f"PRICE   disclosure class correct : {disclosure_ok}/{n}  ({disclosure_ok/n:.0%})")
    print(f"        price values correct     : {price_value_ok}/{n}  ({price_value_ok/n:.0%})")
    print(f"UNDERWRITERS  tp={tp} fp={fp} fn={fn}")
    print(f"        precision {precision:.2f}   recall {recall:.2f}   F1 {f1:.2f}")
    total_banks = dict_covered + dict_missing
    if total_banks:
        print(f"        end-to-end recall (incl. banks absent from the dictionary):"
              f" {tp}/{total_banks} = {tp/total_banks:.2f}")
        print(f"DICTIONARY coverage of labelled banks: {dict_covered}/{total_banks}"
              f"  ({dict_covered/total_banks:.0%})")
    print("=" * 60)


if __name__ == "__main__":
    for name in (sys.argv[1:] or ["dev", "holdout"]):
        main(name)
