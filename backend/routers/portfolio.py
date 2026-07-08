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

from models import PortfolioInsightsBody
from services.llm_providers import *

router = APIRouter()

PORTFOLIO_INSIGHTS_SYSTEM = (
    "You are a seasoned portfolio analyst reviewing an investor's CURRENT holdings. "
    "Reason ONLY from the data provided and be specific — reference actual ticker "
    "symbols and their numbers (allocation %, intrinsic value vs price, F-score, "
    "gain/loss). Do not give generic advice. Provide:\n"
    "(1) Overall portfolio health summary.\n"
    "(2) Concentration risk assessment — is it too concentrated in a few names? Name "
    "the largest positions by allocation.\n"
    "(3) Which holdings look most OVERVALUED and most UNDERVALUED right now per the "
    "provided margin-of-safety/intrinsic-value data — name them specifically.\n"
    "(4) One overall portfolio score 0-10 with a brief justification.\n\n"
    "CRITICAL RULES ABOUT ASSET CLASS (each holding has a quote_type):\n"
    "- Holdings with quote_type ETF or INDEX (e.g. VOO, VTI, SPY) represent BROAD "
    "diversification across hundreds of underlying companies. Do NOT treat allocation "
    "to a single diversified ETF as concentration risk. Concentration risk applies to "
    "allocation in individual single-company stocks (quote_type EQUITY), not diversified "
    "funds. If the portfolio is mostly ETFs/indices, that is generally LOWER risk, not "
    "higher.\n"
    "- ETFs/indices do NOT have a meaningful intrinsic value or margin of safety by "
    "their nature (no single-company financials). Do NOT penalize the portfolio score "
    "for ETF/index holdings 'lacking intrinsic value data' — that is expected, not a flaw.\n"
    "Return ONLY valid JSON: {\"health_summary\": \"...\", \"concentration_risk\": "
    "\"...\", \"valuation_observations\": \"...\", \"portfolio_score\": <0-10 float>, "
    "\"score_justification\": \"...\"}"
)



# Well-known broad-market ETFs/index funds. Finnhub's free profile2 doesn't return
# an asset-class flag, so a fund added while FMP was capped can get mis-stored as
# EQUITY — which made the insights LLM wrongly flag a diversified index fund (VT) as
# single-name "concentration risk". We correct the type server-side before analysis.
_KNOWN_ETFS = {
    "VT", "VTI", "VOO", "VXUS", "SPY", "QQQ", "IVV", "VEA", "VWO", "BND", "AGG",
    "SCHB", "SCHD", "SCHX", "ITOT", "IEFA", "IEMG", "VGT", "VUG", "VTV", "VYM",
    "DIA", "IWM", "VB", "VO", "VEU", "GLD", "SLV", "ARKK", "XLK", "XLF", "XLE",
    "SOXX", "SMH", "JEPI", "JEPQ", "VIG", "VNQ", "TLT", "IJR", "IJH", "MGK",
}


@router.post("/portfolio-insights")
def portfolio_insights(body: PortfolioInsightsBody):
    if not body.holdings:
        raise HTTPException(status_code=400, detail="No holdings provided.")

    holdings = []
    for h in body.holdings:
        hd = h.model_dump()
        if str(hd.get("ticker", "")).upper() in _KNOWN_ETFS and hd.get("quote_type") in (None, "", "EQUITY"):
            hd["quote_type"] = "ETF"  # correct a mis-stored diversified fund
        holdings.append(hd)

    data = {
        "total_portfolio_value": body.total_value,
        "total_gain_loss_pct": body.total_gain_loss_pct,
        "holdings": holdings,
    }
    user_prompt = (
        "Analyze this portfolio using ONLY the data below. Be specific to these "
        "tickers and numbers.\n\n"
        f"DATA:\n{json_mod.dumps(data, indent=2, default=str)}"
    )
    parsed, source = _llm_call(PORTFOLIO_INSIGHTS_SYSTEM, user_prompt, max_tokens=1500)
    if not parsed:
        raise HTTPException(status_code=502, detail="Insights generation failed (all providers).")
    parsed["generated_source"] = source
    return parsed
