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

from config import THESIS_CACHE_TTL
from utils import *
from services.llm_providers import *
from routers.analyze import analyze, metrics_endpoint, financials_endpoint
from routers.ownership import insider_trades, institutional_holdings

router = APIRouter()

THESIS_SYSTEM = (
    "You are a neutral sell-side equity research analyst writing for institutional "
    "clients. Write a polished, professional investment thesis. Cover exactly: "
    "(1) Business Overview - what the company does and how it makes money, 2-3 "
    "sentences. (2) Investment Thesis - 3-4 sentences on the core bull/bear tension "
    "and why this stock matters right now. (3) Key Risks - 2-3 sentences. Tone: "
    "confident, analytical, Wall Street research-note style - NOT a generic AI "
    "summary.\n"
    "When discussing valuation, USE THE 'precomputed_valuation_sentence' field "
    "VERBATIM — do NOT recalculate or rephrase any percentages yourself. When "
    "referring to financial health, use the provided 'f_score_band' label exactly "
    "(never call a 4-6 score 'high' or 'strong'). Return ONLY valid JSON: "
    '{"business_overview": "...", "investment_thesis": "...", "key_risks": "..."}'
)


@router.get("/thesis/{ticker}")
def thesis_endpoint(ticker: str, refresh: bool = False):
    ticker = ticker.upper()
    return _cached_or_generate(
        f"thesis:{ticker}", THESIS_CACHE_TTL, refresh, lambda: _generate_thesis(ticker)
    )


