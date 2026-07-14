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

__all__ = ["safe_get","compute_cagr","safe_median","_series_vals","_fcf_list","extract_series","_trend","_fscore_band","_valuation_sentence","_text","_norm_company","_local"]

def safe_get(info: dict, key: str, default=None):
    v = info.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return v


def compute_cagr(values: list[float]) -> float:
    """CAGR from the FIRST to the LAST value over the ELAPSED number of periods.

    `values` is ordered oldest -> newest. The growth must be measured over the real
    time span: dropping interior negative years and dividing by (count_of_positives
    - 1) shortened the denominator and inflated the rate for any company that had a
    loss year. We instead require the two ENDPOINTS to be positive (a CAGR is
    undefined through a zero/negative base or terminal) and use the full elapsed
    span (len(values) - 1). Returns 0.0 when a CAGR can't be validly computed —
    callers already treat 0.0 as 'no historical growth signal'."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return 0.0
    first, last = vals[0], vals[-1]
    if first is None or last is None or first <= 0 or last <= 0:
        return 0.0  # CAGR undefined through a non-positive endpoint
    n = len(vals) - 1  # elapsed periods, NOT the count of positive years
    return (last / first) ** (1 / n) - 1


def safe_median(vals: list[float]) -> float | None:
    cleaned = [v for v in vals if v is not None and math.isfinite(v) and v > 0]
    return statistics.median(cleaned) if cleaned else None


def _series_vals(df, label, limit=5):
    if label not in df.index:
        return []
    return [float(v) for v in df.loc[label].dropna().values[:limit]]


def _fcf_list(cashflow, limit=5):
    """Free cash flow per period (newest-first), FCF = OCF + capex (capex negative).

    Pairs OCF and capex BY DATE, not by array position. The old positional pairing
    (`ocf.iloc[i] + capex.iloc[i]` after each series was dropna()'d independently)
    silently mismatched years whenever one series had a gap the other didn't — e.g.
    adding 2023 OCF to 2021 capex. If capex is genuinely unavailable we return the
    provider's own 'Free Cash Flow' row when present, else [] — we do NOT pass raw
    OCF off as FCF (it overstates cash flow by the entire capex bill)."""
    # Provider-computed FCF row is the most reliable when present.
    if "Free Cash Flow" in cashflow.index:
        fcf_row = cashflow.loc["Free Cash Flow"].dropna()
        if len(fcf_row):
            return [float(v) for v in fcf_row.values[:limit]]

    for ocf_label in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
        if ocf_label in cashflow.index:
            ocf = cashflow.loc[ocf_label].dropna()
            capex = None
            for cx in ["Capital Expenditure", "Capital Expenditures"]:
                if cx in cashflow.index:
                    capex = cashflow.loc[cx].dropna()
                    break
            if capex is None or not len(capex):
                return []  # no capex -> can't compute FCF; never return OCF as FCF
            # Join by date index (newest-first order preserved from the columns).
            out = []
            for dt in ocf.index:
                if dt in capex.index:
                    out.append(float(ocf[dt]) + float(capex[dt]))
                if len(out) >= limit:
                    break
            return out
    return []


# ---------------------------------------------------------------------------
# Piotroski F-Score (unchanged)
# ---------------------------------------------------------------------------


def extract_series(df, label, max_years=20):
    if label not in df.index:
        return []
    series = df.loc[label].dropna()
    items = []
    for date, val in list(series.items())[:max_years]:
        items.append({"year": str(date.year), "value": float(val)})
    items.reverse()
    return items



def _trend(series):
    """Return a short human-readable trend string from an oldest->newest list."""
    vals = [v for v in series if v is not None]
    if len(vals) < 2:
        return "n/a"
    first, last = vals[0], vals[-1]
    if first == 0:
        return "n/a"
    chg = (last - first) / abs(first) * 100
    direction = "rising" if last > first else "declining" if last < first else "flat"
    return f"{direction} ({chg:+.0f}% over {len(vals)}y)"



def _fscore_band(f_score):
    if f_score is None:
        return "unknown"
    if f_score <= 3:
        return "weak financial health"
    if f_score <= 6:
        return "average"
    return "strong"


def _valuation_sentence(price, fair_value):
    """Pre-computed, unambiguous valuation phrasing so the LLM doesn't conflate
    'upside vs price' with 'discount vs fair value'."""
    if not price or not fair_value or price <= 0 or fair_value <= 0:
        return "A reliable intrinsic value could not be estimated for this company."
    upside_pct = (fair_value - price) / price * 100          # move needed to reach FV
    diff_vs_fv = (fair_value - price) / fair_value * 100      # discount/premium vs FV
    if fair_value >= price:
        return (
            f"At the current price of ${price:.2f}, this stock offers {upside_pct:.1f}% "
            f"upside to reach our ${fair_value:.2f} fair-value estimate (equivalently, "
            f"it trades at a {diff_vs_fv:.1f}% discount to fair value)."
        )
    return (
        f"At the current price of ${price:.2f}, this stock trades {abs(upside_pct):.1f}% "
        f"ABOVE our ${fair_value:.2f} fair-value estimate — equivalently, about "
        f"{abs(diff_vs_fv):.1f}% overvalued versus fair value (a negative margin of safety)."
    )



def _text(node, path):
    if node is None:
        return None
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else None



def _norm_company(name):
    import re
    n = (name or "").upper()
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    for suf in [
        "INCORPORATED", "INC", "CORPORATION", "CORP", "COMPANY",
        "LTD", "PLC", "HOLDINGS", "HOLDING", "GROUP", "CLASS A", "CLASS B",
        "CL A", "CL B", "COM", "THE", "CAP", "STK",
    ]:
        n = re.sub(rf"\b{suf}\b", " ", n)
    return " ".join(n.split())


def _local(tag):
    return tag.rsplit("}", 1)[-1]


