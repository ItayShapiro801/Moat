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

def compute_piotroski(financials, balance_sheet, cashflow, info) -> int:
    score = 0
    try:
        if len(financials.columns) < 2:
            return 0
        ni_curr = financials.loc["Net Income"].iloc[0] if "Net Income" in financials.index else 0
        ni_prev = financials.loc["Net Income"].iloc[1] if "Net Income" in financials.index else 0
        ta_curr = balance_sheet.loc["Total Assets"].iloc[0] if "Total Assets" in balance_sheet.index else 1
        ta_prev = balance_sheet.loc["Total Assets"].iloc[1] if "Total Assets" in balance_sheet.index else 1
        roa_curr = ni_curr / ta_curr if ta_curr else 0
        roa_prev = ni_prev / ta_prev if ta_prev else 0
        if roa_curr > 0: score += 1
        if roa_curr > roa_prev: score += 1
        ocf_curr = 0
        for lbl in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
            if lbl in cashflow.index:
                ocf_curr = cashflow.loc[lbl].iloc[0]; break
        if ocf_curr > 0: score += 1
        if ocf_curr > ni_curr: score += 1
        lt_curr = lt_prev = 0
        for lbl in ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"]:
            if lbl in balance_sheet.index:
                lt_curr = balance_sheet.loc[lbl].iloc[0] or 0
                lt_prev = balance_sheet.loc[lbl].iloc[1] or 0; break
        if (lt_curr / ta_curr if ta_curr else 0) < (lt_prev / ta_prev if ta_prev else 0): score += 1
        ca_c = balance_sheet.loc["Current Assets"].iloc[0] or 0 if "Current Assets" in balance_sheet.index else 0
        ca_p = balance_sheet.loc["Current Assets"].iloc[1] or 0 if "Current Assets" in balance_sheet.index else 0
        cl_c = balance_sheet.loc["Current Liabilities"].iloc[0] or 1 if "Current Liabilities" in balance_sheet.index else 1
        cl_p = balance_sheet.loc["Current Liabilities"].iloc[1] or 1 if "Current Liabilities" in balance_sheet.index else 1
        if (ca_c / cl_c if cl_c else 0) > (ca_p / cl_p if cl_p else 0): score += 1
        sh_c = safe_get(info, "sharesOutstanding", 0)
        sh_p = sh_c
        for lbl in ["Share Issued", "Common Stock Shares Outstanding"]:
            if lbl in balance_sheet.index:
                sh_c = balance_sheet.loc[lbl].iloc[0] or sh_c
                sh_p = balance_sheet.loc[lbl].iloc[1] or sh_p; break
        if sh_c <= sh_p: score += 1
        rev_c = financials.loc["Total Revenue"].iloc[0] if "Total Revenue" in financials.index else 0
        rev_p = financials.loc["Total Revenue"].iloc[1] if "Total Revenue" in financials.index else 0
        cog_c = financials.loc["Cost Of Revenue"].iloc[0] if "Cost Of Revenue" in financials.index else 0
        cog_p = financials.loc["Cost Of Revenue"].iloc[1] if "Cost Of Revenue" in financials.index else 0
        gm_c = (rev_c - cog_c) / rev_c if rev_c else 0
        gm_p = (rev_p - cog_p) / rev_p if rev_p else 0
        if gm_c > gm_p: score += 1
        if (rev_c / ta_curr if ta_curr else 0) > (rev_p / ta_prev if ta_prev else 0): score += 1
    except Exception:
        pass
    return score


# ---------------------------------------------------------------------------
# 1. Internal 2-Stage DCF
# ---------------------------------------------------------------------------

