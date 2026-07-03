"""SEC EDGAR as a free, uncapped fundamentals source for the valuation engine.

Unlike yfinance (blocked on cloud IPs) and FMP (250/day per key), EDGAR's XBRL
`companyfacts` API is official, unlimited, and never IP-blocked. It returns a
company's entire financial history in ONE call.

The catch it solves here: `companyfacts` is raw XBRL — the same line item is filed
under different tags by different companies/eras (e.g. revenue as
`RevenueFromContractWithCustomerExcludingAssessedTax` vs `Revenues` vs
`SalesRevenueNet`), with quarterly/annual/restated values mixed together. This
module maps the ~15 tags the DCF/F-Score/relative-value engine needs, filters to
ANNUAL (10-K) values, de-duplicates restatements (keep latest-filed per period),
and exposes the result through the SAME yfinance-shaped adapter the FMP path uses,
so the entire engine runs unchanged.

EDGAR provides statements only (US operating companies, lagged to filings). Market
data is layered on: the current price + sector/name/beta come from Finnhub (free,
uncapped — so an EDGAR valuation still works when FMP is at its daily cap, which is
the whole point); 5y historical prices for the relative-value multiples come from
FMP. FMP usage drops from ~8 calls per analyze to ~1 (history only), so the daily
budget stretches much further, and Finnhub is used ONLY for market data (never
statements — those are always EDGAR).
"""
from __future__ import annotations

import time
import json as json_mod
import urllib.request
from datetime import date

import pandas as pd

from config import SEC_HEADERS
from services.fmp_fallback import _FmpStock, _num, _fmp_get

EDGAR_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# companyfacts blobs are large; cache the parsed statements per ticker.
_EDGAR_CACHE: dict[str, tuple] = {}
_EDGAR_TTL = 24 * 3600

# metric label (yfinance-shaped) -> (candidate XBRL tags in priority order, kind)
# kind: "dur" = duration/flow (income, cash flow), "inst" = instant (balance sheet)
_INCOME = {
    "Net Income": (["NetIncomeLoss"], "dur"),
    "Total Revenue": ([
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues", "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ], "dur"),
    "Operating Income": (["OperatingIncomeLoss"], "dur"),
    "Gross Profit": (["GrossProfit"], "dur"),
    "Cost Of Revenue": (["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"], "dur"),
}
_CASHFLOW = {
    "Operating Cash Flow": ([
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ], "dur"),
    # Capex tag varies by company: most use PP&E, but e.g. Visa files under
    # PaymentsToAcquireProductiveAssets. Merged across candidates by _pick so the
    # FCF (and thus the DCF) computes for the widest set of filers.
    "Capital Expenditure": ([
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
        "PaymentsForCapitalImprovements",
    ], "dur"),
}
_BALANCE = {
    "Total Assets": (["Assets"], "inst"),
    "Current Assets": (["AssetsCurrent"], "inst"),
    "Current Liabilities": (["LiabilitiesCurrent"], "inst"),
    "Stockholders Equity": ([
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], "inst"),
    "Long Term Debt": (["LongTermDebtNoncurrent", "LongTermDebt"], "inst"),
}
_SHARES_TAGS = ["WeightedAverageNumberOfDilutedSharesOutstanding",
                "WeightedAverageNumberOfSharesOutstandingBasic"]
_DA_TAGS = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization"]
_CASH_TAGS = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]


def _d(s):
    return date.fromisoformat(s)


def _cik_for(ticker: str):
    """Reuse the ownership module's SEC ticker->CIK map (10-digit, zero-padded)."""
    try:
        from routers.ownership import _load_cik_map
        return _load_cik_map().get(ticker.upper())
    except Exception:
        return None


def _annual_series(node, kind):
    """From an XBRL fact node, return {period_end_date: value} for annual (10-K)
    values only, keeping the latest-FILED value per period end (handles
    restatements). `kind` = 'dur' (flow, ~365-day period) or 'inst' (point-in-time)."""
    if not node:
        return {}
    units = node.get("units", {})
    entries = units.get("USD") or units.get("shares") or (next(iter(units.values()), []))
    by_end = {}  # end_date -> (filed_date, value)
    for e in entries:
        end = e.get("end")
        val = e.get("val")
        form = (e.get("form") or "")
        filed = (e.get("filed") or "")
        # 10-K = US annual report; 20-F = foreign private issuer annual report.
        if end is None or val is None or not (form.startswith("10-K") or form.startswith("20-F")):
            continue
        if kind == "dur":
            start = e.get("start")
            if not start:
                continue
            try:
                days = (_d(end) - _d(start)).days
            except Exception:
                continue
            if days < 330 or days > 400:  # must be a full year, not a quarter
                continue
        if end not in by_end or filed >= by_end[end][0]:
            by_end[end] = (filed, float(val))
    return {end: v for end, (f, v) in by_end.items()}


