"""FMP fallback for when yfinance rate-limits.

yfinance is the primary, always-live source. When it throttles (HTTP 429 /
"Too Many Requests", or simply returns empty data), the data endpoints retry the
same request against Financial Modeling Prep's equivalent endpoints and reshape
the result into the SAME response shape the endpoint normally returns.

Design rules (per product spec):
- yfinance, when working, is NEVER cached — always live.
- A SHORT 60s in-memory cache applies ONLY to FMP fallback responses, keyed by
  ticker (+ request params), to protect FMP's 250/day free-tier budget when many
  users hit the same rate-limited ticker in a short window.
- Every served request logs its source: "yfinance" or "fmp_fallback".
- If a field isn't available from FMP, it is returned as null rather than crashing.
"""
from __future__ import annotations

import time
import threading
import json as json_mod
import urllib.request
import urllib.error

import pandas as pd

from config import FMP_API_KEY, FMP_API_KEYS

FMP_BASE = "https://financialmodelingprep.com/stable"
FALLBACK_CACHE_TTL = 60  # seconds; fallback path only

# (key) -> (timestamp, value). Separate from the LLM cache; fallback responses only.
_FALLBACK_CACHE: dict[str, tuple] = {}


# ---------------------------------------------------------------------------
# Rate-limit detection
# ---------------------------------------------------------------------------

_RATE_LIMIT_MARKERS = ("429", "too many requests", "rate limit", "rate-limit", "yfratelimit")


def is_rate_limited(exc: Exception) -> bool:
    """True when an exception looks like a yfinance throttle."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _RATE_LIMIT_MARKERS)


# ---------------------------------------------------------------------------
# Source logging
# ---------------------------------------------------------------------------

def log_source(endpoint: str, ticker: str, source: str) -> None:
    """Record which source served a request (yfinance | fmp_fallback)."""
    print(f"[data-source] {endpoint} {ticker} -> {source}", flush=True)


# ---------------------------------------------------------------------------
# Fallback-only cache
# ---------------------------------------------------------------------------

def _cache_get(key: str):
    entry = _FALLBACK_CACHE.get(key)
    if entry and (time.time() - entry[0]) < FALLBACK_CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value) -> None:
    _FALLBACK_CACHE[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# Low-level FMP fetch
# ---------------------------------------------------------------------------

# --- Multi-key rotation ---------------------------------------------------
# Each FMP key has its own 250/day cap. We drain one key until it reports a
# rate-limit, then advance to the next — so N keys give ~N*250 calls/day. A
# capped key is put on a cooldown (re-tried later, since the daily cap resets)
# instead of being retried on every request.
_KEY_COOLDOWN = 30 * 60  # seconds before re-trying a capped key
_key_lock = threading.Lock()
_key_spent_until: dict[int, float] = {}  # key index -> epoch it may be retried
_key_cursor = [0]                        # start-from index (advances as keys cap)


def _is_fmp_limit_body(data) -> bool:
    """FMP sometimes returns HTTP 200 with a limit-reached error object."""
    if isinstance(data, dict):
        msg = str(data.get("Error Message") or data.get("error") or data.get("message") or "").lower()
        return "limit" in msg or "upgrade" in msg
    return False


def _mark_spent(i: int) -> None:
    with _key_lock:
        _key_spent_until[i] = time.time() + _KEY_COOLDOWN
        if FMP_API_KEYS:
            _key_cursor[0] = (i + 1) % len(FMP_API_KEYS)


def _key_order():
    """Indices of currently-usable keys, starting from the active cursor."""
    now = time.time()
    n = len(FMP_API_KEYS)
    return [
        (_key_cursor[0] + off) % n
        for off in range(n)
        if _key_spent_until.get((_key_cursor[0] + off) % n, 0) <= now
    ]


def _fmp_get(path: str):
    """GET an FMP `stable` endpoint, rotating across API keys on rate-limit.
    Returns parsed JSON or None. Never raises."""
    if not FMP_API_KEYS:
        return None
    sep = "&" if "?" in path else "?"
    for i in _key_order():
        url = f"{FMP_BASE}/{path}{sep}apikey={FMP_API_KEYS[i]}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json_mod.loads(resp.read())
            if _is_fmp_limit_body(data):
                _mark_spent(i)          # this key is capped -> try the next one
                continue
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 402, 403):
                _mark_spent(i)          # rate-limited / quota -> next key
                continue
            return None                 # other HTTP error -> don't burn more keys
        except Exception:
            return None                 # network/timeout -> give up
    return None                         # all keys exhausted for now


def _first(data):
    """FMP list endpoints return an array; grab the first row or None."""
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def _cross_validate_price(ticker: str, price, quote: dict):
    """Guard against a stale FMP quote by cross-checking Finnhub (free, live feed).

    Observed in production: FMP's free-tier quote for MU was months stale ($166 vs
    the real $975 after a huge run-up), which silently poisoned every downstream
    number (P/E, margin of safety, the whole valuation). When the two feeds
    disagree by >25%, prefer the one with the FRESHER exchange timestamp; if
    timestamps aren't comparable, prefer Finnhub (it matched the live market in
    the observed failure). Agreeing feeds (the normal case) return FMP's price."""
    if not price:
        return price
    try:
        from services import finnhub_fallback as FH
        q = FH._get(f"quote?symbol={ticker}") or {}
        fh_price = _num(q.get("c"))
        fh_ts = _num(q.get("t"))
    except Exception:
        return price
    if not fh_price or fh_price <= 0:
        return price
    if abs(price - fh_price) / max(price, fh_price) <= 0.25:
        return price  # feeds agree — normal case
    fmp_ts = _num(quote.get("timestamp"))
    print(f"[price-check] {ticker}: FMP={price} vs Finnhub={fh_price} diverge >25%; "
          f"using {'fresher' if (fh_ts and fmp_ts) else 'Finnhub'} quote", flush=True)
    if fh_ts and fmp_ts:
        return fh_price if fh_ts >= fmp_ts else price
    return fh_price


