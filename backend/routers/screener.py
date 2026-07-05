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

# Cache lives in the backend root (written by run_screener.py); this module is
# one level down in routers/, so resolve up one directory.
SCREENER_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screener_cache.json"
)



# Screener results are a batch snapshot (rebuilt via run_screener.py), valid for a
# month: fundamentals move slowly, but serving a years-old snapshot forever would
# silently lie. Past the window we still serve (stale beats blank) with an honest
# stale flag + note the UI can surface.
_SCREENER_MAX_AGE_DAYS = 31


@router.get("/screener")
def screener(min_margin_of_safety: float = -1000, min_f_score: int = 0):
    if not os.path.exists(SCREENER_CACHE_PATH):
        return {
            "last_updated": None,
            "count": 0,
            "results": [],
            "note": "Screener cache not built yet. Run: python backend/run_screener.py",
        }
    try:
        with open(SCREENER_CACHE_PATH) as f:
            cache = json_mod.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Could not read screener cache.")

    stale = False
    try:
        import datetime as _dt
        built = _dt.datetime.fromisoformat(str(cache.get("last_updated", "")).replace("Z", "+00:00"))
        age_days = (_dt.datetime.now(_dt.timezone.utc) - built).days
        stale = age_days > _SCREENER_MAX_AGE_DAYS
    except Exception:
        pass

    matches = []
    for row in cache.get("results", []):
        mos = row.get("margin_of_safety_pct")
        fs = row.get("f_score")
        if mos is None:
            continue
        if mos < min_margin_of_safety:
            continue
        if fs is None or fs < min_f_score:
            continue
        matches.append(row)

    matches.sort(key=lambda r: r.get("margin_of_safety_pct") or -1e9, reverse=True)
    return {
        "last_updated": cache.get("last_updated"),
        "count": len(matches),
        "total_screened": len(cache.get("results", [])),
        "results": matches,
        **({
            "stale": True,
            "note": "Screener data is more than a month old — valuations may have "
                    "drifted. A rebuild is pending.",
        } if stale else {}),
    }


# ---------------------------------------------------------------------------
# Portfolio Key Insights (LLM, on-demand)
# ---------------------------------------------------------------------------

