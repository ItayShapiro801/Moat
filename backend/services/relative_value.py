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

from utils import *

def detect_reorganization(stock, info):
    """Detect a recent merger/reorg that corrupts multi-year per-share history.

    Two signatures: (a) a large share-count jump (>40%) between consecutive
    periods — the classic stock-for-stock merger tell — checked on BOTH annual
    and quarterly data (a 2026 merger may not be in the annual statements yet);
    (b) an extreme earnings/revenue discontinuity (a sign flip or a >3x swing)
    between consecutive fiscal years, which means pre- and post-merger entity
    numbers are mixed in the same series. Returns (bool, [reasons])."""
    reasons = []

    def _shares_jump(series):
        if not series or len(series) < 2:
            return False
        for i in range(len(series) - 1):
            a, b = series[i], series[i + 1]
            if a and b and min(a, b) > 0 and abs(a - b) / min(a, b) > 0.40:
                return True
        return False

    try:
        annual = None
        bs = stock.balance_sheet
        for lbl in ["Share Issued", "Common Stock Shares Outstanding", "Ordinary Shares Number"]:
            if lbl in bs.index:
                annual = [float(v) for v in bs.loc[lbl].dropna().values[:5]]
                break
        quarterly = None
        try:
            qbs = stock.quarterly_balance_sheet
            for lbl in ["Share Issued", "Common Stock Shares Outstanding", "Ordinary Shares Number"]:
                if lbl in qbs.index:
                    quarterly = [float(v) for v in qbs.loc[lbl].dropna().values[:6]]
                    break
        except Exception:
            pass
        if _shares_jump(annual) or _shares_jump(quarterly):
            reasons.append("share_count_jump_>40%")
    except Exception:
        pass

    try:
        fin = stock.financials
        for label in ["Net Income", "Total Revenue"]:
            vals = _series_vals(fin, label, 5)
            for i in range(len(vals) - 1):
                newer, older = vals[i], vals[i + 1]
                if older is None or newer is None or older == 0:
                    continue
                # Sign flip (e.g. profit -> loss) with material magnitude.
                if (newer < 0) != (older < 0) and (abs(newer) > 1e8 or abs(older) > 1e8):
                    reasons.append(f"{label.lower().replace(' ', '_')}_sign_flip")
                    break
                if abs(older) > 0 and abs(newer / older) > 3.0:
                    reasons.append(f"{label.lower().replace(' ', '_')}_>3x_discontinuity")
                    break
    except Exception:
        pass

    return (len(reasons) > 0, sorted(set(reasons)))


# ---------------------------------------------------------------------------
# 3. Multi-Factor Relative Value (own 5yr historical medians)
# ---------------------------------------------------------------------------


