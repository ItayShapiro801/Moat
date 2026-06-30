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
from services.dcf import compute_internal_dcf, fetch_external_dcf
from services.relative_value import detect_reorganization, compute_relative_value
from services.blend import compute_blended_valuation
from services import fmp_fallback as fmp
from services import finnhub_fallback as finnhub
from services.fmp_fallback import is_rate_limited, log_source, build_fmp_bundle

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

VALUATION_TTL = 3 * 3600  # 3 hours
_VALUATION_CACHE: dict[str, tuple] = {}


def _valuation_cache_get(ticker: str):
    entry = _VALUATION_CACHE.get(ticker)
    if entry and (_time.time() - entry[0]) < VALUATION_TTL:
        return entry[1]
    return None


def _valuation_cache_set(ticker: str, payload: dict) -> None:
    _VALUATION_CACHE[ticker] = (_time.time(), payload)


def _resolve_market_data(ticker: str):
    """Acquire data for the FULL valuation engine, trying providers in order and
    returning (stock_like, info, source). yfinance is tried first for speed/
    freshness; if it's blocked or empty, FMP becomes the full primary source via
    an adapter that mimics yfinance's shape so the engine runs unchanged.
    Returns (None, None, None) when no FULL-data provider is available (the caller
    then degrades to a quote-only response)."""
    # 1. yfinance — fast and fresh when the host IP isn't blocked.
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if info and (info.get("currentPrice") is not None or info.get("regularMarketPrice") is not None):
            return stock, info, "yfinance"
    except Exception:
        pass
    # 2. FMP — official API, full valuation pipeline via the adapter.
    bundle = build_fmp_bundle(ticker)
    if bundle is not None:
        stock, info = bundle
        return stock, info, "fmp"
    # 3. No full-data provider; caller handles degraded quote-only path.
    return None, None, None

@router.get("/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.upper()

    # Serve a recent full valuation from cache without touching any provider.
    cached = _valuation_cache_get(ticker)
    if cached is not None:
        return cached

    # Resolve a FULL-data provider: yfinance -> FMP (full, via adapter).
    stock, info, source = _resolve_market_data(ticker)
    if stock is None:
        # No full-data provider. Degrade to a quote-only response: FMP first,
        # then Finnhub (the last-resort backup). Degraded responses are NOT cached.
        for fb_fn, src in (
            (fmp.analyze_fallback, "fmp_fallback"),
            (finnhub.analyze_fallback, "finnhub_fallback"),
        ):
            fb = fb_fn(ticker)
            if fb is not None:
                log_source("analyze", ticker, src)
                return fb
        raise HTTPException(status_code=503, detail=f"Data temporarily unavailable for {ticker}.")

    log_source("analyze", ticker, source)

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

    # 1. Internal DCF
    dcf_result = compute_internal_dcf(info, fcf_5yr, sector)

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
        sector, current_price, fcf_5yr, info, stock, low_confidence_valuation=valuation_unreliable
    )

    scenarios = dcf_result["scenarios"]
    base_value = scenarios["base"]["value"]
    is_financial = sector in FINANCIAL_SECTORS

    # Consensus = average of [base_dcf, external_dcf (if usable), relative_value]
    consensus_sources = []
    if base_value and base_value > 0 and not is_financial:
        consensus_sources.append(base_value)
    ext_usable = ext_dcf and ext_dcf > 0 and not blend["source_mismatch_warning"]
    if ext_usable:
        consensus_sources.append(ext_dcf)
    if rel_val and rel_val > 0:
        consensus_sources.append(rel_val)
    consensus = round(sum(consensus_sources) / len(consensus_sources), 2) if consensus_sources else None

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

    f_score = compute_piotroski(financials, balance_sheet, cashflow, info)

    dcf_excluded = "sector_excluded_dcf" in blend["adjustments_applied"]

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
            "relative_factors": rel_factors,
            "blend_weights": blend["blend_weights"],
            "adjustments_applied": blend["adjustments_applied"],
            "source_mismatch_warning": blend["source_mismatch_warning"],
        },
        "dcf_breakdown": {
            "wacc": dcf_result["wacc"],
            "terminal_growth": dcf_result["terminal_growth"],
            "growth_rate": dcf_result["growth_rate"],
            "growth_source": dcf_result["growth_source"],
            "sector": sector,
            "enterprise_value": dcf_result["enterprise_value"],
            "equity_value": dcf_result["equity_value"],
            "net_debt": round(safe_get(info, "totalDebt", 0) - safe_get(info, "totalCash", 0)),
        },
        # When yfinance served this, the field is absent (matches prior behavior);
        # for FMP-sourced full valuations it records the primary source.
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
    stock = yf.Ticker(ticker)
    try:
        hist = stock.history(period=period)
    except Exception:
        hist = None  # rate-limit or transient error -> try fallback below

    if hist is None or hist.empty:
        for fb_fn, src in (
            (fmp.price_history_fallback, "fmp_fallback"),
            (finnhub.price_history_fallback, "finnhub_fallback"),
        ):
            fb = fb_fn(ticker, period)
            if fb is not None:
                log_source("price-history", ticker, src)
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

    return {"ticker": ticker, "dates": dates, "prices": prices}



@router.get("/fx-rate")
def fx_rate(base: str = "USD"):
    """Live conversion rate from `base` currency to USD (e.g. JPY -> 0.0064).
    Used to convert non-USD portfolio holdings into a common USD total."""
    base = (base or "USD").upper()
    if base == "USD":
        return {"base": base, "rate_to_usd": 1.0}
    try:
        fx = yf.Ticker(f"{base}USD=X")
        rate = None
        try:
            rate = float(fx.fast_info["last_price"])
        except Exception:
            info = fx.info or {}
            r = info.get("regularMarketPrice") or info.get("currentPrice")
            rate = float(r) if r is not None else None
        if rate and math.isfinite(rate) and rate > 0:
            return {"base": base, "rate_to_usd": rate}
    except Exception:
        pass
    return {"base": base, "rate_to_usd": None}



@router.get("/financials/{ticker}")
def financials_endpoint(ticker: str):
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)
    try:
        inc = stock.financials
        cf = stock.cashflow
        bs = stock.balance_sheet
    except Exception:
        inc = cf = bs = None

    # Empty income statement is the rate-limit symptom; try FMP then Finnhub.
    if inc is None or getattr(inc, "empty", True):
        for fb_fn, src in (
            (fmp.financials_fallback, "fmp_fallback"),
            (finnhub.financials_fallback, "finnhub_fallback"),
        ):
            fb = fb_fn(ticker)
            if fb is not None:
                log_source("financials", ticker, src)
                return fb
        # No fallback data: fall through with empty frames -> empty arrays (prior behavior).
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

    return {
        "ticker": ticker, "revenue": revenue, "eps": eps, "fcf": fcf,
        "gross_profit": gross_profit, "operating_income": operating_income,
        "net_income": net_income, "shares_outstanding": shares,
    }


@router.get("/metrics/{ticker}")
def metrics_endpoint(ticker: str):
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)
    try:
        info = stock.info or {}
    except Exception:
        info = {}

    # Empty info (no market cap / price) is the rate-limit symptom; try FMP then Finnhub.
    if not info or (info.get("marketCap") is None and info.get("currentPrice") is None
                    and info.get("regularMarketPrice") is None):
        for fb_fn, src in (
            (fmp.metrics_fallback, "fmp_fallback"),
            (finnhub.metrics_fallback, "finnhub_fallback"),
        ):
            fb = fb_fn(ticker)
            if fb is not None:
                log_source("metrics", ticker, src)
                return fb
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

    return {
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


