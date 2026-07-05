"""Standalone S&P 500 screener — run manually:  python run_screener.py

Fetches the current S&P 500 constituent list, runs a LIGHTWEIGHT valuation on
each (intrinsic value consensus, margin of safety, F-score — NO LLM calls), and
writes backend/screener_cache.json with a timestamp. Individual ticker failures
are logged and skipped; the run never aborts on one bad ticker. Results are
saved incrementally so a partial run is still usable.

Set SCREENER_LIMIT=N to process only the first N tickers (useful for testing).
"""
import os
import ssl
import json
import time
import datetime
import urllib.request

from routers import analyze as analyze_mod  # reuse the analyze() pipeline directly

# Public read-only fetch; tolerate the Windows cert-store SSL quirk.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

CACHE_PATH = os.path.join(os.path.dirname(__file__), "screener_cache.json")


def get_sp500_tickers():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        html = resp.read().decode("utf-8", "ignore")
    import pandas as pd
    tables = pd.read_html(html)
    df = tables[0]
    # yfinance uses '-' where S&P uses '.', e.g. BRK.B -> BRK-B
    return [str(s).replace(".", "-").strip() for s in df["Symbol"].tolist()]


def save(results):
    out = {
        "last_updated": datetime.datetime.utcnow().isoformat() + "Z",
        "count": len(results),
        "results": results,
    }
    with open(CACHE_PATH, "w") as f:
        json.dump(out, f)


def run():
    tickers = get_sp500_tickers()
    limit = os.getenv("SCREENER_LIMIT")
    if limit:
        tickers = tickers[: int(limit)]
    total = len(tickers)
    print(f"Screening {total} tickers...", flush=True)

    results = []
    ok = fail = 0
    for i, t in enumerate(tickers, 1):
        try:
            d = analyze_mod.analyze(t)
            iv = (d.get("intrinsic_value") or {}).get("consensus")
            results.append({
                "ticker": t,
                "company_name": d.get("company_name"),
                "current_price": d.get("current_price"),
                "intrinsic_value": iv,
                "margin_of_safety_pct": d.get("margin_of_safety_pct"),
                "f_score": d.get("f_score"),
                # Confidence matters most at the extremes: sorting by margin of
                # safety floats the model's boldest (least certain) calls to the
                # top, so the UI must be able to badge them.
                "confidence": d.get("confidence"),
                "moat_score": (d.get("valuation_engine") or {}).get("moat_score"),
            })
            ok += 1
            print(f"[{i}/{total}] {t} OK", flush=True)
        except Exception as e:
            fail += 1
            print(f"[{i}/{total}] {t} SKIP: {str(e)[:80]}", flush=True)
        # Save incrementally so a crash/partial run isn't lost
        if i % 10 == 0:
            save(results)
        time.sleep(0.05)

    save(results)
    print(f"Done. {ok} succeeded, {fail} skipped. Wrote {len(results)} to {CACHE_PATH}", flush=True)


if __name__ == "__main__":
    run()
