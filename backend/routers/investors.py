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

from config import INVESTORS
from utils import *
from services.llm_providers import *
from routers.analyze import gather_fundamentals, _resolve_market_data

router = APIRouter()

def _build_investor_prompt(facts_json):
    return (
        f"Evaluate this company using ONLY the raw fundamental data below. Reason "
        f"independently and specifically in your own documented style and known real "
        f"views — be concrete about THIS company, not generic.\n\nDATA:\n{facts_json}\n\n"
        f"Respond with ONLY valid JSON (no markdown, no preamble), exactly:\n"
        f'{{"score": <0-10 float>, "verdict": "Buy"|"Hold"|"Sell", '
        f'"bull_case": "<3-4 specific sentences in character>", '
        f'"bear_case": "<3-4 specific sentences in character>"}}'
    )


def _parse_investor(investor, content, source):
    parsed = json_mod.loads(content)
    return {
        "name": investor["name"],
        "slug": investor["slug"],
        "score": parsed.get("score"),
        "verdict": parsed.get("verdict"),
        "bull_case": parsed.get("bull_case"),
        "bear_case": parsed.get("bear_case"),
        "source": source,
    }


# Low temperature => stable verdict/score across repeated runs on identical data
# (wording still varies naturally). 0.3 keeps scores within ~±0.5 run-to-run.

def _build_consolidated_investor_prompt():
    """Build ONE system prompt covering all six investors' philosophies."""
    parts = [
        "You are simulating SIX legendary investors. Each evaluates the SAME company "
        "INDEPENDENTLY, in their own documented voice and philosophy. Do not let one "
        "investor's view influence another. The six investors and their philosophies:\n"
    ]
    for inv in INVESTORS:
        parts.append(f"\n### {inv['slug']} ({inv['name']})\n{inv['system']}\n")
    parts.append(
        "\nCRITICAL RULE FOR ALL SIX: Do NOT use generic, recyclable filler such as "
        "'[sector] is highly competitive and faces risks from new entrants' or 'the "
        "company could be disrupted by new technology.' EVERY bull and bear point must "
        "cite a SPECIFIC number or fact from the provided data (a margin, a growth "
        "rate, a ratio, a trend, an insider/holdings signal, the F-score, etc.). If you "
        "cannot ground a point in the data, omit it. Each investor's points should read "
        "differently for different companies because they reference that company's "
        "actual numbers.\n"
        "\nSCORING FOR ALL SIX: the score answers 'how attractive is this stock as an "
        "investment TODAY through THIS investor's documented framework?' Calibrate: "
        "8-10 = they would buy with conviction; 6-7.5 = a business they admire at an "
        "acceptable price (a quality-focused investor does NOT trash a great franchise "
        "over modest overvaluation — they hold such companies for decades); 4-6 = "
        "genuinely mixed; below 4 = fails that investor's CORE tests (not merely "
        "'expensive'). Use the full range, let the investors DISAGREE with each other, "
        "and stay true to how each actually behaved with real money — e.g. a deep-value "
        "contrarian scores a solvent franchise at a depressed multiple HIGH, not low, "
        "and a quality investor scores a fortress moat at a fair price HIGH even when "
        "it isn't statistically cheap.\n"
        "\nReturn ONLY valid JSON (no markdown, no preamble) with an evaluation for "
        "ALL SIX investors, exactly:\n"
        '{"investors": [{"slug": "<investor-slug>", "score": <0-10 float>, '
        '"verdict": "Buy"|"Hold"|"Sell", "bull_case": "<3-4 specific sentences in '
        'character>", "bear_case": "<3-4 specific sentences in character>"}, ... all '
        "six, using the exact slugs above]}"
    )
    return "".join(parts)


_CONSOLIDATED_INVESTOR_SYSTEM = None


@router.get("/investors/{ticker}")
def investors_endpoint(ticker: str, refresh: bool = False):
    global _CONSOLIDATED_INVESTOR_SYSTEM
    ticker = ticker.upper()

    def _generate():
        global _CONSOLIDATED_INVESTOR_SYSTEM
        # Use the same resolver as /analyze (yfinance -> FMP adapter) so investor
        # takes still work when yfinance is IP-blocked. If no full-data provider is
        # available, return no results (stale-serve covers a prior good response).
        stock, info, _src = _resolve_market_data(ticker)
        if stock is None:
            return {"ticker": ticker, "investors": []}, False

        facts = gather_fundamentals(ticker, stock, info)
        facts_json = json_mod.dumps(facts, indent=2, default=str)

        if _CONSOLIDATED_INVESTOR_SYSTEM is None:
            _CONSOLIDATED_INVESTOR_SYSTEM = _build_consolidated_investor_prompt()

        user_prompt = (
            "Evaluate the company below for ALL SIX investors using ONLY this data.\n\n"
            f"DATA:\n{facts_json}"
        )
        # ONE consolidated call (was 6). Larger budget for the combined response.
        parsed, source = _llm_call(
            _CONSOLIDATED_INVESTOR_SYSTEM, user_prompt, max_tokens=4000
        )
        print(f"[investors] {ticker}: 1 consolidated call -> source={source}", flush=True)

        by_slug = {}
        if parsed and isinstance(parsed.get("investors"), list):
            for item in parsed["investors"]:
                if isinstance(item, dict) and item.get("slug"):
                    by_slug[item["slug"]] = item

        results = []
        for inv in INVESTORS:
            item = by_slug.get(inv["slug"])
            if item:
                results.append({
                    "name": inv["name"],
                    "slug": inv["slug"],
                    "score": item.get("score"),
                    "verdict": item.get("verdict"),
                    "bull_case": item.get("bull_case"),
                    "bear_case": item.get("bear_case"),
                    "source": source,
                })

        payload = {"ticker": ticker, "investors": results}
        return payload, bool(results)  # only cache when we actually got results

    return _cached_or_generate(f"investors:{ticker}", AI_CACHE_TTL, refresh, _generate)


# ---------------------------------------------------------------------------
# Insider Trades — SEC EDGAR Form 4
# ---------------------------------------------------------------------------

# SEC requires a descriptive User-Agent on every request or it blocks/rate-limits.
