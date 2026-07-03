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

from config import SEC_HEADERS, TX_CODE_LABELS, LEGENDARY_FUNDS
from utils import *

router = APIRouter()

# Module-level caches for SEC data (mutable; reassigned via `global` below).
_CIK_MAP: dict[str, str] = {}  # ticker -> 10-digit zero-padded CIK
_F13_CACHE: dict[str, tuple] = {}
# 13F filings only update quarterly, so weekly is plenty — and a long TTL means a
# blocked/slow EDGAR (cloud IPs get throttled) rarely triggers a refetch at all.
_F13_TTL = 7 * 24 * 3600  # 7 days
# Per-ticker cache of the combined institutional-holdings result.
_INST_CACHE: dict[str, tuple] = {}
_INST_TTL = 7 * 24 * 3600  # 7 days
# Per-ticker cache of insider (Form 4) trades.
_INSIDER_CACHE: dict[str, tuple] = {}
_INSIDER_TTL = 6 * 3600  # 6 hours


def _load_cik_map():
    """Fetch and cache the SEC ticker->CIK mapping (once). Returns {} without
    raising if EDGAR is unreachable, so callers degrade instead of erroring; the
    empty map is not cached, so it retries on the next request."""
    global _CIK_MAP
    if _CIK_MAP:
        return _CIK_MAP
    raw = _sec_get("https://www.sec.gov/files/company_tickers.json")
    if raw is None:
        return {}
    try:
        data = json_mod.loads(raw)
    except Exception:
        return {}
    mapping = {}
    for row in data.values():
        ticker = str(row.get("ticker", "")).upper()
        cik = str(row.get("cik_str", "")).zfill(10)
        if ticker:
            mapping[ticker] = cik
    _CIK_MAP = mapping
    return _CIK_MAP



