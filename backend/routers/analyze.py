from __future__ import annotations

import os
import math
import base64
import statistics
import urllib.request
import json as json_mod
from datetime import date as datetime_date
from typing import Optional, List

import yfinance as yf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import FINANCIAL_SECTORS
from utils import *
from services.piotroski import compute_piotroski
from services.dcf import compute_internal_dcf, fetch_external_dcf, compute_earnings_multiple_value, fair_pe_for
from services.valuation_engine import run_valuation_engine, ensemble_weights
from services.relative_value import detect_reorganization, compute_relative_value
from services.blend import compute_blended_valuation
from services import fmp_fallback as fmp
from services.fmp_fallback import is_rate_limited, log_source, build_fmp_bundle
from services.edgar_fundamentals import build_edgar_bundle, enrich_growth
from services.ticker_utils import normalize_ticker

router = APIRouter()

# ---------------------------------------------------------------------------
# Full-valuation cache (Part 4). FMP is now the PRIMARY source, so it is hit far
# more often than when it was a rare fallback. A successful FULL valuation is
# cached per ticker for a few hours, regardless of which source produced it, to
# stay within FMP's 250/day budget and absorb popular-ticker traffic. Degraded
# (quote-only) responses are never cached, so the app recovers full data as soon
# as a provider does. Trade-off: a cached price can be up to VALUATION_TTL stale;
# the /price-history chart has its own fresher path.
# ---------------------------------------------------------------------------
import time as _time

VALUATION_TTL = 24 * 3600  # 24 hours (was 3h) — valuation is intraday-stable and
# the deployed upstreams fail often, so a longer window avoids needless refetches.
_VALUATION_CACHE: dict[str, tuple] = {}


def _fresh_price(ticker: str):
    """A cheap current price for refreshing a cached valuation, best-effort.
    yfinance fast_info first (free/live when not blocked), then Finnhub (free,
    uncapped). Deliberately does NOT touch FMP — we don't spend the capped budget
    just to refresh a price. Returns None if no free source answers."""
    forms = normalize_ticker(ticker)
    try:
        fi = yf.Ticker(forms.yf).fast_info
        p = float(fi["last_price"])
        if p and p > 0:
            return p
    except Exception:
        pass
    try:
        from services import finnhub_fallback as FH
        p = FH._quote_price(forms.finnhub)
        if p and p > 0:
            return float(p)
    except Exception:
        pass
    return None


def _with_fresh_price(payload: dict):
    """Return the cached valuation with its price re-fetched live (statements stay
    cached 24h, but the headline quote is never stale). Recomputes the price-derived
    fields (current_price, margin_of_safety_pct) off the fresh price; leaves the
    intrinsic value untouched. Falls back to the cached payload if no fresh price."""
    price = _fresh_price(payload.get("ticker", ""))
    if price is None:
        return payload
    updated = dict(payload)
    updated["current_price"] = round(price, 2)
    consensus = (payload.get("intrinsic_value") or {}).get("consensus")
    if consensus and price:
        updated["margin_of_safety_pct"] = round((consensus - price) / price * 100, 1)
    return updated


# Persistent second tier for the valuation cache. Render's free tier wipes the
# in-memory dict on every ~15-min-idle restart, so without this each stock is
# re-fetched (≈7 FMP calls) on the next visit and the daily budget drains fast.
# Supabase keeps a computed valuation alive for its full TTL across restarts.
# Best-effort: if Supabase isn't configured/available it's a no-op and we behave
# exactly as before (in-memory only).
from services import supabase_cache as _sb

# Versioned key: bump when the valuation MODEL changes so stale pre-fix numbers in
# the persistent cache are ignored rather than served for up to a full TTL.
# v3 = Moat Valuation Engine (CAP horizon, reverse DCF, Monte Carlo, ensemble).
_SB_VAL_PREFIX = "valuation:v6:"


# A data-limited (EDGAR-backup) valuation must NOT be cached for the full 24h:
# the FMP budget may reset within hours, and holding the flagged copy all day left
# a ticker "stuck" showing the limit banner while freshly-searched tickers around
# it got full data. Limited entries retry after 3h instead. Same for valuations
# whose growth came from the Finnhub PROXY (analyst-estimate budget was spent):
# they're held briefly so the next visit upgrades to real estimates.
_LIMITED_TTL = 3 * 3600


def _is_proxy_growth(payload: dict) -> bool:
    return ((payload or {}).get("dcf_breakdown") or {}).get("growth_provider") == "finnhub_proxy"


def _ttl_for_payload(payload: dict) -> int:
    if (payload or {}).get("data_limited") or _is_proxy_growth(payload):
        return _LIMITED_TTL
    return VALUATION_TTL


def _valuation_cache_get(ticker: str):
    entry = _VALUATION_CACHE.get(ticker)
    if entry and (_time.time() - entry[0]) < _ttl_for_payload(entry[1]):
        # Statements/valuation are cached 24h, but refresh the price so the headline
        # quote (and margin of safety) stay live rather than up to a day stale.
        return _with_fresh_price(entry[1])
    # Miss (or wiped by a restart): try the persistent Supabase copy.
    payload = _sb.cache_get(f"{_SB_VAL_PREFIX}{ticker}")
    if payload is not None:
        # Rehydrate the in-memory tier so subsequent hits are instant, then serve
        # with a fresh price like the in-memory path.
        _VALUATION_CACHE[ticker] = (_time.time(), payload)
        return _with_fresh_price(payload)
    return None


def _valuation_cache_get_stale(ticker: str):
    """Last full valuation regardless of age — served when providers can't
    produce a fresh one (a stale-but-real valuation beats degraded quote-only).
    Checks memory first, then the persistent Supabase copy."""
    entry = _VALUATION_CACHE.get(ticker)
    if entry:
        return entry[1]
    return _sb.get_value_any_age(f"{_SB_VAL_PREFIX}{ticker}")


def _valuation_cache_set(ticker: str, payload: dict) -> None:
    _VALUATION_CACHE[ticker] = (_time.time(), payload)
    # Proxy-growth valuations stay in memory ONLY (short TTL): persisting them
    # would serve a lower-fidelity number across restarts even after the analyst-
    # estimate budget resets. Real-estimate valuations persist as usual.
    if not _is_proxy_growth(payload):
        _sb.cache_set(f"{_SB_VAL_PREFIX}{ticker}", payload, _ttl_for_payload(payload))


# Short-TTL cache for the supporting data endpoints (/metrics, /financials,
# /price-history). One analyze page load fetches these from several components at
# once (e.g. financials is used by both the trends chart and the P/E chart), so
# without a cache the same upstream call runs many times per page load — slow and
# a needless drain on the yfinance/FMP rate budget. Only successful full (non-
# degraded) responses are cached here; the degraded fallback paths keep their own
# short cache so the app still recovers quickly when a provider comes back.
# Statements (financials, metrics) barely move day to day and are the expensive
# calls that drain the FMP budget, so they're cached 24h. Price-history is the live
# price chart, so it keeps a short window and stays fresh.
_DATA_TTL_STATEMENTS = 24 * 3600  # financials, metrics
_DATA_TTL_PRICE = 15 * 60         # price-history (kept fresh)
_DATA_CACHE: dict[str, tuple] = {}