def _pick(gaap, dei, tags, kind):
    """Merge the candidate tags into one {end_date: value} series.

    A single company often switches the XBRL tag it uses between years (e.g. GOOGL
    files revenue under `RevenueFromContractWithCustomerExcludingAssessedTax` through
    2024 but `Revenues` for 2025). Picking one tag wholesale would drop the years the
    others cover and leave holes (a missing latest year silently corrupts the growth
    calc). So we overlay all candidates, letting HIGHER-priority tags win a period
    where several report it, while LOWER-priority tags fill the periods the top tag
    is missing. Result: the most complete series across every year."""
    merged = {}
    for t in reversed(tags):  # apply lowest priority first so higher ones overwrite
        node = gaap.get(t) or dei.get(t)
        s = _annual_series(node, kind)
        if s:
            merged.update(s)
    return merged


def _get_facts(ticker: str):
    cik = _cik_for(ticker)
    if not cik:
        return None
    url = EDGAR_FACTS.format(cik=str(cik).zfill(10))
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json_mod.loads(resp.read())
    except Exception:
        return None


def build_edgar_bundle(ticker: str):
    """Return (stock_like, info) backed by EDGAR statements + market data, or None
    if EDGAR can't supply the essentials. Same shape as the FMP adapter, so the
    valuation engine runs unchanged."""
    ticker = ticker.upper()
    cached = _EDGAR_CACHE.get(ticker)
    now = time.time()
    facts_bundle = None
    if cached and now - cached[0] < _EDGAR_TTL:
        facts_bundle = cached[1]
    else:
        raw = _get_facts(ticker)
        if not raw:
            return None
        gaap = raw.get("facts", {}).get("us-gaap", {})
        dei = raw.get("facts", {}).get("dei", {})
        if not gaap:
            return None
        # Extract every metric as {end_date: value}
        series = {}
        for label, (tags, kind) in {**_INCOME, **_CASHFLOW, **_BALANCE}.items():
            series[label] = _pick(gaap, dei, tags, kind)
        series["_shares"] = _pick(gaap, dei, _SHARES_TAGS, "dur")
        series["_da"] = _pick(gaap, dei, _DA_TAGS, "dur")
        series["_cash"] = _pick(gaap, dei, _CASH_TAGS, "inst")
        # EDGAR reports capex as a POSITIVE outflow magnitude; yfinance/FMP (and
        # thus the engine, e.g. _fcf_list which does OCF + capex) expect it NEGATIVE.
        series["Capital Expenditure"] = {k: -v for k, v in series.get("Capital Expenditure", {}).items()}
        facts_bundle = {"name": raw.get("entityName"), "series": series}
        _EDGAR_CACHE[ticker] = (now, facts_bundle)

    series = facts_bundle["series"]
    # Fiscal years = Net Income period-ends (most reliably present), newest first.
    year_ends = sorted(series.get("Net Income", {}).keys(), reverse=True)[:5]
    if len(year_ends) < 2:
        return None  # not enough history for the engine

    cols = [pd.Timestamp(e) for e in year_ends]

    def _df(labels):
        data = {}
        for lbl in labels:
            s = series.get(lbl, {})
            data[lbl] = [_num(s.get(e)) for e in year_ends]
        return pd.DataFrame(data, index=cols).T  # rows=labels, cols=year-ends

    financials = _df(list(_INCOME.keys()))
    cashflow = _df(list(_CASHFLOW.keys()))
    balance_sheet = _df(list(_BALANCE.keys()))
    # Share count row goes on the balance sheet under the label the engine expects.
    balance_sheet.loc["Share Issued"] = [_num(series["_shares"].get(e)) for e in year_ends]

    # --- Market data (EDGAR has none): current price + sector/name + history ---
    price, sector, name, beta, mkt_shares = _market_context(ticker)
    if price is None:
        return None  # can't value without a current price
    name = facts_bundle.get("name") or name or ticker

    latest = year_ends[0]
    # Prefer the XBRL weighted-average diluted count; fall back to the market
    # provider's share count for filers whose share tag we can't resolve (e.g. Visa's
    # multi-class structure), so per-share values (DCF, EPS) don't collapse to zero.
    shares = series["_shares"].get(latest) or mkt_shares or None
    if not series["_shares"].get(latest) and mkt_shares:
        # Backfill the balance-sheet share row too, so anything reading it is consistent.
        balance_sheet.loc["Share Issued"] = [mkt_shares for _ in year_ends]
    equity = series["Stockholders Equity"].get(latest)
    ltd = series["Long Term Debt"].get(latest) or 0
    cash = series["_cash"].get(latest) or 0
    ni = series["Net Income"].get(latest)
    rev = series["Total Revenue"].get(latest)
    ocf = series["Operating Cash Flow"].get(latest)
    capex = series["Capital Expenditure"].get(latest)  # negative (outflow)
    da = series["_da"].get(latest)
    fcf = (ocf + capex) if (ocf is not None and capex is not None) else None
    opinc = series["Operating Income"].get(latest)
    market_cap = price * shares if (price and shares) else None

    # 5y daily closes for the relative-value multiples (1 FMP call; optional).
    prices_df = _historical_prices(ticker)

    info = {
        "currentPrice": price,
        "regularMarketPrice": price,
        "longName": name,
        "shortName": name,
        "sector": sector or "",
        "quoteType": "EQUITY",
        "currency": "USD",  # EDGAR filers report in USD
        "beta": beta or 1.0,
        "sharesOutstanding": shares,
        "totalDebt": ltd,
        "totalCash": cash,
        "marketCap": market_cap,
        "trailingEps": (ni / shares) if (ni and shares) else None,
        "revenuePerShare": (rev / shares) if (rev and shares) else None,
        "freeCashflow": fcf,
        "ebitda": (opinc + da) if (opinc is not None and da is not None) else opinc,
        "enterpriseValue": (market_cap + ltd - cash) if market_cap else None,
        "bookValue": (equity / shares) if (equity and shares) else None,
        # No forward analyst estimates from EDGAR -> DCF uses historical CAGR, and
        # the consensus defers to the external forward DCF where available.
        "earningsGrowth": None,
        "revenueGrowth": None,
        "forwardEps": None,
        "longBusinessSummary": "",
    }

    stock = _FmpStock(info, financials, balance_sheet, cashflow, pd.DataFrame(), prices_df)
    return stock, info


