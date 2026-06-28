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


def compute_internal_dcf(info, fcf_5yr, sector):
    beta = safe_get(info, "beta", 1.0)
    shares = safe_get(info, "sharesOutstanding", 0)
    total_debt = safe_get(info, "totalDebt", 0)
    cash_val = safe_get(info, "totalCash", 0)
    net_debt = total_debt - cash_val

    # Base growth rate
    fwd_earn = safe_get(info, "earningsGrowth")
    fwd_rev = safe_get(info, "revenueGrowth")
    growth_source = "historical_cagr"
    if fwd_earn is not None and fwd_earn != 0 and fwd_earn > -0.20:
        base_growth = fwd_earn
        growth_source = "forward_earnings"
    elif fwd_rev is not None and fwd_rev != 0:
        base_growth = fwd_rev
        growth_source = "forward_revenue"
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

    base_fcf = fcf_5yr[-1] if fcf_5yr else 0

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
    if not FMP_API_KEY:
        return None
    url = f"https://financialmodelingprep.com/stable/discounted-cash-flow?symbol={ticker}&apikey={FMP_API_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json_mod.loads(resp.read())
        if isinstance(data, list) and data:
            return float(data[0].get("dcf", 0))
        if isinstance(data, dict):
            return float(data.get("dcf", 0))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Merger / reorganization detection
# ---------------------------------------------------------------------------