def _ttl_for(key: str) -> int:
    return _DATA_TTL_PRICE if key.startswith("price-history:") else _DATA_TTL_STATEMENTS


def _data_cache_get(key: str):
    entry = _DATA_CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _ttl_for(key):
        return entry[1]
    return None


def _data_cache_set(key: str, payload) -> None:
    _DATA_CACHE[key] = (_time.time(), payload)


# Per-key locks so a burst of concurrent identical requests (one page load fires
# several) collapses into ONE upstream fetch — the rest wait and reuse the result
# instead of all hammering yfinance/FMP at once (which triggers rate-limiting).
import threading as _threading
_DATA_LOCKS: dict[str, "_threading.Lock"] = {}
_DATA_LOCKS_GUARD = _threading.Lock()


def _data_lock(key: str):
    with _DATA_LOCKS_GUARD:
        lock = _DATA_LOCKS.get(key)
        if lock is None:
            lock = _threading.Lock()
            _DATA_LOCKS[key] = lock
        return lock


def _locked_cache(ckey: str, producer):
    """Return cached value, or run `producer()` once under a per-key lock so
    concurrent duplicate requests don't stampede the data provider. The producer
    is responsible for caching its own successful (non-degraded) result."""
    cached = _data_cache_get(ckey)
    if cached is not None:
        return cached
    with _data_lock(ckey):
        cached = _data_cache_get(ckey)  # filled while we waited on the lock
        if cached is not None:
            return cached
        return producer()


def _is_balance_sheet_financial(info, sector) -> bool:
    """True for banks/insurers — companies whose 'FCF' is meaningless because
    deposits/float flow through operating cash flow, so the engine values them on
    excess returns (justified P/B) instead of a DCF.

    Deliberately INDUSTRY-based, not sector-based: the 'Financial Services' sector
    also contains payment networks (Visa/Mastercard) and exchanges with pristine,
    highly-cash-generative business models — excluding those from the DCF (the old
    blanket sector rule) threw away the best valuation lens for them."""
    ind = (safe_get(info, "industry") or "").lower()
    # Berkshire-type mega-cap insurance/holding conglomerates are a special case:
    # their GAAP earnings AND book value are dominated by mark-to-market swings on
    # a huge securities portfolio, so the bank excess-return (justified P/B) model
    # over-values them (BRK.B -> ~$1100 on a $497 stock). Route these to the
    # relative-value engine instead, which already anchors them on their own
    # historical P/B (the `is_conglomerate` path). Detect: Financial-Services +
    # >$200B cap + a "diversified"/"holding" insurer or a conglomerate name.
    if _is_mega_insurance_conglomerate(info, sector):
        return False  # -> DCF is sector-excluded downstream; relative P/B anchors it
    # Banks, insurers and broker-dealers/investment banks (GS/MS: "Capital
    # Markets") — all balance-sheet businesses where OCF/FCF is noise.
    if "bank" in ind or "insur" in ind or "capital market" in ind:
        return True
    if ind:
        return False  # a known non-bank industry (payments, credit services, ...)
    return sector in FINANCIAL_SECTORS  # industry unknown: conservative default


def _is_mega_insurance_conglomerate(info, sector) -> bool:
    """Berkshire-type mega-cap insurance/holding conglomerate: GAAP earnings AND
    book value are dominated by mark-to-market swings on a huge securities
    portfolio, so BOTH an FCF DCF and the bank excess-return (justified P/B) model
    over-value it (BRK.B -> ~$1100 on a $497 stock). These are DCF-excluded but
    routed to the relative-value engine, which anchors them on their OWN historical
    P/B (the `is_conglomerate` path in relative_value.py)."""
    ind = (safe_get(info, "industry") or "").lower()
    market_cap = safe_get(info, "marketCap", 0) or 0
    return (
        sector == "Financial Services" and market_cap > 200e9
        and "insur" in ind and ("diversified" in ind or "holding" in ind)
    )


def _is_financial_dcf_excluded(info, sector) -> bool:
    """True for any financial whose FCF DCF is meaningless: balance-sheet
    financials (banks/insurers via bank_mode) OR mega insurance conglomerates."""
    return _is_balance_sheet_financial(info, sector) or _is_mega_insurance_conglomerate(info, sector)


def _resolve_market_data(ticker: str):
    """Acquire data for the FULL valuation engine, trying providers in order and
    returning (stock_like, info, source).

    Order: yfinance -> FMP -> (SEC EDGAR + Finnhub) -> none.
      1. yfinance — fast/fresh when the host IP isn't blocked (free).
      2. FMP — the PRIMARY provider (750/day across the rotated keys). It carries
         the best inputs (analyst forward estimates, a full multiples history), so
         we always use it while it has any budget left.
      3. EDGAR + Finnhub — the free, uncapped BACKUP, used ONLY when FMP is fully
         exhausted. EDGAR supplies the statements (straight from SEC filings) and
         Finnhub supplies the price + current multiples + growth. Slightly less
         accurate than FMP (no analyst forward estimates), but keeps the site fully
         alive and usable when the FMP daily budget is gone.
    Returns (None, None, None) when no FULL-data provider is available (the caller
    then degrades to a quote-only response)."""
    # Resolve raw provider data, then apply currency normalization ONCE here — the
    # single choke point — so EVERY consumer (analyze, /metrics, /financials,
    # investor personas, thesis) sees statements in the trading currency. Doing it
    # only inside analyze() made the rest of the page contradict itself for foreign
    # ADRs (USD intrinsic value next to DKK charts / DKK-vs-USD ratios).
    stock, info, source = _resolve_market_data_raw(ticker)
    if stock is None:
        return None, None, None
    stock, info = _normalize_statement_currency(ticker, stock, info)
    return stock, info, source


def _resolve_market_data_raw(ticker: str):
    """Provider resolution without currency normalization (see _resolve_market_data)."""
    # Normalize the symbol once so every provider gets its expected spelling
    # (BRK.B -> BRK-B for yfinance/FMP/SEC; Finnhub keeps the dot). Without this,
    # dot-class tickers silently failed everywhere but Finnhub.
    forms = normalize_ticker(ticker)
    # 1. yfinance — fast and fresh when the host IP isn't blocked.
    try:
        stock = yf.Ticker(forms.yf)
        info = stock.info
        if info and (info.get("currentPrice") is not None or info.get("regularMarketPrice") is not None):
            return stock, info, "yfinance"
    except Exception:
        pass
    # 2. FMP — PRIMARY. Full valuation via the adapter; used whenever it has budget.
    bundle = build_fmp_bundle(forms.fmp)
    if bundle is not None:
        stock, info = bundle
        return stock, info, "fmp"
    # 3. SEC EDGAR + Finnhub — free/uncapped BACKUP, only reached once FMP is spent.
    try:
        bundle = build_edgar_bundle(forms.sec)
        if bundle is not None:
            stock, info = bundle
            return stock, info, "edgar"
    except Exception:
        pass
    # 4. No full-data provider; caller handles degraded quote-only path.
    return None, None, None