# Finnhub's `finnhubIndustry` uses its own taxonomy; the valuation engine keys off
# yfinance/GICS sector names (e.g. the DCF's high-growth terminal bucket
# {Technology, Communication Services, Healthcare} and FINANCIAL_SECTORS). Map the
# common Finnhub industries onto those names so, e.g., Alphabet ("Media") lands in
# Communication Services rather than defaulting to the low-growth bucket.
_FINNHUB_SECTOR_MAP = {
    "Media": "Communication Services",
    "Communication Services": "Communication Services",
    "Telecommunication": "Communication Services",
    "Technology": "Technology",
    "Semiconductors": "Technology",
    "Health Care": "Healthcare",
    "Healthcare": "Healthcare",
    "Pharmaceuticals": "Healthcare",
    "Biotechnology": "Healthcare",
    "Life Sciences Tools & Services": "Healthcare",
    "Banking": "Financial Services",
    "Financial Services": "Financial Services",
    "Insurance": "Insurance",
    "Retail": "Consumer Cyclical",
    "Automobiles": "Consumer Cyclical",
    "Consumer products": "Consumer Defensive",
    "Beverages": "Consumer Defensive",
    "Food Products": "Consumer Defensive",
    "Energy": "Energy",
    "Oil & Gas": "Energy",
    "Utilities": "Utilities",
    "Industrial Conglomerates": "Industrials",
    "Machinery": "Industrials",
    "Real Estate": "Real Estate",
    "Basic Materials": "Basic Materials",
    "Chemicals": "Basic Materials",
}


def _map_sector(finnhub_industry):
    """Translate a Finnhub industry string to the engine's GICS-style sector name."""
    if not finnhub_industry:
        return None
    return _FINNHUB_SECTOR_MAP.get(finnhub_industry, finnhub_industry)


def _market_context(ticker: str):
    """(current_price, sector, name, beta, shares) for an EDGAR filer.

    The current PRICE is the one hard requirement (no price -> no valuation), and it
    must survive the FMP daily cap — the whole point of the EDGAR path. So price comes
    from Finnhub FIRST (free, 60/min, uncapped, never IP-blocked), which also yields
    sector/name/beta/shares. FMP quote is only a last-resort price backup (rotates
    across the configured keys). Finnhub is used *solely* for market data here, never
    statements. `shares` is a fallback for the handful of filers whose XBRL weighted-
    average diluted share count isn't in a standard tag (e.g. Visa, multi-class)."""
    price = sector = name = beta = shares = None
    try:
        from services import finnhub_fallback as FH
        price = FH._quote_price(ticker)
        prof = FH._get(f"stock/profile2?symbol={ticker}")
        if isinstance(prof, dict):
            sector = _map_sector(prof.get("finnhubIndustry"))
            name = prof.get("name")
            # Finnhub reports shareOutstanding in MILLIONS.
            so = _num(prof.get("shareOutstanding"))
            shares = so * 1e6 if so else None
        met = FH._get(f"stock/metric?symbol={ticker}&metric=all")
        if isinstance(met, dict):
            beta = _num((met.get("metric") or {}).get("beta"))
    except Exception:
        pass
    if price is None:  # last resort: FMP quote (only if it still has budget)
        from services.fmp_fallback import _first
        q = _first(_fmp_get(f"quote?symbol={ticker}")) or {}
        price = _num(q.get("price"))
        name = name or q.get("name")
        shares = shares or _num(q.get("sharesOutstanding"))
    return price, sector, name, beta, shares


def _historical_prices(ticker: str):
    """5y daily closes (ascending) for relative-value multiples. Uses FMP's cheap
    historical endpoint; returns None if unavailable (relative value is skipped)."""
    hist = _fmp_get(f"historical-price-eod/light?symbol={ticker}")
    if not isinstance(hist, list) or not hist:
        return None
    rows = []
    for h in hist:
        p = _num(h.get("price"))
        d = h.get("date")
        if p is None or not d:
            continue
        rows.append((pd.Timestamp(str(d)[:10]), p))
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    return pd.DataFrame({"Close": [p for _, p in rows]}, index=[d for d, _ in rows])