def _num(v):
    """Coerce to float or None (FMP sometimes returns "", 0, or missing keys)."""
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Endpoint-shaped fallbacks. Each returns the SAME shape as the live endpoint,
# or None if FMP couldn't provide the essentials (caller then surfaces the error).
# ---------------------------------------------------------------------------

def analyze_fallback(ticker: str) -> dict | None:
    """Degraded /analyze: price, name, sector, currency from FMP; valuation
    fields null with a note. (Full valuation needs multi-year statements the
    free tier rate-limits; intentionally not reconstructed here.)"""
    cache_key = f"analyze:{ticker}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    quote = _first(_fmp_get(f"quote?symbol={ticker}"))
    profile = _first(_fmp_get(f"profile?symbol={ticker}"))
    if not quote and not profile:
        return None

    quote = quote or {}
    profile = profile or {}
    price = _num(quote.get("price"))
    if price is None:
        price = _num(profile.get("price"))
    price = _cross_validate_price(ticker, price, quote)
    name = profile.get("companyName") or quote.get("name") or ticker
    sector = profile.get("sector") or ""
    currency = profile.get("currency") or "USD"

    payload = {
        "ticker": ticker,
        "company_name": name,
        "current_price": round(price, 2) if price is not None else None,
        "quote_type": "EQUITY",
        "currency": currency,
        "intrinsic_value": {
            "bear": {"value": None}, "base": {"value": None},
            "bull": {"value": None}, "consensus": None, "partial": False,
        },
        "margin_of_safety_pct": None,
        "confidence": None,
        "valuation_note": (
            "Live valuation is temporarily unavailable (primary data source "
            "rate-limited). Showing basic quote data from the fallback source."
        ),
        "f_score": None,
        "revenue_5yr": [],
        "fcf_5yr": [],
        "valuation_breakdown": None,
        "dcf_breakdown": None,
        "data_source": "fmp_fallback",
    }
    _cache_set(cache_key, payload)
    return payload