def _generate_thesis(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Could not fetch data for {ticker}: {e}")
    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
        raise HTTPException(status_code=404, detail=f"No data found for ticker {ticker}")

    business_summary = safe_get(info, "longBusinessSummary", "") or ""
    sector = safe_get(info, "sector")
    industry = safe_get(info, "industry")
    employees = safe_get(info, "fullTimeEmployees")
    website = safe_get(info, "website")

    # Pull computed valuation context (reuses the analyze pipeline)
    try:
        av = analyze(ticker)
    except Exception:
        av = {}
    iv = (av.get("intrinsic_value") or {}).get("consensus")
    f_score = av.get("f_score")
    facts = {
        "company_name": safe_get(info, "longName") or safe_get(info, "shortName", ticker),
        "sector": sector,
        "industry": industry,
        "employees": employees,
        "current_price": av.get("current_price"),
        "intrinsic_value": iv,
        "precomputed_valuation_sentence": _valuation_sentence(av.get("current_price"), iv),
        "f_score": f_score,
        "f_score_band": f"{f_score}/9 ({_fscore_band(f_score)})" if f_score is not None else "unknown",
        "pe_ratio": safe_get(info, "trailingPE"),
        "profit_margin_pct": round(safe_get(info, "profitMargins") * 100, 1) if safe_get(info, "profitMargins") is not None else None,
        "revenue_growth": safe_get(info, "revenueGrowth"),
        "business_summary": business_summary[:1200],
    }
    user_prompt = (
        "Write the investment thesis using this data. Be specific to THIS company.\n\n"
        f"DATA:\n{json_mod.dumps(facts, indent=2, default=str)}"
    )
    parsed = _llm_json(THESIS_SYSTEM, user_prompt) or {}

    payload = {
        "ticker": ticker,
        "business_summary": business_summary,
        "sector": sector,
        "industry": industry,
        "employees": employees,
        "website": website,
        "business_overview": parsed.get("business_overview"),
        "investment_thesis": parsed.get("investment_thesis"),
        "key_risks": parsed.get("key_risks"),
    }
    return payload, bool(parsed.get("investment_thesis"))  # cache only on success



DEEP_RESEARCH_SYSTEM = (
    "You are a senior hedge-fund investment analyst conducting full diligence as if "
    "ACQUIRING THE ENTIRE COMPANY, not just buying a share. Write in English. Be "
    "SKEPTICAL, not promotional — actively hunt for reasons the investment could fail. "
    "Clearly separate facts from estimates from opinions. Use the REAL numbers provided "
    "in the data wherever relevant; do NOT fabricate precise figures (e.g. exact "
    "CAC/LTV, market-size dollars) that are not in the provided data — discuss those "
    "qualitatively and explicitly note when precise figures aren't publicly available. "
    "For the Scenarios section, you MUST use the exact Bear/Base/Bull intrinsic-value "
    "numbers provided; discuss probability/risk context around them — do not invent new "
    "scenario price numbers.\n\n"
    "Return ONLY valid JSON with EXACTLY these fields (strings unless noted):\n"
    "{\n"
    '  "executive_summary": "one-line description; revenue sources; key advantages; '
    'biggest risks; strongest bull reason; strongest bear reason",\n'
    '  "business_model": "how money is actually made, revenue sources ranked, cost '
    'structure, what drives profit vs what burns cash",\n'
    '  "products_services": "breakdown, revenue/growth relevance, substitution risk",\n'
    '  "competitive_moat": {"network_effects": <1-10>, "brand": <1-10>, '
    '"switching_costs": <1-10>, "data_advantage": <1-10>, "technology_advantage": '
    '<1-10>, "regulatory_advantage": <1-10>, "overall": <1-10>, "summary": "..."},\n'
    '  "industry_analysis": "market trends, growth trajectory, AI/tech disruption '
    'threats, barriers to entry — framed as analytical judgment, not hard data",\n'
    '  "competitors": "name 2-4 REAL competitors, compare qualitatively, who likely '
    'wins long-term and why",\n'
    '  "management": "capital-allocation history, insider-ownership signals from the '
    'insider data provided, incentive alignment",\n'
    '  "financial_history": "discuss revenue, operating income, net income, FCF, '
    'margins, ROE/ROIC, debt over the ACTUAL years provided; STATE how many years this '
    'covers",\n'
    '  "unit_economics": "qualitative only; explicitly note when precise CAC/LTV are '
    'not publicly available rather than fabricating",\n'
    '  "risks": "material risks (regulatory, competitive, technology, management, '
    'geopolitical) — what could cause an 80% decline",\n'
    '  "growth_drivers": "what could double revenue/profit; unpriced opportunities",\n'
    '  "scenarios": {"bear": "risk/probability context around the provided bear value", '
    '"base": "context around the provided base value", "bull": "context around the '
    'provided bull value"},\n'
    '  "red_flags": "accounting concerns, concentration risk, concerning trends",\n'
    '  "open_questions": "what is genuinely unknown / to research further",\n'
    '  "investment_summary": {"reasons_to_buy": [10 strings], "reasons_not_to_buy": '
    '[10 strings], "thesis_works_if": "...", "thesis_breaks_if": "...", "scores": '
    '{"business_quality": <1-10>, "management_quality": <1-10>, "competitive_advantage"'
    ': <1-10>, "growth_potential": <1-10>, "risk_level": <1-10>, '
    '"overall_attractiveness": <1-10>}}\n'
    "}"
)


@router.get("/deep-research/{ticker}")
def deep_research(ticker: str, refresh: bool = False):
    ticker = ticker.upper()
    return _cached_or_generate(
        f"deep:{ticker}", DEEP_CACHE_TTL, refresh, lambda: _generate_deep_research(ticker)
    )


def _generate_deep_research(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Could not fetch data for {ticker}: {e}")
    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
        raise HTTPException(status_code=404, detail=f"No data found for ticker {ticker}")

    # Gather everything (best-effort; never crash the report on a sub-fetch)
    def safe(fn):
        try:
            return fn()
        except Exception:
            return {}

    av = safe(lambda: analyze(ticker))
    met = safe(lambda: metrics_endpoint(ticker))
    fin = safe(lambda: financials_endpoint(ticker))
    insiders = safe(lambda: insider_trades(ticker))
    inst = safe(lambda: institutional_holdings(ticker))

    scenarios = (av.get("intrinsic_value") or {})
    bear = (scenarios.get("bear") or {}).get("value")
    base = (scenarios.get("base") or {}).get("value")
    bull = (scenarios.get("bull") or {}).get("value")
    consensus = scenarios.get("consensus")

    rev = fin.get("revenue") or []
    years_covered = len(rev)
    year_range = f"{rev[0]['year']}–{rev[-1]['year']}" if rev else "n/a"

    # Insider summary (counts, not the full list)
    trades = insiders.get("trades") or []
    buys = sum(1 for t in trades if t.get("transaction_type") == "Purchase")
    sells = sum(1 for t in trades if t.get("transaction_type") == "Sale")
    holders = [f["fund"] for f in (inst.get("funds") or []) if f.get("holds")]

    f_score = av.get("f_score")

    # Fix 4: pre-compute internal vs external DCF divergence (a fact for the LLM).
    vb = av.get("valuation_breakdown") or {}
    internal_dcf = vb.get("internal_dcf")
    external_dcf = vb.get("external_dcf")
    dcf_divergence_note = None
    if internal_dcf and external_dcf and internal_dcf > 0 and external_dcf > 0:
        ratio = max(internal_dcf, external_dcf) / min(internal_dcf, external_dcf)
        if ratio >= 2.0:
            dcf_divergence_note = (
                f"Our internal DCF (${internal_dcf:.2f}) and an independent external DCF "
                f"(${external_dcf:.2f}) disagree by {ratio:.1f}x — this divergence suggests "
                f"one model's growth or discount-rate assumptions may be too extreme; treat "
                f"the blended estimate with added caution. Discuss WHY this gap likely "
                f"exists given the data (e.g. an aggressive vs conservative growth assumption)."
            )

    data = {
        "company_name": safe_get(info, "longName") or safe_get(info, "shortName", ticker),
        "ticker": ticker,
        "sector": safe_get(info, "sector"),
        "industry": safe_get(info, "industry"),
        "employees": safe_get(info, "fullTimeEmployees"),
        "business_summary": (safe_get(info, "longBusinessSummary", "") or "")[:2000],
        "current_price": av.get("current_price"),
        "intrinsic_value_consensus": consensus,
        "precomputed_valuation_sentence": _valuation_sentence(av.get("current_price"), consensus),
        "dcf_scenarios_REAL_VALUES": {"bear": bear, "base": base, "bull": bull},
        "internal_dcf": internal_dcf,
        "external_dcf_fmp": external_dcf,
        "dcf_divergence_redflag": dcf_divergence_note,
        "margin_of_safety_pct": av.get("margin_of_safety_pct"),
        "f_score": f_score,
        "f_score_band": f"{f_score}/9 ({_fscore_band(f_score)})" if f_score is not None else "unknown",
        "financial_history": {
            "years_available": years_covered,
            "fiscal_range": year_range,
            "revenue": rev,
            "net_income": fin.get("net_income"),
            "operating_income": fin.get("operating_income"),
            "free_cash_flow": fin.get("fcf"),
            "eps": fin.get("eps"),
        },
        "key_metrics": {
            "valuation": met.get("valuation"),
            "quality": met.get("quality"),
            "financial_health": met.get("financial_health"),
        },
        "analyst_ratings": met.get("analyst_ratings"),
        "insider_activity": {"recent_purchases": buys, "recent_sales": sells, "sample": trades[:6]},
        "institutional_holders_tracked": holders,
    }

    divergence_instruction = (
        " The data includes a 'dcf_divergence_redflag' — you MUST surface it in the "
        "Red Flags section using its framing and discuss why the gap likely exists."
        if dcf_divergence_note else ""
    )
    user_prompt = (
        "Conduct full diligence on the company below using ONLY this data. For the "
        "Scenarios section use these EXACT intrinsic-value numbers and only add "
        f"qualitative context: bear={bear}, base={base}, bull={bull}. The financial "
        f"history covers {years_covered} year(s) ({year_range}) — state this honestly. "
        "When stating valuation upside/discount, use 'precomputed_valuation_sentence' "
        "verbatim — do NOT recompute percentages. Use the 'f_score_band' label exactly "
        "(never call a 4-6 score 'high' or 'strong')." + divergence_instruction + "\n\n"
        f"DATA:\n{json_mod.dumps(data, indent=2, default=str)}"
    )

    # Gemini PRIMARY for long-form quality, then Groq, then Cerebras.
    parsed, source = _llm_call(
        DEEP_RESEARCH_SYSTEM, user_prompt, max_tokens=8000,
        order=("gemini", "groq", "cerebras"),
    )
    if not parsed:
        raise HTTPException(status_code=502, detail="Deep research generation failed (all providers).")

    # Guarantee the Scenarios values are OUR real numbers, never invented.
    sc = parsed.get("scenarios")
    if isinstance(sc, dict):
        parsed["scenarios"] = {
            "bear": {"value": bear, "commentary": sc.get("bear") if isinstance(sc.get("bear"), str) else (sc.get("bear") or {}).get("commentary")},
            "base": {"value": base, "commentary": sc.get("base") if isinstance(sc.get("base"), str) else (sc.get("base") or {}).get("commentary")},
            "bull": {"value": bull, "commentary": sc.get("bull") if isinstance(sc.get("bull"), str) else (sc.get("bull") or {}).get("commentary")},
        }

    payload = {
        "ticker": ticker,
        "company_name": data["company_name"],
        "generated_source": source,
        "years_covered": years_covered,
        "fiscal_range": year_range,
        "report": parsed,
    }
    print(f"[deep-research] {ticker}: generated via {source}, {years_covered}y history", flush=True)
    return payload, True


# ---------------------------------------------------------------------------
# Stock Screener (reads the cache produced by run_screener.py)
# ---------------------------------------------------------------------------

