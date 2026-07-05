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

def detect_cyclical(fcf_5yr):
    """Dynamic cyclicality detection via FCF volatility, independent of sector label.
    Cyclical if coefficient of variation (stdev/mean) > 0.5, OR if any year is
    negative while others are strongly positive."""
    vals = [float(v) for v in fcf_5yr if v is not None]
    if len(vals) < 3:
        return False
    neg = [v for v in vals if v < 0]
    pos = [v for v in vals if v > 0]
    # Negative year amid positive years => cyclical swing
    if neg and pos and max(pos) > 0:
        return True
    mean = statistics.mean(vals)
    if mean > 0:
        cv = statistics.pstdev(vals) / mean
        if cv > 0.5:
            return True
    return False



def compute_blended_valuation(
    internal_dcf_result, external_dcf_val, relative_val,
    sector, current_price, fcf_5yr, info, stock, low_confidence_valuation=False,
    is_financial=None,
):
    # `low_confidence_valuation` is set when the multi-year multiples were deemed
    # unreliable (a detected merger, or a result wildly out of line with price)
    # and the estimate had to be forward/current-anchored instead.
    adjustments = []
    if low_confidence_valuation:
        adjustments.append("multiples_unreliable")
    internal_fv = internal_dcf_result["fair_value"] if internal_dcf_result["meaningful"] else None
    ext_fv = external_dcf_val

    # --- Sector Exclusion (balance-sheet financials) ---
    # Caller passes an industry-aware flag (banks/insurers only — payment networks
    # like Visa keep their DCF); fall back to the blunt sector test if not given.
    if is_financial is None:
        is_financial = sector in FINANCIAL_SECTORS
    if is_financial:
        adjustments.append("sector_excluded_dcf")
        internal_fv = None
        ext_fv = None

    # --- Cyclical Adjustment (dynamic, by FCF volatility) ---
    is_cyclical = detect_cyclical(fcf_5yr) and not is_financial
    if is_cyclical:
        adjustments.append("cyclical_avg_fcf")

    # --- Hyper-Capex Normalization ---
    hyper_capex = False
    if not is_financial:
        cashflow = stock.cashflow
        financials = stock.financials
        rev_vals = _series_vals(financials, "Total Revenue", 5)
        capex_vals = []
        for lbl in ["Capital Expenditure", "Capital Expenditures"]:
            if lbl in cashflow.index:
                capex_vals = [abs(float(v)) for v in cashflow.loc[lbl].dropna().values[:5]]
                break
        if rev_vals and capex_vals and len(rev_vals) >= 2 and len(capex_vals) >= 2:
            hist_ratios = [capex_vals[i] / rev_vals[i] for i in range(min(len(capex_vals), len(rev_vals))) if rev_vals[i] > 0]
            if hist_ratios:
                avg_ratio = statistics.mean(hist_ratios)
                current_ratio = capex_vals[0] / rev_vals[0] if rev_vals[0] > 0 else 0
                if current_ratio > avg_ratio * 1.5 and avg_ratio > 0:
                    hyper_capex = True
                    adjustments.append("hyper_capex")

    # --- Source Mismatch Warning ---
    source_mismatch = False
    # (a) Internal DCF vs External DCF
    if internal_fv and ext_fv and ext_fv > 0:
        ratio = internal_fv / ext_fv if ext_fv else 999
        if ratio > 1.5 or ratio < 0.667:
            source_mismatch = True
    # (b) Internal/Base DCF vs Relative Value disagree by >2x (Fix 3) —
    # triggers regardless of whether External DCF agrees with either
    if internal_fv and internal_fv > 0 and relative_val and relative_val > 0:
        rv_ratio = max(internal_fv, relative_val) / min(internal_fv, relative_val)
        if rv_ratio > 2.0:
            source_mismatch = True

    # --- Blend Weights ---
    if is_financial:
        w_dcf, w_ext, w_rel = 0.0, 0.0, 1.0
    elif is_cyclical or hyper_capex:
        w_dcf, w_ext, w_rel = 0.10, 0.30, 0.60
    else:
        w_dcf, w_ext, w_rel = 0.15, 0.35, 0.50

    if source_mismatch:
        w_ext = 0.0
        w_dcf = 0.15 if not (is_cyclical or hyper_capex) else 0.10
        w_rel = 1.0 - w_dcf

    # Fallback: if external_dcf unavailable, shift its weight
    ext_available = ext_fv and ext_fv > 0 and not source_mismatch
    if not ext_available and not is_financial:
        w_dcf_fallback = 0.35
        w_rel_fallback = 0.65
        w_dcf = w_dcf_fallback
        w_rel = w_rel_fallback
        w_ext = 0.0

    sources = []
    if internal_fv and internal_fv > 0:
        sources.append(("dcf", internal_fv, w_dcf))
    if ext_available:
        sources.append(("ext", ext_fv, w_ext))
    if relative_val and relative_val > 0:
        sources.append(("rel", relative_val, w_rel))

    if not sources:
        return {
            "fair_value": None,
            "fair_value_low": None,
            "fair_value_high": None,
            "confidence": "low",
            "blend_weights": {"dcf": w_dcf, "external": w_ext, "multiples": w_rel},
            "adjustments_applied": adjustments,
            "source_mismatch_warning": source_mismatch,
        }

    total_w = sum(s[2] for s in sources)
    weighted = sum(s[1] * s[2] / total_w for s in sources) if total_w > 0 else 0

    # --- Confidence + Sanity Guard ---
    confidence = "high"
    fair_low = None
    fair_high = None

    if internal_fv and internal_fv > 0 and relative_val and relative_val > 0:
        ratio = max(internal_fv, relative_val) / min(internal_fv, relative_val)
        if ratio > 2.0:
            confidence = "low"
            weighted = max(relative_val * 0.75, min(weighted, relative_val * 1.25))
            fair_low = round(min(internal_fv, relative_val), 2)
            fair_high = round(max(internal_fv, relative_val), 2)
        elif ratio > 1.4:
            confidence = "medium"

    # Fix B: independence check. The internal DCF and relative value frequently
    # share the same forward-growth input, so their mutual agreement is weak
    # evidence. If FMP's external DCF (the one truly independent source) exists
    # and disagrees with the internal average by >2x, downgrade high -> medium.
    if (
        confidence == "high"
        and ext_fv and ext_fv > 0
        and internal_fv and internal_fv > 0
        and relative_val and relative_val > 0
    ):
        internal_avg = (internal_fv + relative_val) / 2
        ext_ratio = max(internal_avg, ext_fv) / min(internal_avg, ext_fv)
        if ext_ratio > 2.0:
            confidence = "medium"

    # When the multi-year multiples were unreliable, the estimate is forward-
    # anchored and inherently low-confidence.
    if low_confidence_valuation:
        confidence = "low"

    weighted = round(weighted, 2)

    return {
        "fair_value": weighted,
        "fair_value_low": fair_low,
        "fair_value_high": fair_high,
        "confidence": confidence,
        "blend_weights": {
            "dcf": round(w_dcf, 2),
            "external": round(w_ext, 2),
            "multiples": round(w_rel, 2),
        },
        "adjustments_applied": adjustments,
        "source_mismatch_warning": source_mismatch,
    }


# ---------------------------------------------------------------------------
# Search endpoint
# ---------------------------------------------------------------------------

