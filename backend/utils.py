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
    positives = [v for v in values if v > 0]
    if len(positives) < 2:
        return 0.0
    first, last = positives[0], positives[-1]
    n = len(positives) - 1
    return (last / first) ** (1 / n) - 1


def safe_median(vals: list[float]) -> float | None:
    cleaned = [v for v in vals if v is not None and math.isfinite(v) and v > 0]
    return statistics.median(cleaned) if cleaned else None


def _series_vals(df, label, limit=5):
    if label not in df.index:
        return []
    return [float(v) for v in df.loc[label].dropna().values[:limit]]


def _fcf_list(cashflow, limit=5):
    for ocf_label in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
        if ocf_label in cashflow.index:
            ocf = cashflow.loc[ocf_label].dropna()
            capex = None
            for cx in ["Capital Expenditure", "Capital Expenditures"]:
                if cx in cashflow.index:
                    capex = cashflow.loc[cx].dropna()
                    break
            if capex is not None:
                n = min(len(ocf), len(capex), limit)
                return [float(ocf.iloc[i] + capex.iloc[i]) for i in range(n)]
            return [float(v) for v in ocf.values[:limit]]
    if "Free Cash Flow" in cashflow.index:
        return [float(v) for v in cashflow.loc["Free Cash Flow"].dropna().values[:limit]]
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
        "INCORPORATED", "INC", "CORPORATION", "CORP", "COMPANY", "CompanY",
        "LTD", "PLC", "HOLDINGS", "HOLDING", "GROUP", "CLASS A", "CLASS B",
        "CL A", "CL B", "COM", "THE", "CAP", "STK",
    ]:
        n = re.sub(rf"\b{suf}\b", " ", n)
    return " ".join(n.split())


def _local(tag):
    return tag.rsplit("}", 1)[-1]


