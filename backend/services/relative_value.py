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

# Clean forward-split ratios to recognise (a split is NOT a reorganization).
_SPLIT_RATIOS = (2, 3, 4, 5, 6, 7, 8, 10, 15, 20)
_SPLIT_TOL = 0.12  # a jump within 12% of a clean ratio counts as that split


def _normalize_splits(series):
    """Back-adjust clean-ratio stock splits in a newest-first share-count series
    so every period is on the current share basis. A forward N:1 split shows up,
    going newest -> oldest, as the newer count being ~N x the older count; we
    scale the older periods up by N. Returns (adjusted_newest_first, did_split).
    Real share changes (mergers, issuance) are NOT clean ratios and pass through
    untouched so the merger guard can still see them."""
    if not series or len(series) < 2:
        return list(series or []), False
    adj = [float(v) for v in series]
    did_split = False
    for i in range(len(adj) - 1):
        newer, older = adj[i], adj[i + 1]
        if not newer or not older or newer <= 0 or older <= 0:
            continue
        ratio = newer / older  # >1 for a forward split going newer->older
        for r in _SPLIT_RATIOS:
            if abs(ratio - r) / r < _SPLIT_TOL:
                for j in range(i + 1, len(adj)):
                    adj[j] *= r
                did_split = True
                break
    return adj, did_split


def _largest_jump(series):
    """Largest consecutive relative change in a series, or 0 if <2 points."""
    if not series or len(series) < 2:
        return 0.0
    worst = 0.0
    for i in range(len(series) - 1):
        a, b = series[i], series[i + 1]
        if a and b and min(a, b) > 0:
            worst = max(worst, abs(a - b) / min(a, b))
    return worst


def _share_series(bs):
    for lbl in ["Share Issued", "Common Stock Shares Outstanding", "Ordinary Shares Number"]:
        if lbl in bs.index:
            return [float(v) for v in bs.loc[lbl].dropna().values[:6]]
    return None


def detect_reorganization(stock, info):
    """Detect a recent merger/reorganization that genuinely corrupts multi-year
    per-share history — distinct from a stock split or ordinary earnings volatility.

    A real reorganization (e.g. a stock-for-stock merger) shows BOTH signatures
    together: a large share-count jump that is NOT explained by a clean split
    ratio, AND an earnings/revenue discontinuity (sign flip or >3x swing) in the
    same window. Requiring both avoids two false positives:
      - a stable conglomerate (e.g. Berkshire) whose GAAP mark-to-market earnings
        swing wildly but whose share count is stable — earnings volatility alone
        is NOT a reorganization;
      - a stock split (e.g. NVDA 10:1) — a clean-ratio share jump with no earnings
        discontinuity — which is normalized elsewhere, not flagged.
    Returns (bool reorganized, [reasons])."""
    reasons = []
    share_jump = False
    try:
        annual = _share_series(stock.balance_sheet)
        quarterly = None
        try:
            quarterly = _share_series(stock.quarterly_balance_sheet)
        except Exception:
            pass
        for series in (annual, quarterly):
            if not series:
                continue
            adjusted, _ = _normalize_splits(series)  # remove split artifacts first
            if _largest_jump(adjusted) > 0.40:        # residual = real share change
                share_jump = True
                break
    except Exception:
        pass

    earnings_discontinuity = False
    try:
        fin = stock.financials
        for label in ["Net Income", "Total Revenue"]:
            vals = _series_vals(fin, label, 5)
            for i in range(len(vals) - 1):
                newer, older = vals[i], vals[i + 1]
                if older is None or newer is None or older == 0:
                    continue
                if (newer < 0) != (older < 0) and (abs(newer) > 1e8 or abs(older) > 1e8):
                    earnings_discontinuity = True
                    reasons.append(f"{label.lower().replace(' ', '_')}_sign_flip")
                    break
                if abs(older) > 0 and abs(newer / older) > 3.0:
                    earnings_discontinuity = True
                    reasons.append(f"{label.lower().replace(' ', '_')}_>3x_discontinuity")
                    break
    except Exception:
        pass

    # A genuine reorganization requires BOTH a real (non-split) share jump AND an
    # earnings discontinuity. Either alone is a conglomerate or a split, not a merger.
    reorganized = share_jump and earnings_discontinuity
    if reorganized:
        reasons.append("share_count_jump_>40%")
    return (reorganized, sorted(set(reasons)) if reorganized else [])


# ---------------------------------------------------------------------------
# 3. Multi-Factor Relative Value (own 5yr historical medians)
# ---------------------------------------------------------------------------


def compute_relative_value(stock, info, current_price, reorganized=False):
    financials = stock.financials
    cashflow = stock.cashflow
    balance_sheet = stock.balance_sheet
    shares = safe_get(info, "sharesOutstanding", 0)
    if not shares or shares <= 0:
        return None, {}, False

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

    # Get share counts per year for per-share calc. Back-adjust clean-ratio stock
    # splits so the multi-year per-share history is on a single, consistent basis
    # (e.g. NVDA's 10:1 split would otherwise make older years' per-share metrics
    # 10x too large). Real (non-split) share changes pass through unadjusted.
    sh_series = None
    for lbl in ["Share Issued", "Common Stock Shares Outstanding", "Ordinary Shares Number"]:
        if lbl in balance_sheet.index:
            sh_series = balance_sheet.loc[lbl].dropna()
            break
    raw_sh = [float(v) for v in sh_series.values[:5]] if sh_series is not None else [shares] * 5
    sh_vals, _ = _normalize_splits(raw_sh)

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

    # --- Unreliable multi-year multiples -> forward/current anchoring ---
    # One consistent rule for every situation where the historical-median
    # multiples can't be trusted:
    #   * a detected merger/reorganization (pre- and post-merger numbers mixed), OR
    #   * a result wildly divorced from the market price (>5x or <0.2x), which is
    #     the tell-tale of corrupted history regardless of cause (restructuring,
    #     a near-zero denominator year, etc.).
    # In those cases, ignore the historical medians and anchor on FORWARD/CURRENT
    # data: forward P/E on forward EPS, plus current P/S and P/B (sane multiples).
    # This trades precision for not lying. Conglomerates keep their P/B anchor.
    prelim = None
    if factors and not is_conglomerate:
        _w = sum(f["weight"] for f in factors.values())
        prelim = sum(f["implied"] * f["weight"] for f in factors.values()) / _w if _w else None
    out_of_range = bool(
        prelim is not None and current_price and current_price > 0
        and (prelim > current_price * 5 or prelim < current_price * 0.2)
    )
    unreliable = (reorganized or out_of_range) and not is_conglomerate

    if unreliable:
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
        return None, {}, unreliable

    # Redistribute weights proportionally if any factors missing
    total_w = sum(f["weight"] for f in factors.values())
    if total_w > 0 and total_w != 1.0:
        for f in factors.values():
            f["weight"] = f["weight"] / total_w

    relative_val = sum(f["implied"] * f["weight"] for f in factors.values())
    # Final clamp: never report a relative value wildly outside the market price.
    if current_price and current_price > 0:
        relative_val = max(min(relative_val, current_price * 5), current_price * 0.2)
    return round(relative_val, 2), factors, unreliable


# ---------------------------------------------------------------------------
# 4-7. Blend Engine
# ---------------------------------------------------------------------------