def compute_relative_value(stock, info, current_price, reorganized=False):
    financials = stock.financials
    cashflow = stock.cashflow
    balance_sheet = stock.balance_sheet
    shares = safe_get(info, "sharesOutstanding", 0)
    if not shares or shares <= 0:
        return None, {}

    # Gather annual per-share metrics
    ni_vals = _series_vals(financials, "Net Income", 5)
    rev_vals = _series_vals(financials, "Total Revenue", 5)
    fcf_vals = _fcf_list(cashflow, 5)

    ebitda_vals = []
    if "EBITDA" in financials.index:
        ebitda_vals = _series_vals(financials, "EBITDA", 5)
    elif "Operating Income" in financials.index:
        ebitda_vals = _series_vals(financials, "Operating Income", 5)

    # Shareholder equity per year (for Price/Book multiples)
    equity_vals = []
    for lbl in ["Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"]:
        if lbl in balance_sheet.index:
            equity_vals = [float(v) for v in balance_sheet.loc[lbl].dropna().values[:5]]
            break

    # Get share counts per year for per-share calc
    sh_series = None
    for lbl in ["Share Issued", "Common Stock Shares Outstanding", "Ordinary Shares Number"]:
        if lbl in balance_sheet.index:
            sh_series = balance_sheet.loc[lbl].dropna()
            break
    sh_vals = [float(v) for v in sh_series.values[:5]] if sh_series is not None else [shares] * 5

    # Get historical year-end prices
    try:
        hist = stock.history(period="5y")
        yearly_prices = {}
        for dt in hist.index:
            yearly_prices[dt.year] = float(hist.loc[dt, "Close"])
    except Exception:
        yearly_prices = {}

    fiscal_years = []
    if financials.columns is not None:
        fiscal_years = [c.year for c in list(financials.columns)[:5]]

    def historical_multiples(metric_vals, per_share=True):
        multiples = []
        for i, yr in enumerate(fiscal_years):
            if i >= len(metric_vals) or i >= len(sh_vals):
                continue
            price = yearly_prices.get(yr)
            if not price or price <= 0:
                continue
            val = metric_vals[i]
            if per_share:
                s = sh_vals[i] if i < len(sh_vals) else shares
                if not s or s <= 0: continue
                ps_val = val / s
            else:
                ps_val = val
            if ps_val and ps_val > 0:
                multiples.append(price / ps_val)
        return multiples

    pe_mults = historical_multiples(ni_vals)
    ps_mults = historical_multiples(rev_vals)
    ev_ebitda_mults = historical_multiples(ebitda_vals)
    pfcf_mults = historical_multiples(fcf_vals)
    pb_mults = historical_multiples(equity_vals)

    # Current TTM per-share values
    eps_ttm = safe_get(info, "trailingEps")
    rev_ps = safe_get(info, "revenuePerShare")
    fcf_ttm = safe_get(info, "freeCashflow")
    fcf_ps = fcf_ttm / shares if fcf_ttm and shares else None

    ev = safe_get(info, "enterpriseValue")
    ebitda_ttm = safe_get(info, "ebitda")

    # Implied values from each factor
    factors = {}
    med_pe = safe_median(pe_mults)
    if med_pe and eps_ttm and eps_ttm > 0:
        factors["pe"] = {"median": round(med_pe, 1), "implied": round(med_pe * eps_ttm, 2), "weight": 0.20}

    med_ps = safe_median(ps_mults)
    if med_ps and rev_ps and rev_ps > 0:
        factors["ps"] = {"median": round(med_ps, 1), "implied": round(med_ps * rev_ps, 2), "weight": 0.10}

    med_ev_eb = safe_median(ev_ebitda_mults)
    if med_ev_eb and ebitda_ttm and ebitda_ttm > 0 and ev and shares:
        implied_ev = med_ev_eb * ebitda_ttm
        net_debt = ev - (current_price * shares)
        implied_eq = implied_ev - net_debt
        factors["ev_ebitda"] = {"median": round(med_ev_eb, 1), "implied": round(implied_eq / shares, 2), "weight": 0.30}

    med_pfcf = safe_median(pfcf_mults)
    if med_pfcf and fcf_ps and fcf_ps > 0:
        factors["pfcf"] = {"median": round(med_pfcf, 1), "implied": round(med_pfcf * fcf_ps, 2), "weight": 0.40}

    # --- Berkshire-type conglomerate detection ---
    # Large Financial Services conglomerates (e.g. Berkshire) carry huge equity
    # portfolios whose GAAP-mandated mark-to-market swings make headline earnings
    # (and thus P/E) wildly unreliable. For these, anchor on Price/Book instead.
    sector = safe_get(info, "sector", "")
    market_cap = safe_get(info, "marketCap", 0) or 0
    book_value_ps = safe_get(info, "bookValue")
    med_pb = safe_median(pb_mults)

    eps_per_year = [
        ni_vals[i] / sh_vals[i]
        for i in range(min(len(ni_vals), len(sh_vals)))
        if sh_vals[i]
    ]
    extreme_eps_volatility = False
    for i in range(len(eps_per_year) - 1):
        older = eps_per_year[i + 1]
        newer = eps_per_year[i]
        if older and abs(older) > 0 and abs((newer - older) / abs(older)) > 0.5:
            extreme_eps_volatility = True
            break

    is_conglomerate = (
        sector == "Financial Services"
        and market_cap > 200e9
        and extreme_eps_volatility
        and med_pb is not None
        and book_value_ps is not None
        and book_value_ps > 0
    )

    if is_conglomerate:
        # Anchor on Price/Book; keep P/E only as a near-zero sanity signal.
        # (Other factors left out — FCF/EBITDA are also distorted by insurance
        # float and the securities portfolio for these conglomerates.)
        factors = {
            "pb": {"median": round(med_pb, 1), "implied": round(med_pb * book_value_ps, 2), "weight": 0.90},
        }
        if med_pe and eps_ttm and eps_ttm > 0:
            factors["pe"] = {"median": round(med_pe, 1), "implied": round(med_pe * eps_ttm, 2), "weight": 0.10}

    # --- Merger / reorganization handling ---
    # Multi-year historical multiples (esp. P/FCF) are corrupted when pre- and
    # post-merger numbers mix in the same series, producing absurd implied values.
    # For a reorganized entity, ignore the historical-median multiples and anchor
    # on FORWARD/CURRENT data only: forward P/E on forward EPS, plus current P/S
    # and P/B (capped to sane multiples). This trades precision for not lying.
    if reorganized and not is_conglomerate:
        fwd_eps = safe_get(info, "forwardEps")
        factors = {}
        fwd_pe = med_pe if (med_pe and 0 < med_pe < 40) else 18.0
        if fwd_eps and fwd_eps > 0:
            factors["forward_pe"] = {"median": round(fwd_pe, 1), "implied": round(fwd_pe * fwd_eps, 2), "weight": 0.50}
        if med_ps and 0 < med_ps < 10 and rev_ps and rev_ps > 0:
            factors["ps"] = {"median": round(med_ps, 1), "implied": round(med_ps * rev_ps, 2), "weight": 0.25}
        if book_value_ps and book_value_ps > 0:
            pb_mult = med_pb if (med_pb and 0 < med_pb < 5) else 1.0
            factors["pb"] = {"median": round(pb_mult, 1), "implied": round(pb_mult * book_value_ps, 2), "weight": 0.25}

    if not factors:
        return None, {}

    # Redistribute weights proportionally if any factors missing
    total_w = sum(f["weight"] for f in factors.values())
    if total_w > 0 and total_w != 1.0:
        for f in factors.values():
            f["weight"] = f["weight"] / total_w

    relative_val = sum(f["implied"] * f["weight"] for f in factors.values())
    return round(relative_val, 2), factors


# ---------------------------------------------------------------------------
# 4-7. Blend Engine
# ---------------------------------------------------------------------------

