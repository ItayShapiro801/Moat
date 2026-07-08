"""Finnhub — third-tier resilience provider.

Chain: yfinance (if reachable) -> FMP (primary, full valuation) -> Finnhub (last
resort). yfinance is an unofficial scraper that gets cloud IPs blocked; FMP has a
250/day cap; Finnhub has a 60/min cap. Combining a daily cap and a per-minute cap
gives more combined headroom than either alone.

Finnhub's free fundamentals coverage is thinner than FMP's, so this layer is
intentionally DEGRADED: it provides price/name/sector and whatever ratios the
free `/stock/metric` endpoint exposes, with the same null-if-missing rule as the
FMP mapper. It does not reconstruct the full multi-year DCF/F-Score pipeline.

All functions return None (never raise) when the key is unset or Finnhub can't
provide the essentials, so the caller can surface its own error.
"""
from __future__ import annotations

import json as json_mod
import urllib.request

from config import FINNHUB_API_KEY

FINNHUB_BASE = "https://finnhub.io/api/v1"


def _get(path: str):
    """GET a Finnhub endpoint, return parsed JSON or None. Never raises."""
    if not FINNHUB_API_KEY:
        return None
    sep = "&" if "?" in path else "?"
    url = f"{FINNHUB_BASE}/{path}{sep}token={FINNHUB_API_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json_mod.loads(resp.read())
    except Exception:
        return None


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _quote_price(ticker: str):
    q = _get(f"quote?symbol={ticker}")
    if isinstance(q, dict):
        c = _num(q.get("c"))  # current price
        return c if c else None
    return None


def analyst_recommendation(ticker: str) -> dict | None:
    """Real Wall-Street analyst consensus from Finnhub (free tier), or None.

    Returns the latest recommendation-trend counts + a derived consensus rating:
      {"strong_buy": 13, "buy": 23, "hold": 16, "sell": 2, "strong_sell": 0,
       "total": 54, "consensus": "Buy", "score": 4.15, "period": "2026-07-01"}
    score is 1 (Strong Sell) .. 5 (Strong Buy). Finnhub's free plan does NOT
    include price targets (premium), so we report the rating consensus only —
    accurate and matching what public sites show, unlike a fabricated target."""
    data = _get(f"stock/recommendation?symbol={ticker}")
    if not isinstance(data, list) or not data:
        return None
    r = data[0]  # most recent period, newest first
    sb = int(r.get("strongBuy") or 0)
    b = int(r.get("buy") or 0)
    h = int(r.get("hold") or 0)
    s = int(r.get("sell") or 0)
    ss = int(r.get("strongSell") or 0)
    total = sb + b + h + s + ss
    if total == 0:
        return None
    score = (sb * 5 + b * 4 + h * 3 + s * 2 + ss * 1) / total
    if score >= 4.5:
        consensus = "Strong Buy"
    elif score >= 3.5:
        consensus = "Buy"
    elif score >= 2.5:
        consensus = "Hold"
    elif score >= 1.5:
        consensus = "Sell"
    else:
        consensus = "Strong Sell"
    return {
        "strong_buy": sb, "buy": b, "hold": h, "sell": s, "strong_sell": ss,
        "total": total, "consensus": consensus, "score": round(score, 2),
        "period": r.get("period"),
    }


def analyze_fallback(ticker: str) -> dict | None:
    """Degraded /analyze from Finnhub: price/name/sector, valuation fields null.

    Works for ETFs/indices/crypto too (Finnhub is free & uncapped and, unlike
    EDGAR, covers non-equities). ETFs are detected so the portfolio can add them
    as price-only holdings even when every full-data provider is exhausted."""
    price = _quote_price(ticker)
    profile = _get(f"stock/profile2?symbol={ticker}")
    profile = profile if isinstance(profile, dict) else {}
    if price is None and not profile:
        return None
    # Finnhub sets no clean asset-class flag; infer ETF from the profile type or a
    # missing market-cap/industry combined with a valid quote (typical of funds).
    ptype = str(profile.get("type") or "").upper()
    quote_type = "ETF" if ("ETF" in ptype or "FUND" in ptype) else "EQUITY"
    return {
        "ticker": ticker,
        "company_name": profile.get("name") or ticker,
        "current_price": round(price, 2) if price is not None else None,
        "quote_type": quote_type,
        "currency": profile.get("currency") or "USD",
        "intrinsic_value": {
            "bear": {"value": None}, "base": {"value": None},
            "bull": {"value": None}, "consensus": None, "partial": False,
        },
        "margin_of_safety_pct": None,
        "confidence": None,
        "valuation_note": (
            "Live valuation is temporarily unavailable (primary data sources "
            "unreachable). Showing basic quote data from a backup source."
        ),
        "f_score": None,
        "revenue_5yr": [],
        "fcf_5yr": [],
        "valuation_breakdown": None,
        "dcf_breakdown": None,
        "data_source": "finnhub_fallback",
    }