def _sec_get(url):
    """Fetch from SEC EDGAR. Returns bytes, or None if EDGAR is unreachable
    (blocked cloud IP, timeout, error) — callers serve cached data instead of
    failing. 10s timeout keeps a cold-start request from hanging."""
    try:
        req = urllib.request.Request(url, headers=SEC_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except Exception:
        return None



def _parse_form4(xml_bytes, cik_int, accession_nodash):
    """Parse one Form 4 ownership XML into a list of transaction dicts."""
    import xml.etree.ElementTree as ET
    trades = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return trades

    # Reporting owner name + relationship
    owner = root.find(".//reportingOwner")
    name = _text(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    titles = []
    if rel is not None:
        if _text(rel, "isDirector") in ("1", "true"):
            titles.append("Director")
        if _text(rel, "isOfficer") in ("1", "true"):
            titles.append(_text(rel, "officerTitle") or "Officer")
        if _text(rel, "isTenPercentOwner") in ("1", "true"):
            titles.append("10% Owner")
        if _text(rel, "isOther") in ("1", "true"):
            titles.append(_text(rel, "otherText") or "Other")
    title = ", ".join(titles) if titles else "Insider"

    for tx in root.findall(".//nonDerivativeTransaction"):
        try:
            date = _text(tx, "transactionDate/value")
            code = _text(tx, "transactionCoding/transactionCode") or ""
            shares = _text(tx, "transactionAmounts/transactionShares/value")
            price = _text(tx, "transactionAmounts/transactionPricePerShare/value")
            after = _text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
            shares_n = float(shares) if shares else 0.0
            price_n = float(price) if price else 0.0
            trades.append({
                "insider_name": name or "Unknown",
                "title": title,
                "date": date,
                "transaction_code": code,
                "transaction_type": TX_CODE_LABELS.get(code, code or "Other"),
                "shares": round(shares_n),
                "price": round(price_n, 2),
                "value": round(shares_n * price_n),
                "shares_owned_after": round(float(after)) if after else None,
            })
        except Exception:
            continue
    return trades



@router.get("/insider-trades/{ticker}")
def insider_trades(ticker: str):
    ticker = ticker.upper()
    import time
    now = time.time()
    cached = _INSIDER_CACHE.get(ticker)
    if cached and now - cached[0] < _INSIDER_TTL:
        return cached[1]

    def _degrade():
        # EDGAR unreachable: serve stale cache if present, else empty (never 502).
        return cached[1] if cached else {"ticker": ticker, "trades": []}

    cik_map = _load_cik_map()  # {} if EDGAR unreachable (no raise)
    if not cik_map:
        return _degrade()

    cik = cik_map.get(ticker)
    if not cik:
        payload = {"ticker": ticker, "trades": []}
        _INSIDER_CACHE[ticker] = (now, payload)
        return payload

    cik_int = int(cik)
    sub_raw = _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if sub_raw is None:
        return _degrade()
    try:
        sub = json_mod.loads(sub_raw)
    except Exception:
        return _degrade()

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    # Collect the most recent Form 4 filings. Each is a separate SEC request, so
    # 10 (down from 20) roughly halves the cold-fetch time while still surfacing
    # plenty of recent insider activity; result is cached 6h afterward.
    form4 = []
    for i, f in enumerate(forms):
        if f == "4":
            form4.append((accessions[i], primary_docs[i] if i < len(primary_docs) else ""))
        if len(form4) >= 10:
            break

    from concurrent.futures import ThreadPoolExecutor

    def fetch_and_parse(item):
        accession, primary = item
        nodash = accession.replace("-", "")
        raw_doc = primary.split("/")[-1] if primary else ""
        if not raw_doc:
            return []
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{nodash}/{raw_doc}"
        try:
            return _parse_form4(_sec_get(url), cik_int, nodash)
        except Exception:
            return []

    all_trades = []
    # SEC allows ~10 req/sec; 4 workers stays safely under that.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for trades in pool.map(fetch_and_parse, form4):
            all_trades.extend(trades)

    # Sort newest first, cap to a sensible number
    all_trades.sort(key=lambda t: t.get("date") or "", reverse=True)
    payload = {"ticker": ticker, "trades": all_trades[:25]}
    _INSIDER_CACHE[ticker] = (now, payload)
    return payload


# ---------------------------------------------------------------------------
# 13F — Legendary investor fund holdings (SEC EDGAR)
# ---------------------------------------------------------------------------


def _fund_holdings(cik):
    """Fetch + parse a fund's most recent 13F-HR info table. Cached per fund for
    7 days. If EDGAR is unreachable, serve the cached copy even if expired (13F
    data is quarterly, so stale is fine) and never overwrite good data with empty.
    Result carries `ok`: True if freshly fetched/valid, False if EDGAR was down."""
    import time
    now = time.time()
    cached = _F13_CACHE.get(cik)
    if cached and now - cached[0] < _F13_TTL:
        return cached[1]

    def _stale_or_empty():
        # EDGAR unreachable: prefer stale cache, else an empty "not ok" result.
        if cached:
            return cached[1]
        return {"holdings": {}, "period": None, "ok": False}

    import xml.etree.ElementTree as ET
    result = {"holdings": {}, "period": None, "ok": True}
    try:
        raw = _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if raw is None:
            return _stale_or_empty()  # EDGAR down — keep any prior good data
        sub = json_mod.loads(raw)
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("reportDate", [])
        idx = next((i for i, f in enumerate(forms) if f == "13F-HR"), None)
        if idx is None:
            _F13_CACHE[cik] = (now, result)
            return result
        accession = accessions[idx]
        result["period"] = dates[idx] if idx < len(dates) else None
        nodash = accession.replace("-", "")
        cik_int = int(cik)
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{nodash}"

        # Find the information-table XML via the filing's directory index
        idx_raw = _sec_get(f"{base}/index.json")
        if idx_raw is None:
            return _stale_or_empty()
        index = json_mod.loads(idx_raw)
        names = [it.get("name", "") for it in index.get("directory", {}).get("item", [])]
        xmls = [n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()]
        info_name = next(
            (n for n in xmls if any(k in n.lower() for k in ("infotable", "form13f", "table"))),
            xmls[0] if xmls else None,
        )
        if not info_name:
            _F13_CACHE[cik] = (now, result)
            return result

        xml_raw = _sec_get(f"{base}/{info_name}")
        if xml_raw is None:
            return _stale_or_empty()
        root = ET.fromstring(xml_raw)
        for it in root.iter():
            if _local(it.tag) != "infoTable":
                continue
            issuer = shares = value = None
            for child in it.iter():
                lt = _local(child.tag)
                if lt == "nameOfIssuer" and child.text:
                    issuer = child.text.strip()
                elif lt == "value" and child.text:
                    value = child.text.strip()
                elif lt == "sshPrnamt" and child.text:
                    shares = child.text.strip()
            if issuer:
                norm = _norm_company(issuer)
                sh = int(float(shares)) if shares else 0
                val = int(float(value)) if value else 0
                # Filers split a position across many rows (by manager/lot) —
                # aggregate all rows for the same issuer rather than overwrite.
                existing = result["holdings"].get(norm)
                if existing:
                    existing["shares"] += sh
                    existing["value"] += val
                else:
                    result["holdings"][norm] = {
                        "issuer": issuer,
                        "shares": sh,  # 13F value is whole dollars (post-2023)
                        "value": val,
                    }
    except Exception:
        pass

    _F13_CACHE[cik] = (now, result)
    return result


def _company_name_for(ticker):
    # yfinance first (fast when the host IP isn't blocked)...
    try:
        info = yf.Ticker(ticker).info
        name = safe_get(info, "longName") or safe_get(info, "shortName")
        if name:
            return name
    except Exception:
        pass
    # ...but on cloud hosts yfinance is blocked, so fall back to the shared resolver
    # (EDGAR/Finnhub give the real company name). Without this the name defaulted to
    # the ticker, so the 13F name-match failed and EVERY fund showed holds=False.
    try:
        from routers.analyze import _resolve_market_data
        _stock, info, _src = _resolve_market_data(ticker)
        if info:
            name = safe_get(info, "longName") or safe_get(info, "shortName")
            if name:
                return name
    except Exception:
        pass
    return ticker



@router.get("/institutional-holdings/{ticker}")
def institutional_holdings(ticker: str):
    ticker = ticker.upper()
    import time
    now = time.time()
    cached = _INST_CACHE.get(ticker)
    if cached and now - cached[0] < _INST_TTL:
        return cached[1]

    company = _company_name_for(ticker)
    comp_norm = _norm_company(company)
    edgar = {"ok": False}  # did EDGAR serve any usable data this call?

    from concurrent.futures import ThreadPoolExecutor

    def check(fund):
        data = _fund_holdings(fund["cik"])
        if data.get("ok", True):  # fresh/valid (or served from a prior good cache)
            edgar["ok"] = True
        holdings = data["holdings"]
        match = None
        # 1) Exact normalized match wins (avoids e.g. "APPLE HOSPITALITY REIT"
        #    matching "Apple Inc.").
        if comp_norm and comp_norm in holdings:
            match = holdings[comp_norm]
        else:
            # 2) Fall back to a conservative substring match.
            for norm, h in holdings.items():
                if norm and comp_norm and (comp_norm in norm or norm in comp_norm):
                    match = h
                    break
        return {
            "fund": fund["name"],
            "manager": fund["manager"],
            "holds": match is not None,
            "shares": match["shares"] if match else None,
            "value": match["value"] if match else None,
            "period": data.get("period"),
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(check, LEGENDARY_FUNDS))

    if edgar["ok"]:
        payload = {"ticker": ticker, "company_name": company, "funds": results}
        _INST_CACHE[ticker] = (now, payload)
        return payload

    # EDGAR unreachable for every fund. Prefer stale cache (13F is quarterly, so a
    # week-old copy is valid); otherwise be honest rather than showing "—" for all.
    if cached:
        return cached[1]
    return {
        "ticker": ticker,
        "company_name": company,
        "funds": [],
        "status": "temporarily_unavailable",
    }


# ---------------------------------------------------------------------------
# Email report (Resend)
# ---------------------------------------------------------------------------