def _fx_to(base: str, quote: str):
    """Conversion rate from `base` currency into `quote` (e.g. DKK->USD ≈ 0.145).
    Uses Finnhub's free forex quote (works on cloud where yfinance is blocked),
    then yfinance as a backup. Returns None if it can't be determined."""
    base, quote = (base or "").upper(), (quote or "").upper()
    if not base or not quote or base == quote:
        return 1.0
    # Finnhub forex: OANDA:BASE_QUOTE current price.
    try:
        from services import finnhub_fallback as _fh
        q = _fh._get(f"quote?symbol=OANDA:{base}_{quote}")
        c = q.get("c") if isinstance(q, dict) else None
        if c and float(c) > 0:
            return float(c)
    except Exception:
        pass
    try:
        fx = yf.Ticker(f"{base}{quote}=X")
        rate = float(fx.fast_info["last_price"])
        if rate and rate > 0:
            return rate
    except Exception:
        pass
    return None


# Statement-derived money fields reported in the FINANCIAL currency (need FX).
# NOT marketCap/enterpriseValue — those come from the USD price × share count and
# are already in the trading currency; scaling them would double-convert.
_MONEY_INFO_FIELDS = (
    "totalDebt", "totalCash", "freeCashflow", "ebitda",
)
_PER_SHARE_INFO_FIELDS = (
    "trailingEps", "revenuePerShare", "bookValue", "forwardEps",
)


class _CurrencyScaledStock:
    """Wraps a stock (yfinance.Ticker or _FmpStock) exposing FX-scaled statement
    DataFrames while delegating everything else (e.g. .history, .info) unchanged.
    Share-count rows are left un-scaled — only monetary rows are converted."""
    def __init__(self, inner, rate):
        self._inner = inner
        self._rate = rate

    def _scaled(self, df):
        if df is None or getattr(df, "empty", True):
            return df
        out = df * self._rate
        for r in df.index:
            if "Share" in str(r):  # share counts are not money — keep as-is
                out.loc[r] = df.loc[r]
        return out

    @property
    def financials(self):
        return self._scaled(getattr(self._inner, "financials", None))

    @property
    def balance_sheet(self):
        return self._scaled(getattr(self._inner, "balance_sheet", None))

    @property
    def cashflow(self):
        return self._scaled(getattr(self._inner, "cashflow", None))

    @property
    def quarterly_balance_sheet(self):
        return self._scaled(getattr(self._inner, "quarterly_balance_sheet", None))

    def __getattr__(self, name):
        return getattr(self._inner, name)  # delegate history(), info, etc.


def _normalize_statement_currency(ticker, stock, info):
    """If the company reports statements in a DIFFERENT currency than it trades in
    (foreign filers / ADRs — e.g. NVO trades USD but reports DKK), convert every
    statement value and monetary info field into the TRADING currency so the DCF's
    per-share math is consistent with the price. No-op when currencies match or the
    FX rate is unavailable."""
    if info.get("_fx_normalized"):
        return stock, info  # already normalized — idempotent guard
    fin_ccy = safe_get(info, "financialCurrency")
    trade_ccy = safe_get(info, "currency") or "USD"
    if not fin_ccy or fin_ccy == trade_ccy:
        return stock, info
    rate = _fx_to(fin_ccy, trade_ccy)  # statements(fin_ccy) * rate -> trade_ccy
    if not rate or rate == 1.0:
        return stock, info
    try:
        for k in _MONEY_INFO_FIELDS + _PER_SHARE_INFO_FIELDS:
            v = info.get(k)
            if isinstance(v, (int, float)):
                info[k] = v * rate
        # Recompute enterprise value from the (USD) market cap + FX-scaled net debt.
        mc = info.get("marketCap")
        if isinstance(mc, (int, float)):
            info["enterpriseValue"] = mc + (info.get("totalDebt") or 0) - (info.get("totalCash") or 0)
        info["_fx_normalized"] = {"from": fin_ccy, "to": trade_ccy, "rate": round(rate, 6)}
        return _CurrencyScaledStock(stock, rate), info
    except Exception:
        return stock, info


# Negative cache: tickers that resolved to NOTHING (invalid symbol, or no provider
# had data) are remembered briefly so repeated hits (typos, bots) don't re-walk the
# whole provider chain and burn paid FMP/BQ calls each time.
_NEG_CACHE: dict[str, float] = {}
_NEG_TTL = 10 * 60  # seconds