def price_history_fallback(ticker: str, period: str) -> dict | None:
    """Finnhub historical candles. NOTE: /stock/candle is premium-gated on many
    free plans; returns None (null, not a crash) when unavailable."""
    import time
    span_days = {"1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "5y": 1830, "max": 3650}
    now = int(time.time())
    frm = now - span_days.get(period, 366) * 86400
    data = _get(f"stock/candle?symbol={ticker}&resolution=D&from={frm}&to={now}")
    if not isinstance(data, dict) or data.get("s") != "ok":
        return None
    closes = data.get("c") or []
    times = data.get("t") or []
    if not closes or not times:
        return None
    import datetime
    dates, prices = [], []
    for ts, c in zip(times, closes):
        cv = _num(c)
        if cv is None:
            continue
        dates.append(datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"))
        prices.append(round(cv, 2))
    if not prices:
        return None
    return {"ticker": ticker, "dates": dates, "prices": prices, "data_source": "finnhub_fallback"}


def metrics_fallback(ticker: str) -> dict | None:
    """Best-effort /metrics from Finnhub's free /stock/metric endpoint."""
    data = _get(f"stock/metric?symbol={ticker}&metric=all")
    if not isinstance(data, dict):
        return None
    m = data.get("metric") or {}
    if not m:
        return None

    def r1(v):
        v = _num(v)
        return round(v, 1) if v is not None else None

    def r2(v):
        v = _num(v)
        return round(v, 2) if v is not None else None

    return {
        "ticker": ticker,
        "valuation": {
            "pe_ratio": r1(m.get("peTTM") or m.get("peNormalizedAnnual")),
            "forward_pe": None,
            "pb_ratio": r1(m.get("pbAnnual") or m.get("pbQuarterly")),
            "ev_ebitda": r1(m.get("currentEv/freeCashFlowTTM")),
            "p_fcf": r1(m.get("pfcfShareTTM")),
            "peg_ratio": r2(m.get("pegRatio") or m.get("pegTTM")),
        },
        "dividends": {
            "dividend_yield": r2(m.get("dividendYieldIndicatedAnnual")),
            "annual_dividend": r2(m.get("dividendPerShareAnnual")),
            "payout_ratio": r1(m.get("payoutRatioTTM")),
        },
        "quality": {
            "current_ratio": r2(m.get("currentRatioAnnual") or m.get("currentRatioQuarterly")),
            "quick_ratio": r2(m.get("quickRatioAnnual") or m.get("quickRatioQuarterly")),
            "roic": r1(m.get("roiTTM") or m.get("roaTTM")),
            "profit_margin": r1(m.get("netProfitMarginTTM")),
        },
        "financial_health": {
            "eps_ttm": r2(m.get("epsTTM") or m.get("epsAnnual")),
            "fcf_per_share": r2(m.get("freeCashFlowPerShareTTM")),
            "net_debt_per_share": None,
            "debt_equity": r2(m.get("totalDebt/totalEquityAnnual") or m.get("totalDebt/totalEquityQuarterly")),
            "market_cap": _num(m.get("marketCapitalization")),
        },
        "analyst_ratings": {
            "recommendation": None,
            "num_analysts": None,
            "target_mean_price": None,
            "target_high_price": None,
            "target_low_price": None,
        },
        "data_source": "finnhub_fallback",
    }


def financials_fallback(ticker: str) -> dict | None:
    """Finnhub's free statement coverage is sparse/awkwardly nested; we don't
    attempt to reshape it. Returning None lets the caller degrade to empty
    series rather than emit unreliable data."""
    return None
