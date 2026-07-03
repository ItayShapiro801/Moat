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

from config import FMP_API_KEY
from utils import *

def _run_dcf(base_fcf, growth_rate, wacc, tgr, net_debt, shares):
    """Run the 2-stage DCF formula for one parameter set, return per-share fair value + EV/equity."""
    proj = [round(base_fcf * (1 + growth_rate) ** y) for y in range(1, 6)]
    disc_sum = sum(p / (1 + wacc) ** y for y, p in enumerate(proj, 1))
    tv = 0
    if wacc > tgr and proj:
        tv = proj[-1] * (1 + tgr) / (wacc - tgr)
    tv_disc = tv / (1 + wacc) ** 5
    ev = disc_sum + tv_disc
    eq = ev - net_debt
    fv = eq / shares if shares else 0
    return fv, ev, eq


def _normalize_base_fcf(latest_fcf, fcf_5yr, revenue_5yr):
    """Smooth a capex-depressed latest FCF toward the company's typical FCF margin.

    Uses the MEDIAN free-cash-flow margin (FCF/revenue) across the window applied to
    the latest revenue as a "normalized" base FCF, then returns the larger of that
    and the raw latest FCF. This lifts a base that's temporarily crushed by an
    abnormal capex year toward the historical norm, but never drags a healthy base
    down. Requires aligned positive revenue; otherwise returns the raw latest FCF."""
    if not revenue_5yr or not fcf_5yr:
        return latest_fcf
    n = min(len(revenue_5yr), len(fcf_5yr))
    if n < 3:
        return latest_fcf
    rev = revenue_5yr[-n:]
    fcf = fcf_5yr[-n:]
    margins = [f / r for f, r in zip(fcf, rev) if r and r > 0]
    if len(margins) < 3:
        return latest_fcf
    latest_rev = rev[-1]
    if not latest_rev or latest_rev <= 0:
        return latest_fcf
    normalized = statistics.median(margins) * latest_rev
    # Only normalize UPWARD (recover a depressed base); never inflate a strong one.
    return max(latest_fcf, normalized) if normalized > 0 else latest_fcf


def compute_internal_dcf(info, fcf_5yr, sector, revenue_5yr=None):
    beta = safe_get(info, "beta", 1.0)
    shares = safe_get(info, "sharesOutstanding", 0)
    total_debt = safe_get(info, "totalDebt", 0)
    cash_val = safe_get(info, "totalCash", 0)
    net_debt = total_debt - cash_val

    # Base growth rate.
    # Order of trust: forward analyst estimates (best) -> historical growth of the
    # BUSINESS (revenue, then net income) -> historical growth of FCF (worst).
    # We avoid leaning on FCF CAGR because free cash flow is distorted by the capex
    # cycle: a company mid-buildout (e.g. hyperscaler datacenters) shows near-zero or
    # negative FCF growth even while revenue/earnings compound double digits, which
    # made the DCF absurdly undervalue such names (GOOGL -> ~$74, AAPL -> ~$86).
    fwd_earn = safe_get(info, "earningsGrowth")
    fwd_rev = safe_get(info, "revenueGrowth")
    growth_source = "historical_cagr"
    if fwd_earn is not None and fwd_earn != 0 and fwd_earn > -0.20:
        base_growth = fwd_earn
        growth_source = "forward_earnings"
    elif fwd_rev is not None and fwd_rev != 0:
        base_growth = fwd_rev
        growth_source = "forward_revenue"
    elif revenue_5yr and len(revenue_5yr) >= 3 and all(v > 0 for v in revenue_5yr[-3:]):
        # No forward estimate: use the revenue trajectory (the cleanest available
        # signal of the underlying business), not the capex-distorted FCF trajectory.
        base_growth = compute_cagr(revenue_5yr)
        growth_source = "historical_revenue_cagr"
    else:
        last_3 = fcf_5yr[-3:] if len(fcf_5yr) >= 3 else fcf_5yr
        base_growth = compute_cagr(last_3)
    base_growth = max(-0.15, min(base_growth, 0.35))

    # CAPM WACC
    base_wacc = 0.045 + beta * 0.05
    base_wacc = max(0.06, min(base_wacc, 0.13))

    # Terminal growth
    high_growth = {"Technology", "Communication Services", "Healthcare"}
    tgr = 0.035 if sector in high_growth else 0.025

    # Base FCF, normalized for the capex cycle. The latest year's FCF can be
    # temporarily depressed by a capex surge (datacenter buildout, a new plant). If
    # we have revenue, anchor the base to the company's typical FCF *margin* (median
    # FCF/revenue over the window) applied to the latest revenue, so one abnormal
    # capex year doesn't set the entire terminal value. Only smooths UPWARD toward
    # the historical norm (never inflates above it); falls back to raw latest FCF.
    base_fcf = fcf_5yr[-1] if fcf_5yr else 0
    base_fcf = _normalize_base_fcf(base_fcf, fcf_5yr, revenue_5yr)

    # Three scenarios: same data, different growth/discount assumptions
    scenarios_params = {
        "bear": {
            "growth": base_growth * 0.5,
            "wacc": base_wacc + 0.02,
        },
        "base": {
            "growth": base_growth,
            "wacc": base_wacc,
        },
        "bull": {
            "growth": min(base_growth * 1.4, 0.40),
            "wacc": max(base_wacc - 0.015, 0.05),
        },
    }

    scenarios = {}
    for name, p in scenarios_params.items():
        w = max(p["wacc"], tgr + 0.001)  # ensure wacc > tgr for terminal value
        fv, ev, eq = _run_dcf(base_fcf, p["growth"], w, tgr, net_debt, shares)
        scenarios[name] = {
            "value": round(fv, 2) if (eq > 0 and base_fcf > 0) else None,
            "growth": round(p["growth"], 4),
            "discount_rate": round(p["wacc"], 4),
            "enterprise_value": round(ev),
            "equity_value": round(eq),
        }

    base_meaningful = scenarios["base"]["value"] is not None

    return {
        "scenarios": scenarios,
        "fair_value": scenarios["base"]["value"] if base_meaningful else 0,
        "wacc": round(base_wacc, 4),
        "terminal_growth": tgr,
        "growth_rate": round(base_growth, 4),
        "growth_source": growth_source,
        "enterprise_value": scenarios["base"]["enterprise_value"],
        "equity_value": scenarios["base"]["equity_value"],
        "base_fcf": base_fcf,
        "meaningful": base_meaningful,
    }


# ---------------------------------------------------------------------------
# 2. External DCF Benchmark (FMP)
# ---------------------------------------------------------------------------


def fetch_external_dcf(ticker: str) -> float | None:
    # Route through the shared FMP client so it uses the same multi-key rotation
    # and rate-limit handling as the rest of the FMP calls.
    from services.fmp_fallback import _fmp_get
    data = _fmp_get(f"discounted-cash-flow?symbol={ticker}")
    try:
        if isinstance(data, list) and data:
            return float(data[0].get("dcf", 0))
        if isinstance(data, dict) and "dcf" in data:
            return float(data.get("dcf", 0))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Merger / reorganization detection
# ---------------------------------------------------------------------------

