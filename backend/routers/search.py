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

router = APIRouter()

SEARCHABLE_TYPES = {"EQUITY", "ETF", "CRYPTOCURRENCY", "INDEX", "MUTUALFUND"}


@router.get("/search")
def search(q: str = "", equity_only: bool = False):
    q = q.strip()
    if not q:
        return []
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.request.quote(q)}&quotesCount=8&newsCount=0&listsCount=0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json_mod.loads(resp.read())
    except Exception:
        return []
    results = []
    for item in data.get("quotes", []):
        qt = item.get("quoteType")
        if qt not in SEARCHABLE_TYPES:
            continue
        # equity_only is used by Compare (DCF/F-Score/investors need a company).
        if equity_only and qt != "EQUITY":
            continue
        results.append({
            "symbol": item.get("symbol", ""),
            "name": item.get("longname") or item.get("shortname", ""),
            "quote_type": qt,
        })
    ql = q.lower()
    def rank(r):
        sym = r["symbol"]
        name = r["name"].lower()
        return (sym.lower() != ql, not name.startswith(ql), "." in sym, len(sym))
    results.sort(key=rank)
    return results[:8]


# ---------------------------------------------------------------------------
# Main analyze endpoint
# ---------------------------------------------------------------------------