@router.get("/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.upper()

    # Serve a recent full valuation from cache without touching any provider.
    cached = _valuation_cache_get(ticker)
    if cached is not None:
        return cached

    # Recently-failed ticker: short-circuit without touching any provider.
    neg = _NEG_CACHE.get(ticker)
    if neg is not None and (_time.time() - neg) < _NEG_TTL:
        raise HTTPException(status_code=404, detail=f"No data available for {ticker}.")

    # Resolve a FULL-data provider: yfinance -> FMP (full, via adapter).
    stock, info, source = _resolve_market_data(ticker)
    if stock is None:
        # No full-data provider right now (yfinance blocked + FMP rate-limited).
        # Prefer a STALE full valuation over degrading — a day-old full analysis is
        # far more useful than "limited data mode", and valuation barely moves
        # intraday. Only degrade to quote-only if we've never had a full result.
        stale = _valuation_cache_get_stale(ticker)
        if stale is not None:
            log_source("analyze", ticker, "stale_cache")
            return stale
        # FMP is the sole fallback provider (it rotates across the configured keys).
        fb = fmp.analyze_fallback(ticker)
        if fb is not None:
            log_source("analyze", ticker, "fmp_fallback")
            return fb
        # Last resort: a Finnhub quote-only response (free, uncapped). Critical for
        # ETFs/indices/crypto, which EDGAR can't cover (no SEC financial statements)
        # — without this an ETF like VT returns a hard 503 and can't even be added
        # to a portfolio when FMP is exhausted. Valuation fields stay null; the
        # caller/UI treats it as a price-only holding.
        from services import finnhub_fallback as _fh
        quote = _fh.analyze_fallback(normalize_ticker(ticker).finnhub)
        if quote is not None and quote.get("current_price"):
            log_source("analyze", ticker, "finnhub_quote")
            return quote
        # Nothing, anywhere. Remember for a short window so a typo/bot doesn't
        # re-walk the whole provider chain (and burn paid calls) on every hit.
        _NEG_CACHE[ticker] = _time.time()
        raise HTTPException(status_code=503, detail=f"Data temporarily unavailable for {ticker}.")

    log_source("analyze", ticker, source)

    # NOTE: currency normalization for foreign ADRs now happens inside
    # _resolve_market_data (the single choke point), so `stock`/`info` here are
    # already in the trading currency — no second normalization needed.

    # Backfill forward-ish growth from Finnhub (free/uncapped) for any source that
    # didn't supply it — notably FMP's free tier, which leaves growth None and would
    # otherwise force the DCF onto a weak historical CAGR (AAPL -> ~$124). This makes
    # valuation quality independent of which provider served the statements.
    enrich_growth(ticker, info)

    current_price = safe_get(info, "currentPrice") or safe_get(info, "regularMarketPrice", 0)
    company_name = safe_get(info, "longName") or safe_get(info, "shortName", ticker)
    sector = safe_get(info, "sector", "")
    quote_type = safe_get(info, "quoteType", "EQUITY") or "EQUITY"
    currency = safe_get(info, "currency", "USD") or "USD"

    # --- Asset-class branch ---
    # DCF, relative-value multiples, the F-Score and the investor personas are all
    # built for operating companies with financial statements. ETFs, crypto and
    # indices have none, so we short-circuit to a price-only response instead of
    # fabricating valuation. This is an ADDITIVE branch; equities fall through
    # unchanged below.
    if quote_type not in ("EQUITY", "MUTUALFUND"):
        etf_info = None
        if quote_type == "ETF":
            etf_info = {
                "category": safe_get(info, "category"),
                "expense_ratio": safe_get(info, "annualReportExpenseRatio")
                or safe_get(info, "netExpenseRatio"),
                "total_assets": safe_get(info, "totalAssets"),
                "summary": (safe_get(info, "longBusinessSummary", "") or "")[:600] or None,
            }
        return {
            "ticker": ticker,
            "company_name": company_name,
            "current_price": round(current_price, 2) if current_price else None,
            "quote_type": quote_type,
            "currency": currency,
            "intrinsic_value": {"bear": {"value": None}, "base": {"value": None}, "bull": {"value": None}, "consensus": None, "partial": False},
            "margin_of_safety_pct": None,
            "confidence": None,
            "valuation_note": "Not an operating company — valuation metrics (intrinsic value, F-Score, investor analysis) do not apply.",
            "f_score": None,
            "revenue_5yr": [],
            "fcf_5yr": [],
            "etf_info": etf_info,
            "valuation_breakdown": None,
            "dcf_breakdown": None,
        }

    financials = stock.financials
    balance_sheet = stock.balance_sheet
    cashflow = stock.cashflow

    revenue_5yr = []
    if "Total Revenue" in financials.index:
        rev_series = financials.loc["Total Revenue"].dropna()
        revenue_5yr = [int(v) for v in rev_series.values[:5]][::-1]

    fcf_5yr_raw = _fcf_list(cashflow, 5)
    fcf_5yr = [int(v) for v in fcf_5yr_raw][::-1] if fcf_5yr_raw else []

    # 1. Internal DCF -> Moat Valuation Engine.
    # The legacy compute_internal_dcf still derives the growth input (forward
    # analyst estimate -> revenue CAGR -> FCF CAGR) and the capex-normalized base
    # FCF; the engine then supersedes its flat 5-year projection with the moat-
    # driven CAP model (fading growth over a quality-dependent horizon), a reverse
    # DCF (market-implied growth) and a Monte Carlo fair-value distribution.
    # Balance-sheet financials (banks/insurers — NOT payment networks like Visa)
    # get the excess-return (justified P/B) model instead of an FCF DCF.
    # bank_mode -> the engine's excess-return (justified P/B) model. dcf_excluded
    # -> the DCF is meaningless (banks/insurers AND mega insurance conglomerates
    # like BRK, which are NOT bank_mode but must still skip the FCF DCF and lean on
    # the relative-value P/B anchor). Two distinct flags on purpose.
    bank_mode = _is_balance_sheet_financial(info, sector)
    financial_dcf_excluded = _is_financial_dcf_excluded(info, sector)
    legacy_dcf = compute_internal_dcf(info, fcf_5yr, sector, revenue_5yr)
    f_score_early = compute_piotroski(financials, balance_sheet, cashflow, info)
    dcf_result = run_valuation_engine(
        info, financials, balance_sheet, cashflow,
        fcf_5yr, revenue_5yr, sector, f_score_early, current_price,
        base_fcf=legacy_dcf.get("base_fcf"),
        growth_rate=legacy_dcf.get("growth_rate"),
        growth_source=legacy_dcf.get("growth_source"),
        bank_mode=bank_mode,
    )

    # 2. External DCF
    ext_dcf = fetch_external_dcf(ticker)

    # Merger/reorg detection — corrupts multi-year per-share history.
    reorganized, reorg_reasons = detect_reorganization(stock, info)

    # 3. Relative Value. `rel_unreliable` is True when the multi-year multiples
    # had to be discarded for forward/current anchoring (detected merger OR a
    # result wildly out of line with price) — a single, uniform low-confidence path.
    rel_val, rel_factors, rel_unreliable = compute_relative_value(
        stock, info, current_price, reorganized=reorganized
    )
    valuation_unreliable = reorganized or rel_unreliable

    # 4-7. Blend (still used for adjustments + source mismatch detection)
    blend = compute_blended_valuation(
        dcf_result, ext_dcf, rel_val,
        sector, current_price, fcf_5yr, info, stock,
        low_confidence_valuation=valuation_unreliable, is_financial=financial_dcf_excluded
    )

    scenarios = dcf_result["scenarios"]
    base_value = scenarios["base"]["value"]
    # Any financial whose FCF DCF is meaningless (banks/insurers via bank_mode,
    # AND mega insurance conglomerates like BRK). For bank_mode the "internal"
    # value IS the excess-return model, so it stays reliable; for a conglomerate
    # the internal DCF is excluded and only relative-value P/B carries it.
    is_financial = financial_dcf_excluded and not bank_mode
    engine_meta = dcf_result.get("engine") or {}
    dcf_reliable = engine_meta.get("dcf_reliable", True)

    # Consensus = weighted ensemble of [internal model, external DCF, relative
    # value, earnings multiple]. How much we trust the INTERNAL model depends on
    # its growth input:
    #   - forward_* (analyst estimates)     -> reliable; always include.
    #   - historical_revenue_cagr           -> reliable enough; include even when an
    #     external DCF exists (it uses the business's revenue trajectory + a
    #     capex-normalized base FCF, so it no longer collapses for growth names).
    #   - historical_cagr (raw FCF CAGR)    -> weak; keep the prior behavior of
    #     deferring to a forward-looking external DCF when one is available, since
    #     FCF CAGR badly undervalues companies mid capex-cycle.
    # Banks/insurers are included too — their internal model is the excess-return
    # (justified P/B) valuation, the correct lens for balance-sheet financials.
    growth_source = str(dcf_result.get("growth_source", ""))
    dcf_is_forward = growth_source.startswith("forward")
    dcf_is_business = dcf_is_forward or growth_source == "historical_revenue_cagr"
    has_external = bool(ext_dcf and ext_dcf > 0)
    internal_reliable = (
        base_value and base_value > 0
        and not is_financial  # conglomerates: DCF excluded, relative P/B carries it
        and (dcf_is_business or not has_external or bank_mode)
    )
    # Labeled sources so the UI's Valuation Breakdown shows EXACTLY what went into
    # the consensus and at what weight — previously the earnings anchor was a hidden
    # fourth input, so the displayed numbers didn't add up to the headline value.
    consensus_sources = []  # list of (label, value)
    if internal_reliable:
        consensus_sources.append(("internal_dcf", base_value))
    # Use the external forward DCF unless it mismatches a *reliable* internal DCF
    # (when the internal one is the unreliable historical-CAGR fallback, the
    # "mismatch" is the internal's fault, so we still trust the external).
    ext_usable = has_external and (not blend["source_mismatch_warning"] or not internal_reliable)
    if ext_usable:
        consensus_sources.append(("external_dcf", ext_dcf))
    if rel_val and rel_val > 0:
        consensus_sources.append(("relative_value", rel_val))

    # Earnings-multiple anchor (fair P/E × EPS). An independent, earnings-based
    # estimate that stays sane for hyper-capex names whose FCF-DCF collapses
    # (AMZN/TSLA) and for FINANCIALS (where a DCF doesn't apply and P/E is the
    # standard lens — for those it's often the ONLY usable source). Included
    # whenever the company is profitable; keeps the consensus grounded in earnings.
    # EXCEPT mega insurance conglomerates (BRK): their GAAP EPS is mark-to-market
    # noise, so the earnings multiple is as unreliable as the DCF — lean on the
    # relative-value P/B anchor alone, matching relative_value's conglomerate path.
    earn_mult = compute_earnings_multiple_value(info, sector, dcf_result.get("growth_rate"))
    if earn_mult and earn_mult > 0 and not _is_mega_insurance_conglomerate(info, sector):
        consensus_sources.append(("earnings_multiple", earn_mult))

    # Diagnostic-weighted ensemble (not a blind equal average): the DCF's weight
    # scales with how clean the FCF record is and whether its growth input is a
    # real forward estimate; the earnings anchor with earnings stability. These
    # are the ACTUAL weights of the consensus, surfaced to the UI.
    consensus_weights = ensemble_weights(
        consensus_sources, fcf_5yr, revenue_5yr, info, growth_source,
        dcf_reliable=dcf_reliable or bank_mode,
    ) if consensus_sources else {}
    consensus = (
        round(sum(v * consensus_weights.get(label, 0) for label, v in consensus_sources), 2)
        if consensus_sources else None
    )

    # Fix 1: Consensus consistency. If ALL DCF scenarios are N/A but a consensus
    # was still produced (from FMP + multiples), flag it as partial so the UI
    # doesn't present a confident-looking number with no DCF backing.
    scenarios_all_none = all(
        scenarios[k]["value"] is None for k in ("bear", "base", "bull")
    )
    intrinsic_partial = scenarios_all_none and consensus is not None
    if intrinsic_partial:
        # Confidence cannot be high when DCF is entirely unavailable
        if blend["confidence"] == "high":
            blend["confidence"] = "medium"

    # Confidence cannot be high when the ONLY valuation input is the weakest growth
    # signal — a raw historical FCF CAGR (no forward estimate, no revenue trajectory)
    # — AND there's no external DCF or relative multiple to cross-check it. That
    # combination is where the internal DCF is least reliable, so cap it at "low".
    only_weak_growth = (
        growth_source == "historical_cagr"
        and not has_external
        and not (rel_val and rel_val > 0)
    )
    if only_weak_growth and blend["confidence"] in ("high", "medium"):
        blend["confidence"] = "low"

    # Structural guard for FCF-DCF blind spots. Hyper-capex names (AMZN, TSLA) have
    # free cash flow that's thin/volatile relative to their earning power, so the
    # DCF produces an absurd per-share value (AMZN base ~ $25 vs a $242 price). The
    # earnings-multiple anchor can then pull the BLENDED consensus back into a
    # plausible-looking range (~$127) while the underlying DCF is still nonsense —
    # so keying the guard off the consensus alone misses it. Instead we also look at
    # the internal DCF's own base scenario: if it lands wildly away from price
    # (<0.5x or >2x), the DCF is the wrong lens here and confidence can't be high,
    # regardless of what the anchor did to the blend. (Financials legitimately have
    # no DCF, so they're exempt — their earnings anchor stands on its own.)
    dcf_absurd = (
        not is_financial and base_value and current_price
        and (base_value < 0.5 * current_price or base_value > 2.0 * current_price)
    )
    internal_only = not has_external and not (rel_val and rel_val > 0)
    consensus_absurd = (
        internal_only and consensus and current_price
        and (consensus < 0.4 * current_price or consensus > 2.5 * current_price)
    )
    if (dcf_absurd or consensus_absurd) and blend["confidence"] in ("high", "medium"):
        blend["confidence"] = "low"

    # A consensus that disagrees with the market by more than ~35% can be RIGHT —
    # that's what a value screen is for — but it should never wear a green "high
    # confidence" badge: the market is a real prior, and a big divergence deserves
    # epistemic humility (the AI second opinion carries the counter-analysis).
    if (consensus and current_price
            and (consensus < 0.65 * current_price or consensus > 1.55 * current_price)
            and blend["confidence"] == "high"):
        blend["confidence"] = "medium"

    # Margin of safety anchored to the consensus (Intrinsic Value), which is the
    # single headline number shown in the UI.
    anchor = consensus
    if anchor and current_price:
        mos = round((anchor - current_price) / current_price * 100, 1)
    else:
        mos = None

    # Note: internal methodology details (partial/DCF-disagreement) are NOT
    # surfaced as prose; the confidence badge communicates valuation reliability.
    valuation_note = None
    if consensus is None:
        valuation_note = "Insufficient data for valuation"
    elif reorganized:
        valuation_note = (
            "Recently merged/reorganized — multi-year history mixes pre- and "
            "post-merger figures, so the multi-year valuation is unreliable. This "
            "estimate is forward-anchored and low-confidence."
        )
    elif rel_unreliable:
        valuation_note = (
            "Multi-year valuation multiples look unreliable for this company "
            "(e.g. a restructuring or distorted historical figures), so this "
            "estimate is forward-anchored and low-confidence."
        )

    f_score = f_score_early  # computed above as a moat-score input

    # Banks/insurers now HAVE an internal model (excess return / justified P/B),
    # so the old "Excluded (sector)" state only remains when even that model
    # couldn't be computed (e.g. negative equity).
    if bank_mode and dcf_result["meaningful"]:
        blend["adjustments_applied"] = [
            a for a in blend["adjustments_applied"] if a != "sector_excluded_dcf"
        ]
        if "excess_return_model" not in blend["adjustments_applied"]:
            blend["adjustments_applied"].append("excess_return_model")
    dcf_excluded = "sector_excluded_dcf" in blend["adjustments_applied"]

    # Analyst estimates section — REAL Wall-Street consensus from Finnhub: the
    # Buy/Hold/Sell recommendation breakdown + a derived consensus rating. Plus the
    # analyst forward-EPS figures from BusinessQuant (also real). Price targets are
    # premium-gated on Finnhub's free plan, so we intentionally DON'T fabricate one
    # (the earlier EPS×fair-PE "target" was a model number, not an analyst view, and
    # didn't match public sources). This shows what analysts actually rate the stock.
    analyst_estimates = None
    try:
        from services import finnhub_fallback as _fh
        rec = _fh.analyst_recommendation(ticker)
        eps = None
        try:
            from services.businessquant import analyst_eps_estimates
            eps = analyst_eps_estimates(ticker)
        except Exception:
            pass
        if rec:
            analyst_estimates = {
                "consensus": rec["consensus"],       # e.g. "Buy"
                "score": rec["score"],               # 1..5
                "strong_buy": rec["strong_buy"],
                "buy": rec["buy"],
                "hold": rec["hold"],
                "sell": rec["sell"],
                "strong_sell": rec["strong_sell"],
                "total_analysts": rec["total"],
                "period": rec["period"],
                # Real forward-EPS consensus (BusinessQuant), if available.
                "eps_period": eps.get("period") if eps else None,
                "eps_low": eps.get("low") if eps else None,
                "eps_base": eps.get("base") if eps else None,
                "eps_high": eps.get("high") if eps else None,
                "last_reported_eps": eps.get("last_reported") if eps else None,
            }
    except Exception:
        pass

    payload = {
        "ticker": ticker,
        "company_name": company_name,
        "current_price": round(current_price, 2),
        "quote_type": quote_type,
        "currency": currency,
        "intrinsic_value": {
            "bear": scenarios["bear"],
            "base": scenarios["base"],
            "bull": scenarios["bull"],
            "consensus": consensus,
            "partial": intrinsic_partial,
        },
        "margin_of_safety_pct": mos,
        "confidence": blend["confidence"],
        "valuation_note": valuation_note,
        "f_score": f_score,
        "revenue_5yr": revenue_5yr,
        "fcf_5yr": fcf_5yr,
        "valuation_breakdown": {
            "internal_dcf": None if dcf_excluded else (base_value if dcf_result["meaningful"] else None),
            "dcf_excluded": dcf_excluded,
            "external_dcf": round(ext_dcf, 2) if ext_dcf else None,
            "relative_value": rel_val,
            "earnings_multiple": earn_mult,
            "relative_factors": rel_factors,
            "blend_weights": blend["blend_weights"],
            # The consensus is an equal-weight average of the sources below; these
            # are the TRUE weights (the legacy blend_weights above are only used for
            # adjustment/mismatch detection and confused the UI when displayed).
            "consensus_weights": consensus_weights,
            "adjustments_applied": blend["adjustments_applied"],
            "source_mismatch_warning": blend["source_mismatch_warning"],
        },
        "dcf_breakdown": {
            "wacc": dcf_result["wacc"],
            "terminal_growth": dcf_result["terminal_growth"],
            "growth_rate": dcf_result["growth_rate"],
            "growth_source": dcf_result["growth_source"],
            # Which provider actually supplied the growth input: native (yfinance),
            # businessquant (analyst consensus) or finnhub_proxy (historical trend).
            # Proxy-based valuations get a short cache so they upgrade quickly.
            "growth_provider": safe_get(info, "_growth_provider"),
            "sector": sector,
            "enterprise_value": dcf_result["enterprise_value"],
            "equity_value": dcf_result["equity_value"],
            "net_debt": round(safe_get(info, "totalDebt", 0) - safe_get(info, "totalCash", 0)),
        },
        # Moat Valuation Engine output: moat score & components, CAP horizon,
        # market-implied vs expected growth (reverse DCF), Monte Carlo band.
        "valuation_engine": dcf_result.get("engine"),
        # Wall-Street analyst forward-EPS consensus (low/base/high) + implied price
        # range — distinct from the model's own DCF scenarios.
        "analyst_estimates": analyst_estimates,
        # Records which provider served the full valuation (absent for yfinance,
        # matching prior behavior). NOTE: we intentionally DON'T set data_limited
        # for the EDGAR path anymore. On the free tier FMP is often capped, so
        # nearly every request goes through EDGAR — and EDGAR now produces a
        # COMPLETE valuation (SEC-filing statements + BusinessQuant analyst
        # estimates + Finnhub price/ratios). Flagging every one as "limited" cried
        # wolf on normal, full-quality results. The genuinely-degraded quote-only
        # path (finnhub_fallback, handled earlier) sets its own note instead.
        **({"data_source": source} if source != "yfinance" else {}),
    }
    # Cache full valuations (Part 4) regardless of source. Degraded responses
    # (handled above) are never cached, so the app recovers full data promptly.
    _valuation_cache_set(ticker, payload)
    return payload


# ---------------------------------------------------------------------------
# Other endpoints (unchanged)
# ---------------------------------------------------------------------------


VALID_PERIODS = {"1mo", "3mo", "6mo", "1y", "5y", "max"}


@router.get("/price-history/{ticker}")
def price_history(ticker: str, period: str = "1y"):
    ticker = ticker.upper()
    if period not in VALID_PERIODS:
        period = "1y"
    ckey = f"price-history:{ticker}:{period}"
    return _locked_cache(ckey, lambda: _price_history_impl(ticker, period, ckey))


def _price_history_impl(ticker: str, period: str, ckey: str):
    stock = yf.Ticker(normalize_ticker(ticker).yf)
    try:
        hist = stock.history(period=period)
    except Exception:
        hist = None  # rate-limit or transient error -> try fallback below

    if hist is None or hist.empty:
        fb = fmp.price_history_fallback(ticker, period)
        if fb is not None:
            log_source("price-history", ticker, "fmp_fallback")
            return fb
        raise HTTPException(status_code=404, detail=f"No price history for {ticker}")
    log_source("price-history", ticker, "yfinance")
    # yfinance can return NaN/Inf closes for some dates (gaps, partial data).
    # Skip them — NaN/Inf are not JSON-serializable and 500 the whole response.
    dates = []
    prices = []
    for d, p in hist["Close"].items():
        pv = float(p)
        if not math.isfinite(pv):
            continue
        dates.append(d.strftime("%Y-%m-%d"))
        prices.append(round(pv, 2))
    if not prices:
        raise HTTPException(status_code=404, detail=f"No price history for {ticker}")

    # history() ends at the LAST CLOSED session; today's bar is NaN (dropped above),
    # so the series would otherwise lag the live quote by a day. Append the current
    # live price as the final point so the chart ends at the real current price.
    try:
        live = None
        try:
            live = float(stock.fast_info["last_price"])
        except Exception:
            info = stock.info or {}
            live = info.get("currentPrice") or info.get("regularMarketPrice")
            live = float(live) if live is not None else None
        if live and math.isfinite(live):
            today = datetime_date.today().strftime("%Y-%m-%d")
            if dates and dates[-1] == today:
                prices[-1] = round(live, 2)  # replace today's stale/partial point
            elif not dates or dates[-1] < today:
                dates.append(today)
                prices.append(round(live, 2))
    except Exception:
        pass

    result = {"ticker": ticker, "dates": dates, "prices": prices}
    _data_cache_set(ckey, result)
    return result



@router.get("/fx-rate")
def fx_rate(base: str = "USD"):
    """Live conversion rate from `base` currency to USD (e.g. JPY -> 0.0064).
    Used to convert non-USD portfolio holdings into a common USD total.

    Routes through the shared _fx_to() (Finnhub OANDA first, yfinance backup) so it
    works on cloud hosts where yfinance is IP-blocked — the old yfinance-only
    version returned null in production, and the frontend then silently converted
    1:1, recording a ¥50,000 holding as $50,000. Returns rate_to_usd: null when the
    rate genuinely can't be determined, and the frontend surfaces that rather than
    inventing a 1:1 rate."""
    base = (base or "USD").upper()
    if base == "USD":
        return {"base": base, "rate_to_usd": 1.0}
    rate = _fx_to(base, "USD")
    if rate and math.isfinite(rate) and rate > 0:
        return {"base": base, "rate_to_usd": rate}
    return {"base": base, "rate_to_usd": None}



@router.get("/financials/{ticker}")
def financials_endpoint(ticker: str):
    ticker = ticker.upper()
    ckey = f"financials:{ticker}"
    return _locked_cache(ckey, lambda: _financials_impl(ticker, ckey))


def _financials_impl(ticker: str, ckey: str):
    stock = yf.Ticker(normalize_ticker(ticker).yf)
    try:
        inc = stock.financials
        cf = stock.cashflow
        bs = stock.balance_sheet
    except Exception:
        inc = cf = bs = None

    # Empty income statement is the rate-limit symptom; try the FMP fallback, then
    # the free EDGAR bundle (its statement DataFrames are the same shape) so the
    # revenue/EPS/FCF charts still render when FMP is capped.
    if inc is None or getattr(inc, "empty", True):
        fb = fmp.financials_fallback(ticker)
        if fb is not None:
            log_source("financials", ticker, "fmp_fallback")
            return fb
        try:
            bundle = build_edgar_bundle(ticker)
            if bundle is not None:
                estock, _einfo = bundle
                inc, cf, bs = estock.financials, estock.cashflow, estock.balance_sheet
                log_source("financials", ticker, "edgar")
        except Exception:
            pass
        # Still nothing: fall through with empty frames -> empty arrays (prior behavior).
        import pandas as pd
        inc = inc if inc is not None else pd.DataFrame()
        cf = cf if cf is not None else pd.DataFrame()
        bs = bs if bs is not None else pd.DataFrame()
    else:
        log_source("financials", ticker, "yfinance")

    revenue = extract_series(inc, "Total Revenue")
    gross_profit = extract_series(inc, "Gross Profit")
    operating_income = extract_series(inc, "Operating Income")
    net_income = extract_series(inc, "Net Income")

    eps = []
    ni_series = inc.loc["Net Income"].dropna() if "Net Income" in inc.index else None
    shares_series = None
    for lbl in ["Share Issued", "Common Stock Shares Outstanding", "Ordinary Shares Number"]:
        if lbl in bs.index:
            shares_series = bs.loc[lbl].dropna()
            break
    if ni_series is not None and shares_series is not None:
        for date in ni_series.index:
            yr = date.year
            matched = None
            for sd in shares_series.index:
                if sd.year == yr:
                    matched = shares_series[sd]; break
            if matched and matched > 0:
                eps.append({"year": str(yr), "value": round(float(ni_series[date] / matched), 2)})
        eps.reverse()

    fcf = []
    ocf_s = None
    for lbl in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
        if lbl in cf.index:
            ocf_s = cf.loc[lbl].dropna(); break
    capex_s = None
    for lbl in ["Capital Expenditure", "Capital Expenditures"]:
        if lbl in cf.index:
            capex_s = cf.loc[lbl].dropna(); break
    if "Free Cash Flow" in cf.index:
        fcf = extract_series(cf, "Free Cash Flow")
    elif ocf_s is not None:
        for date in ocf_s.index:
            cx = 0.0
            if capex_s is not None:
                for sd in capex_s.index:
                    if sd.year == date.year:
                        cx = float(capex_s[sd]); break
            fcf.append({"year": str(date.year), "value": float(ocf_s[date]) + cx})
        fcf.reverse()

    shares = []
    if shares_series is not None:
        for date, val in list(shares_series.items()):
            shares.append({"year": str(date.year), "value": float(val)})
        shares.reverse()

    result = {
        "ticker": ticker, "revenue": revenue, "eps": eps, "fcf": fcf,
        "gross_profit": gross_profit, "operating_income": operating_income,
        "net_income": net_income, "shares_outstanding": shares,
    }
    _data_cache_set(ckey, result)
    return result


@router.get("/metrics/{ticker}")
def metrics_endpoint(ticker: str):
    ticker = ticker.upper()
    ckey = f"metrics:{ticker}"
    return _locked_cache(ckey, lambda: _metrics_impl(ticker, ckey))


def _metrics_impl(ticker: str, ckey: str):
    stock = yf.Ticker(normalize_ticker(ticker).yf)
    try:
        info = stock.info or {}
    except Exception:
        info = {}

    # Empty info (no market cap / price) is the rate-limit symptom; try the FMP
    # fallback (rotates across the configured keys), then the free EDGAR+Finnhub
    # bundle so /metrics still populates (P/E, margins, etc.) when FMP is capped —
    # previously this returned all-null and the "About" card showed "Couldn't load".
    if not info or (info.get("marketCap") is None and info.get("currentPrice") is None
                    and info.get("regularMarketPrice") is None):
        fb = fmp.metrics_fallback(ticker)
        if fb is not None:
            log_source("metrics", ticker, "fmp_fallback")
            return fb
        try:
            bundle = build_edgar_bundle(ticker)
            if bundle is not None:
                _stock_e, info = bundle  # EDGAR info has price/mcap/eps/margins
                enrich_growth(ticker, info)
                log_source("metrics", ticker, "edgar")
        except Exception:
            pass
    else:
        log_source("metrics", ticker, "yfinance")

    def g(key, default=None):
        return safe_get(info, key, default)

    market_cap = g("marketCap")
    fcf = g("freeCashflow")
    shares = g("sharesOutstanding")
    total_debt = g("totalDebt", 0)
    total_cash = g("totalCash", 0)
    trailing_eps = g("trailingEps")

    p_fcf = round(market_cap / fcf, 1) if market_cap and fcf and fcf > 0 else None
    fcf_per_share = round(fcf / shares, 2) if fcf is not None and shares and shares > 0 else None
    net_debt = total_debt - total_cash
    net_debt_ps = round(net_debt / shares, 2) if shares and shares > 0 else None
    de = g("debtToEquity")
    debt_equity = round(de / 100, 2) if de is not None else None

    pe = g("trailingPE")
    peg = g("trailingPegRatio")
    fg = g("earningsGrowth")
    if peg is None and pe is not None and fg is not None and fg > 0:
        peg = round(pe / (fg * 100), 2)

    result = {
        "ticker": ticker,
        "valuation": {
            "pe_ratio": round(pe, 1) if pe is not None else None,
            "forward_pe": round(g("forwardPE"), 1) if g("forwardPE") is not None else None,
            "pb_ratio": round(g("priceToBook"), 1) if g("priceToBook") is not None else None,
            "ev_ebitda": round(g("enterpriseToEbitda"), 1) if g("enterpriseToEbitda") is not None else None,
            "p_fcf": p_fcf,
            "peg_ratio": round(peg, 2) if peg is not None else None,
        },
        "dividends": {
            "dividend_yield": round(g("dividendYield"), 2) if g("dividendYield") is not None else None,
            "annual_dividend": round(g("dividendRate"), 2) if g("dividendRate") is not None else None,
            "payout_ratio": round(g("payoutRatio") * 100, 1) if g("payoutRatio") is not None else None,
        },
        "quality": {
            "current_ratio": round(g("currentRatio"), 2) if g("currentRatio") is not None else None,
            "quick_ratio": round(g("quickRatio"), 2) if g("quickRatio") is not None else None,
            "roic": round(g("returnOnAssets") * 100, 1) if g("returnOnAssets") is not None else None,
            "profit_margin": round(g("profitMargins") * 100, 1) if g("profitMargins") is not None else None,
        },
        "financial_health": {
            "eps_ttm": round(trailing_eps, 2) if trailing_eps is not None else None,
            "fcf_per_share": fcf_per_share,
            "net_debt_per_share": net_debt_ps,
            "debt_equity": debt_equity,
            "market_cap": market_cap,
        },
        "analyst_ratings": {
            "recommendation": g("recommendationKey"),
            "num_analysts": g("numberOfAnalystOpinions"),
            "target_mean_price": g("targetMeanPrice"),
            "target_high_price": g("targetHighPrice"),
            "target_low_price": g("targetLowPrice"),
        },
    }
    _data_cache_set(ckey, result)
    return result


# ---------------------------------------------------------------------------
# Phase 4 — Legendary Investor Cards (Groq)
# ---------------------------------------------------------------------------


def gather_fundamentals(ticker, stock, info):
    financials = stock.financials
    cashflow = stock.cashflow

    rev_vals = _series_vals(financials, "Total Revenue", 5)[::-1]
    fcf_vals = _fcf_list(cashflow, 5)[::-1]
    ni_vals = _series_vals(financials, "Net Income", 5)[::-1]

    current_price = safe_get(info, "currentPrice") or safe_get(info, "regularMarketPrice", 0)
    shares = safe_get(info, "sharesOutstanding", 0)
    fcf_ttm = safe_get(info, "freeCashflow")
    rev_ttm = safe_get(info, "totalRevenue")
    fcf_margin = (fcf_ttm / rev_ttm * 100) if fcf_ttm and rev_ttm else None

    # Shares outstanding trend (declining => buybacks)
    bs = stock.balance_sheet
    shares_trend = "n/a"
    for lbl in ["Share Issued", "Common Stock Shares Outstanding", "Ordinary Shares Number"]:
        if lbl in bs.index:
            sh = [float(v) for v in bs.loc[lbl].dropna().values[:5]][::-1]
            shares_trend = _trend(sh)
            break

    roe = safe_get(info, "returnOnEquity")
    roa = safe_get(info, "returnOnAssets")
    de = safe_get(info, "debtToEquity")
    eps_ttm = safe_get(info, "trailingEps")
    bvps = safe_get(info, "bookValue")

    # Graham Number = sqrt(22.5 * EPS * Book Value per Share) — a defensive fair-price ceiling
    graham_number = None
    if eps_ttm and bvps and eps_ttm > 0 and bvps > 0:
        graham_number = round(math.sqrt(22.5 * eps_ttm * bvps), 2)

    facts = {
        "company_name": safe_get(info, "longName") or safe_get(info, "shortName", ticker),
        "sector": safe_get(info, "sector", "n/a"),
        "business_summary": (safe_get(info, "longBusinessSummary", "") or "")[:900],
        "current_price": round(current_price, 2) if current_price else None,
        "52w_low": safe_get(info, "fiftyTwoWeekLow"),
        "52w_high": safe_get(info, "fiftyTwoWeekHigh"),
        "revenue_ttm": rev_ttm,
        "revenue_trend": _trend(rev_vals),
        "fcf_ttm": fcf_ttm,
        "fcf_trend": _trend(fcf_vals),
        "fcf_margin_pct": round(fcf_margin, 1) if fcf_margin is not None else None,
        "net_income_trend": _trend(ni_vals),
        "roe_pct": round(roe * 100, 1) if roe is not None else None,
        "roic_or_roa_pct": round(roa * 100, 1) if roa is not None else None,
        "debt_to_equity": round(de / 100, 2) if de is not None else None,
        "current_ratio": safe_get(info, "currentRatio"),
        "total_debt": safe_get(info, "totalDebt"),
        "total_cash": safe_get(info, "totalCash"),
        "shares_trend_buybacks": shares_trend,
        "eps_ttm": eps_ttm,
        "book_value_per_share": bvps,
        "price_to_book": safe_get(info, "priceToBook"),
        "graham_number": graham_number,
        "pe_ratio": safe_get(info, "trailingPE"),
        "forward_pe": safe_get(info, "forwardPE"),
        "peg_ratio": safe_get(info, "trailingPegRatio"),
        "profit_margin_pct": round(safe_get(info, "profitMargins") * 100, 1) if safe_get(info, "profitMargins") is not None else None,
        "beta": safe_get(info, "beta"),
    }
    return facts


