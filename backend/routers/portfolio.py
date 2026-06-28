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



@router.post("/portfolio-insights")
def portfolio_insights(body: PortfolioInsightsBody):
    if not body.holdings:
        raise HTTPException(status_code=400, detail="No holdings provided.")

    data = {
        "total_portfolio_value": body.total_value,
        "total_gain_loss_pct": body.total_gain_loss_pct,
        "holdings": [h.model_dump() for h in body.holdings],
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