def price_history_fallback(ticker: str, period: str) -> dict | None:
    cache_key = f"price-history:{ticker}:{period}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # FMP "light" historical endpoint returns [{date, price}, ...], newest first.
    data = _fmp_get(f"historical-price-eod/light?symbol={ticker}")
    rows = data if isinstance(data, list) else None
    if not rows:
        return None

    # Trim to the requested window (approx; FMP returns a long history).
    span_days = {"1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "5y": 1830, "max": 10**6}
    limit = span_days.get(period, 366)
    rows = rows[:limit]

    dates, prices = [], []
    for r in reversed(rows):  # oldest -> newest for charting
        p = _num(r.get("price"))
        d = r.get("date")
        if p is None or not d:
            continue
        dates.append(str(d)[:10])
        prices.append(round(p, 2))
    if not prices:
        return None

    payload = {"ticker": ticker, "dates": dates, "prices": prices, "data_source": "fmp_fallback"}
    _cache_set(cache_key, payload)
    return payload


def _statement_series(rows, field):
    """Map an FMP statement array -> [{year, value}] oldest-first, like yfinance."""
    out = []
    for r in rows or []:
        val = _num(r.get(field))
        yr = r.get("calendarYear") or (str(r.get("date", ""))[:4] or None)
        if val is None or not yr:
            continue
        out.append({"year": str(yr), "value": val})
    out.reverse()  # FMP is newest-first; endpoints return oldest-first
    return out


def financials_fallback(ticker: str) -> dict | None:
    cache_key = f"financials:{ticker}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    inc = _fmp_get(f"income-statement?symbol={ticker}&limit=5")
    cf = _fmp_get(f"cash-flow-statement?symbol={ticker}&limit=5")
    bs = _fmp_get(f"balance-sheet-statement?symbol={ticker}&limit=5")
    inc = inc if isinstance(inc, list) else []
    cf = cf if isinstance(cf, list) else []
    bs = bs if isinstance(bs, list) else []
    if not inc and not cf and not bs:
        return None

    payload = {
        "ticker": ticker,
        "revenue": _statement_series(inc, "revenue"),
        "eps": _statement_series(inc, "eps"),
        "fcf": _statement_series(cf, "freeCashFlow"),
        "gross_profit": _statement_series(inc, "grossProfit"),
        "operating_income": _statement_series(inc, "operatingIncome"),
        "net_income": _statement_series(inc, "netIncome"),
        "shares_outstanding": _statement_series(inc, "weightedAverageShsOut"),
        "data_source": "fmp_fallback",
    }
    _cache_set(cache_key, payload)
    return payload


def metrics_fallback(ticker: str) -> dict | None:
    cache_key = f"metrics:{ticker}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    quote = _first(_fmp_get(f"quote?symbol={ticker}")) or {}
    ratios = _first(_fmp_get(f"ratios?symbol={ticker}&limit=1")) or {}
    km = _first(_fmp_get(f"key-metrics?symbol={ticker}&limit=1")) or {}
    if not quote and not ratios and not km:
        return None

    def r1(v):
        return round(v, 1) if v is not None else None

    def r2(v):
        return round(v, 2) if v is not None else None

    # Field names per FMP's `stable` API (verified against live responses).
    market_cap = _num(quote.get("marketCap")) or _num(km.get("marketCap"))
    # FMP's stable /quote omits EPS; derive it from PE (price / PE) when possible.
    eps = _num(quote.get("eps"))
    if eps is None:
        price = _num(quote.get("price"))
        pe_val = _num(ratios.get("priceToEarningsRatio"))
        if price is not None and pe_val:
            eps = price / pe_val
    # Stale-quote guard: FMP's ratio endpoints are computed with FMP's own price,
    # so a stale quote (observed: MU at $166 vs the real $975) silently poisons
    # every price-dependent multiple. Those multiples are LINEAR in price, so when
    # the cross-check detects staleness we rescale them exactly. (EPS and other
    # per-share fundamentals don't depend on price and pass through unchanged.)
    _raw_p = _num(quote.get("price"))
    _fresh_p = _cross_validate_price(ticker, _raw_p, quote)
    _scale = (_fresh_p / _raw_p) if (_raw_p and _fresh_p and _raw_p > 0
                                     and abs(_fresh_p - _raw_p) / max(_fresh_p, _raw_p) > 0.25) else 1.0

    def _scaled(v):
        return v * _scale if v is not None else None

    if market_cap is not None:
        market_cap = market_cap * _scale
    div_yield = _num(ratios.get("dividendYield"))
    if div_yield is not None and _scale != 1.0:
        div_yield = div_yield / _scale  # yield moves inversely with price
    payout = _num(ratios.get("dividendPayoutRatio"))
    # The live /metrics endpoint maps "roic" from returnOnAssets; mirror that.
    roa = _num(km.get("returnOnAssets"))
    net_margin = _num(ratios.get("netProfitMargin"))

    payload = {
        "ticker": ticker,
        "valuation": {
            "pe_ratio": r1(_scaled(_num(ratios.get("priceToEarningsRatio")))),
            "forward_pe": None,  # not on FMP free tier
            "pb_ratio": r1(_scaled(_num(ratios.get("priceToBookRatio")))),
            "ev_ebitda": r1(_num(km.get("evToEBITDA")) or _num(ratios.get("enterpriseValueMultiple"))),
            "p_fcf": r1(_scaled(_num(ratios.get("priceToFreeCashFlowRatio")))),
            "peg_ratio": r2(_scaled(_num(ratios.get("priceToEarningsGrowthRatio")))),
        },
        "dividends": {
            "dividend_yield": r2(div_yield * 100) if div_yield is not None else None,
            "annual_dividend": r2(_num(ratios.get("dividendPerShare"))),
            "payout_ratio": r1(payout * 100) if payout is not None else None,
        },
        "quality": {
            "current_ratio": r2(_num(ratios.get("currentRatio"))),
            "quick_ratio": r2(_num(ratios.get("quickRatio"))),
            "roic": r1(roa * 100) if roa is not None else None,
            "profit_margin": r1(net_margin * 100) if net_margin is not None else None,
        },
        "financial_health": {
            "eps_ttm": r2(eps),
            "fcf_per_share": r2(_num(ratios.get("freeCashFlowPerShare"))),
            "net_debt_per_share": None,
            "debt_equity": r2(_num(ratios.get("debtToEquityRatio"))),
            "market_cap": market_cap,
        },
        "analyst_ratings": {
            "recommendation": None,
            "num_analysts": None,
            "target_mean_price": None,
            "target_high_price": None,
            "target_low_price": None,
        },
        "data_source": "fmp_fallback",
    }
    _cache_set(cache_key, payload)
    return payload


# ---------------------------------------------------------------------------
# FMP PRIMARY adapter
#
# yfinance is an unofficial scraper that gets cloud IPs blocked. FMP is an
# official API. To make FMP a *full* primary source (not just a degraded
# fallback) without rewriting the valuation engine, we expose FMP data through
# objects that quack like yfinance: an `info` dict with the same keys the engine
# reads, and a stock-like object whose .financials / .balance_sheet / .cashflow /
# .quarterly_balance_sheet / .history() return pandas DataFrames in yfinance's
# shape (index = row labels, columns = period-end Timestamps, newest-first).
# The entire DCF / F-Score / relative-value / merger-guard pipeline then runs
# unchanged on FMP data.
# ---------------------------------------------------------------------------

# yfinance income-statement row label -> FMP income-statement field
_INC_MAP = {
    "Net Income": "netIncome",
    "Total Revenue": "revenue",
    "Cost Of Revenue": "costOfRevenue",
    "Operating Income": "operatingIncome",
    "EBITDA": "ebitda",
    "Gross Profit": "grossProfit",
}
_BS_MAP = {
    "Total Assets": "totalAssets",
    "Current Assets": "totalCurrentAssets",
    "Current Liabilities": "totalCurrentLiabilities",
    "Long Term Debt": "longTermDebt",
    "Stockholders Equity": "totalStockholdersEquity",
}
_CF_MAP = {
    "Operating Cash Flow": "operatingCashFlow",
    "Capital Expenditure": "capitalExpenditure",
    "Free Cash Flow": "freeCashFlow",
}


def _stmt_df(rows, field_map, shares_from_rows=None):
    """Build a yfinance-shaped DataFrame from an FMP statement array.
    index = row labels, columns = period-end Timestamps (newest-first)."""
    if not rows:
        return pd.DataFrame()
    cols = []
    for r in rows:
        d = r.get("date") or (f"{r.get('fiscalYear')}-12-31" if r.get("fiscalYear") else None)
        cols.append(pd.Timestamp(d) if d else pd.Timestamp("1970-01-01"))
    data = {label: [_num(r.get(f)) for r in rows] for label, f in field_map.items()}
    df = pd.DataFrame(data, index=cols).T  # rows=labels, cols=dates
    if shares_from_rows is not None:
        # Share count per period (FMP reports it on the income statement).
        df.loc["Share Issued"] = [_num(r.get("weightedAverageShsOut")) for r in shares_from_rows]
    return df


class _FmpStock:
    """Minimal yfinance.Ticker stand-in backed by FMP statement arrays."""

    def __init__(self, info, financials, balance_sheet, cashflow, quarterly_bs, prices):
        self.info = info
        self.financials = financials
        self.balance_sheet = balance_sheet
        self.cashflow = cashflow
        self._quarterly_bs = quarterly_bs
        self._prices = prices  # DataFrame indexed by date with a "Close" column

    @property
    def quarterly_balance_sheet(self):
        return self._quarterly_bs

    def history(self, period="5y", **kwargs):
        return self._prices if self._prices is not None else pd.DataFrame()


def build_fmp_bundle(ticker: str):
    """Return (stock_like, info) backed by FMP for the FULL valuation engine,
    or None if FMP can't provide the essentials. Costs ~6 FMP calls (cached for
    the fallback window); the caller should layer the longer valuation cache."""
    quote = _first(_fmp_get(f"quote?symbol={ticker}")) or {}
    profile = _first(_fmp_get(f"profile?symbol={ticker}")) or {}
    inc = _fmp_get(f"income-statement?symbol={ticker}&limit=5")
    bs = _fmp_get(f"balance-sheet-statement?symbol={ticker}&limit=5")
    cf = _fmp_get(f"cash-flow-statement?symbol={ticker}&limit=5")
    inc = inc if isinstance(inc, list) else []
    bs = bs if isinstance(bs, list) else []
    cf = cf if isinstance(cf, list) else []

    price = _num(quote.get("price")) or _num(profile.get("price"))
    price = _cross_validate_price(ticker, price, quote)
    if price is None or not inc:
        return None  # without a price and statements there's no full valuation

    market_cap = _num(quote.get("marketCap")) or _num(profile.get("marketCap"))
    shares = market_cap / price if (market_cap and price) else _num(inc[0].get("weightedAverageShsOut"))

    financials = _stmt_df(inc, _INC_MAP)
    balance_sheet = _stmt_df(bs, _BS_MAP, shares_from_rows=inc)
    cashflow = _stmt_df(cf, _CF_MAP)

    # Quarterly share series for the merger guard (best-effort; free tier allows it).
    quarterly_bs = pd.DataFrame()
    qinc = _fmp_get(f"income-statement?symbol={ticker}&period=quarter&limit=6")
    if isinstance(qinc, list) and qinc:
        quarterly_bs = _stmt_df(qinc, {}, shares_from_rows=qinc)

    # 5y daily closes for relative-value historical multiples.
    prices_df = None
    hist = _fmp_get(f"historical-price-eod/light?symbol={ticker}")
    if isinstance(hist, list) and hist:
        rows = [(pd.Timestamp(str(h.get("date"))[:10]), _num(h.get("price"))) for h in hist if h.get("date")]
        rows = [(d, p) for d, p in rows if p is not None]
        rows.sort(key=lambda x: x[0])  # ascending, like yfinance
        if rows:
            prices_df = pd.DataFrame({"Close": [p for _, p in rows]}, index=[d for d, _ in rows])

    latest_inc = inc[0]
    latest_bs = bs[0] if bs else {}
    latest_cf = cf[0] if cf else {}
    total_debt = _num(latest_bs.get("totalDebt")) or 0
    total_cash = _num(latest_bs.get("cashAndCashEquivalents")) or 0
    equity = _num(latest_bs.get("totalStockholdersEquity"))
    eps = _num(latest_inc.get("eps"))
    revenue = _num(latest_inc.get("revenue"))
    fcf = _num(latest_cf.get("freeCashFlow"))
    ebitda = _num(latest_inc.get("ebitda"))
    is_fund = bool(profile.get("isEtf") or profile.get("isFund"))

    info = {
        "currentPrice": price,
        "regularMarketPrice": price,
        "longName": profile.get("companyName") or quote.get("name") or ticker,
        "shortName": profile.get("companyName") or ticker,
        "sector": profile.get("sector") or "",
        "industry": profile.get("industry") or "",
        "quoteType": "ETF" if is_fund else "EQUITY",
        "currency": profile.get("currency") or "USD",
        "beta": _num(profile.get("beta")) or 1.0,
        "sharesOutstanding": shares,
        "totalDebt": total_debt,
        "totalCash": total_cash,
        "marketCap": market_cap,
        "trailingEps": eps,
        "revenuePerShare": (revenue / shares) if (revenue and shares) else None,
        "freeCashflow": fcf,
        "ebitda": ebitda,
        "enterpriseValue": (market_cap + total_debt - total_cash) if market_cap else None,
        "bookValue": (equity / shares) if (equity and shares) else None,
        # Forward estimates aren't on FMP's free tier; the DCF growth model falls
        # back to historical CAGR when these are absent (a supported code path).
        "earningsGrowth": None,
        "revenueGrowth": None,
        "forwardEps": None,
        "longBusinessSummary": (profile.get("description") or "")[:2000],
    }

    stock = _FmpStock(info, financials, balance_sheet, cashflow, quarterly_bs, prices_df)
    return stock, info
