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


# ---------------------------------------------------------------------------
# AI valuation reviewer — a second opinion on the deterministic model.
#
# The quantitative engine (DCF + relative multiples) is the auditable anchor, but
# it can produce unreasonable numbers on edge cases (restructurings, negative or
# near-zero denominators, unusual capital structures). This reviewer judges the
# model's output against the fundamentals and, when it disagrees, supplies its own
# grounded intrinsic-value estimate and confidence. It NEVER pretends to do precise
# DCF math — it reasons from the provided numbers and is told to prefer an honest
# "low confidence / wide range" over false precision.
# ---------------------------------------------------------------------------

VALUATION_REVIEW_SYSTEM = (
    "You are a senior equity-valuation analyst giving a SECOND OPINION on an "
    "automated valuation model's output for a single company. You will receive the "
    "company's fundamentals, the current market price, and the model's intrinsic-"
    "value estimate (a blend of an internal DCF, an external DCF, and relative "
    "multiples), plus its scenarios and self-reported confidence.\n\n"
    "YOUR JOB:\n"
    "1. Judge whether the model's intrinsic value is REASONABLE given the "
    "fundamentals and the current price. Watch for red flags: a fair value that is "
    "wildly above or below the market price with no justification; growth or margin "
    "assumptions inconsistent with the actual history; multiples distorted by a "
    "near-zero or negative denominator (EPS/FCF); ignoring losses, heavy debt, "
    "cyclicality, dilution, or a recent restructuring/merger.\n"
    "2. If the model looks reasonable, endorse it and state your confidence.\n"
    "3. If the model looks WRONG, say so plainly and provide YOUR OWN intrinsic-"
    "value estimate: a single point value AND a low-high range, grounded in the "
    "provided numbers (e.g. a defensible P/E on normalized earnings, an EV/EBITDA "
    "or P/FCF on trend cash flow, or book value for asset-heavy/financials). Show "
    "the reasoning briefly.\n\n"
    "HARD RULES:\n"
    "- Reason ONLY from the numbers provided. Do NOT invent financial figures, "
    "analyst targets, or facts not in the data. If a needed number is missing, say "
    "so and widen your range.\n"
    "- Your estimates are informed JUDGEMENT, not precise calculations — never imply "
    "false precision. When the business is genuinely hard to value (no earnings, "
    "restructuring, extreme volatility), prefer LOW confidence and a WIDE range over "
    "a confident single number.\n"
    "- All monetary values are per-share, in the same currency as current_price.\n"
    "- Keep the rationale to 2-4 sentences, specific and quantitative.\n\n"
    "Return ONLY valid JSON with EXACTLY these fields:\n"
    "{\n"
    '  "assessment": "reasonable" | "too_high" | "too_low" | "unreliable",\n'
    '  "agrees_with_model": true | false,\n'
    '  "ai_fair_value": <number per share, or null if you cannot estimate>,\n'
    '  "ai_value_low": <number, or null>,\n'
    '  "ai_value_high": <number, or null>,\n'
    '  "confidence": "high" | "medium" | "low",\n'
    '  "rationale": "2-4 sentences explaining your judgement",\n'
    '  "key_factors": ["short phrase", "short phrase", "short phrase"]\n'
    "}"
)


@router.get("/valuation-review/{ticker}")
def valuation_review(ticker: str, refresh: bool = False):
    ticker = ticker.upper()
    return _cached_or_generate(
        f"valreview:{ticker}", THESIS_CACHE_TTL, refresh, lambda: _generate_valuation_review(ticker)
    )


def _generate_valuation_review(ticker: str):
    # Reuse the already-cached analyze + metrics results (no extra upstream cost).
    try:
        a = analyze(ticker)
    except Exception:
        a = {}
    # Only operating companies get a quantitative valuation to review.
    if not a.get("valuation_breakdown"):
        payload = {
            "ticker": ticker,
            "applicable": False,
            "note": "Valuation review applies to operating companies only.",
        }
        return payload, False  # don't cache a non-applicable stub

    try:
        m = metrics_endpoint(ticker)
    except Exception:
        m = {}
    iv = a.get("intrinsic_value") or {}
    vb = a.get("valuation_breakdown") or {}
    val = (m.get("valuation") or {})
    qual = (m.get("quality") or {})
    fh = (m.get("financial_health") or {})

    data = {
        "company_name": a.get("company_name"),
        "sector": (a.get("dcf_breakdown") or {}).get("sector"),
        "currency": a.get("currency"),
        "current_price": a.get("current_price"),
        "model_intrinsic_value": iv.get("consensus"),
        "model_scenarios": {
            "bear": (iv.get("bear") or {}).get("value"),
            "base": (iv.get("base") or {}).get("value"),
            "bull": (iv.get("bull") or {}).get("value"),
        },
        "model_confidence": a.get("confidence"),
        "model_margin_of_safety_pct": a.get("margin_of_safety_pct"),
        "model_adjustments": vb.get("adjustments_applied"),
        "valuation_breakdown": {
            "internal_dcf": vb.get("internal_dcf"),
            "external_dcf": vb.get("external_dcf"),
            "relative_value": vb.get("relative_value"),
            "weights": vb.get("blend_weights"),
        },
        "piotroski_f_score_0_to_9": a.get("f_score"),
        "revenue_last_5y": a.get("revenue_5yr"),
        "free_cash_flow_last_5y": a.get("fcf_5yr"),
        "ratios": {
            "pe": val.get("pe_ratio"),
            "forward_pe": val.get("forward_pe"),
            "price_to_book": val.get("pb_ratio"),
            "ev_ebitda": val.get("ev_ebitda"),
            "p_fcf": val.get("p_fcf"),
            "peg": val.get("peg_ratio"),
            "profit_margin_pct": qual.get("profit_margin"),
            "roa_pct": qual.get("roic"),
            "current_ratio": qual.get("current_ratio"),
            "eps_ttm": fh.get("eps_ttm"),
            "debt_to_equity": fh.get("debt_equity"),
            "market_cap": fh.get("market_cap"),
        },
    }

    user_prompt = (
        "Review this valuation. Use ONLY the data below; per-share values are in "
        f"{data.get('currency') or 'USD'}.\n\n"
        f"DATA:\n{json_mod.dumps(data, indent=2, default=str)}"
    )
    parsed, source = _llm_call(VALUATION_REVIEW_SYSTEM, user_prompt, max_tokens=1200)
    if not parsed:
        return {"ticker": ticker, "applicable": True}, False  # allow stale-serve

    parsed["ticker"] = ticker
    parsed["applicable"] = True
    parsed["model_intrinsic_value"] = iv.get("consensus")
    parsed["current_price"] = a.get("current_price")
    parsed["generated_source"] = source
    return parsed, True


def _generate_thesis(ticker: str):
    # Use the /analyze resolver (yfinance -> FMP adapter) so the thesis still works
    # when yfinance is IP-blocked. No provider -> no thesis (stale-serve covers it).
    from routers.analyze import _resolve_market_data
    stock, info, _src = _resolve_market_data(ticker)
    if stock is None:
        return {"ticker": ticker}, False

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

    # Cerebras first — it's the reliably-available provider on the free tiers
    # (Groq/Gemini frequently quota-exhausted); Gemini/Groq remain backups for
    # long-form quality when their quotas reset.
    parsed, source = _llm_call(
        DEEP_RESEARCH_SYSTEM, user_prompt, max_tokens=8000,
        order=("cerebras", "gemini", "groq"),
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

