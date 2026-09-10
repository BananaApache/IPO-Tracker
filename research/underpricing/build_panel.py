"""
Build the IPO panel. Resumable: every stage is cached per firm, so re-running
after an interruption only does the work that is still missing.

    uv run --with openpyxl python build_panel.py [--limit N] [--stage all]

Stages run cheapest-first so the expensive Wikipedia lookups are only spent
on firms that already have both a price and an offer.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pipeline as P

STATE = P.OUT / "panel.json"
LOG = P.OUT / "build.log"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def key_of(f):
    return f"{f['ticker']}_{f['ipo_date']}"


def stage_price(f):
    return P.cached("price", key_of(f),
                    lambda: P.first_day_close(f["ticker"], f["ipo_date"]))


def stage_offer(f):
    def go():
        if not f.get("cik"):
            return None
        pro = P.find_prospectus(f["cik"], f["ipo_date"])
        if not pro:
            return None
        price, shares = P.parse_offer(pro["url"])
        return {**pro, "offer_price": price, "shares_offered": shares}
    return P.cached("offer", key_of(f), go)


def stage_wiki(f):
    return P.cached("wiki", key_of(f),
                    lambda: P.wikipedia_flag(f["name"], f["ipo_date"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    univ = P.load_universe(2014, 2024)
    if args.limit:
        univ = univ[:args.limit]
    log(f"universe: {len(univ)} IPOs (2014-2024, non-ADR, non-SPAC)")

    # --- CIK (local tables, no network) ---
    hits = 0
    for f in univ:
        cik, how = P.resolve_cik(f)
        f["cik"], f["cik_via"] = cik, how
        hits += bool(cik)
    log(f"CIK resolved: {hits}/{len(univ)} ({hits/len(univ):.0%})")

    # --- prices (1 request each) ---
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(stage_price, f): f for f in univ}
        for fu in as_completed(futs):
            f = futs[fu]
            try:
                f["price"] = fu.result()
            except Exception as e:  # noqa: BLE001
                f["price"] = None
                log(f"  price error {f['ticker']}: {e}")
            done += 1
            if done % 250 == 0:
                log(f"  prices {done}/{len(univ)}")
    with_price = [f for f in univ if f.get("price")]
    log(f"first-day close: {len(with_price)}/{len(univ)} ({len(with_price)/len(univ):.0%})")

    # --- offer price (2 requests each), only where a price exists ---
    todo = [f for f in with_price if f.get("cik")]
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(stage_offer, f): f for f in todo}
        for fu in as_completed(futs):
            f = futs[fu]
            try:
                f["offer"] = fu.result()
            except Exception as e:  # noqa: BLE001
                f["offer"] = None
                log(f"  offer error {f['ticker']}: {e}")
            done += 1
            if done % 100 == 0:
                log(f"  offers {done}/{len(todo)}")
    with_offer = [f for f in todo if f.get("offer") and f["offer"].get("offer_price")]
    log(f"offer price parsed: {len(with_offer)}/{len(todo)} ({len(with_offer)/max(len(todo),1):.0%})")

    # --- Wikipedia (the expensive stage), only for complete rows ---
    done = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(stage_wiki, f): f for f in with_offer}
        for fu in as_completed(futs):
            f = futs[fu]
            try:
                f["wikiinfo"] = fu.result()
            except Exception as e:  # noqa: BLE001
                f["wikiinfo"] = None
                log(f"  wiki error {f['ticker']}: {e}")
            done += 1
            if done % 100 == 0:
                log(f"  wiki {done}/{len(with_offer)}")

    # --- assemble ---
    rows = []
    for f in with_offer:
        w = f.get("wikiinfo") or {}
        offer = f["offer"]["offer_price"]
        close = f["price"]["close"]
        rows.append({
            "ticker": f["ticker"], "name": f["name"], "ipo_date": f["ipo_date"],
            "year": f["year"], "cik": f["cik"], "vc": f["vc"],
            "founding": f["founding"],
            "sic": f["offer"].get("sic"), "sic_desc": f["offer"].get("sic_desc"),
            "form": f["offer"].get("form"), "filed": f["offer"].get("filed"),
            "prospectus_url": f["offer"].get("url"),
            "offer_price": offer, "shares_offered": f["offer"].get("shares_offered"),
            "close": close, "split_factor": f["price"].get("split_factor"),
            "underpricing": (close - offer) / offer * 100.0,
            "wiki": w.get("wiki"), "wiki_status": w.get("status"),
            "wiki_title": w.get("title"), "wiki_created": w.get("created"),
            "wiki_words": w.get("words"), "wiki_overlap": w.get("overlap"),
            "wiki_via": w.get("via"), "wiki_reason": w.get("reason"),
            "wiki_would_be": w.get("would_be"), "wiki_extract": w.get("extract"),
        })
    STATE.write_text(json.dumps(rows, indent=1))
    log(f"WROTE {STATE} with {len(rows)} rows")

    acc = sum(1 for r in rows if r["wiki_status"] == "accept")
    amb = sum(1 for r in rows if r["wiki_status"] == "ambiguous")
    err = sum(1 for r in rows if r["wiki_status"] is None or r["wiki_reason"] == "api_error")
    log(f"wiki: accept={acc} ambiguous={amb} api_error={err}")
    log(f"wiki==1 among accepted: {sum(1 for r in rows if r['wiki'] == 1)}")


if __name__ == "__main__":
    sys.exit(main())
