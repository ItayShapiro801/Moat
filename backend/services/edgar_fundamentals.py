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
    """Reuse the ownership module's SEC ticker->CIK map (10-digit, zero-padded).
    The SEC map spells share classes with a dash (BRK.B -> BRK-B), so normalize."""
    try:
        from routers.ownership import _load_cik_map
        sym = (ticker or "").strip().upper().replace(".", "-")
        return _load_cik_map().get(sym)
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
    price, sector, name, beta, mkt_shares, metric, industry = _market_context(ticker)
    if price is None:
        return None  # can't value without a current price
    name = facts_bundle.get("name") or name or ticker

    latest = year_ends[0]
    # Prefer the XBRL weighted-average diluted count; fall back to the market
    # provider's share count for filers whose share tag we can't resolve (e.g. Visa's
    # multi-class structure), so per-share values (DCF, EPS) don't collapse to zero.
    xbrl_shares = series["_shares"].get(latest)
    shares = xbrl_shares or mkt_shares or None

    # SCALE GUARD: some filers report share counts in a scaled unit (e.g. MCD's XBRL
    # gives weighted-avg shares as "716.4" meaning 716.4 MILLION). Read literally,
    # that made EPS and per-share DCF explode by ~1e6 (MCD intrinsic value showed
    # $291 MILLION). When the market provider has a share count and the two differ by
    # >100x, the XBRL figure is mis-scaled — trust the market provider's actual count.
    if xbrl_shares and mkt_shares and mkt_shares > 0:
        ratio = mkt_shares / xbrl_shares
        if ratio > 100 or ratio < 0.01:
            shares = mkt_shares

    if shares != xbrl_shares and shares:
        # Keep the balance-sheet share row consistent with the count we actually use.
        balance_sheet.loc["Share Issued"] = [shares for _ in year_ends]
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

    # --- Ratios computed from EDGAR statements (so /metrics is NOT all-N/A on the
    # EDGAR path — previously these only came from yfinance/FMP). Finnhub's metric
    # blob fills a couple it can't derive (P/B, EV/EBITDA when available). ---
    cur_assets = series["Current Assets"].get(latest)
    cur_liab = series["Current Liabilities"].get(latest)
    total_assets = series["Total Assets"].get(latest)
    ebitda_val = (opinc + da) if (opinc is not None and da is not None) else opinc
    current_ratio = (cur_assets / cur_liab) if (cur_assets and cur_liab and cur_liab > 0) else None
    profit_margin = (ni / rev) if (ni is not None and rev and rev > 0) else None
    roa = (ni / total_assets) if (ni is not None and total_assets and total_assets > 0) else None
    price_to_book = (price / (equity / shares)) if (equity and shares and equity > 0) else None
    debt_to_equity = (ltd / equity * 100) if (equity and equity > 0) else None
    ent_val = (market_cap + ltd - cash) if market_cap else None
    ev_ebitda = (ent_val / ebitda_val) if (ent_val and ebitda_val and ebitda_val > 0) else None
    # Finnhub fallbacks where EDGAR can't compute (e.g. TTM figures).
    price_to_book = price_to_book or _num(metric.get("pbAnnual")) or _num(metric.get("pbQuarterly"))
    ev_ebitda = ev_ebitda or _num(metric.get("currentEv/freeCashFlowTTM"))

    # 5y daily closes for the relative-value multiples (1 FMP call; optional).
    prices_df = _historical_prices(ticker)

    info = {
        "currentPrice": price,
        "regularMarketPrice": price,
        "longName": name,
        "shortName": name,
        "sector": sector or "",
        "industry": industry or "",
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
        # Growth is deliberately left None here. If we pre-filled it with the Finnhub
        # PROXY, the downstream enrich_growth() would see a non-None value and skip
        # BusinessQuant entirely — so the EDGAR path would NEVER use the real analyst
        # forward estimates (the whole reason BQ exists). enrich_growth() runs right
        # after resolution and fills this best-to-good: BQ real estimate -> proxy.
        "earningsGrowth": None,
        "revenueGrowth": None,
        "forwardEps": None,
        "longBusinessSummary": "",
        # Current market multiples from Finnhub (uncapped), so the relative-value
        # step works without FMP's historical prices. Consumed by _relative_from_metric.
        "_finnhub_metric": metric,
        "trailingPE": _num(metric.get("peTTM")) or _num(metric.get("peBasicExclExtraTTM")),
        # Ratios computed above (yfinance key names so /metrics reads them directly).
        "currentRatio": current_ratio,
        "profitMargins": profit_margin,
        "returnOnAssets": roa,
        "priceToBook": price_to_book,
        "enterpriseToEbitda": ev_ebitda,
        "debtToEquity": debt_to_equity,
        # Dividend + liquidity fields from Finnhub's metric blob (yfinance key names),
        # so the "Dividends & Income" and quick-ratio cells populate on the EDGAR path
        # instead of showing N/A. Finnhub reports yield/payout as percents.
        "quickRatio": _num(metric.get("quickRatioAnnual")) or _num(metric.get("quickRatioQuarterly")),
        "dividendYield": (_num(metric.get("currentDividendYieldTTM"))
                          or _num(metric.get("dividendYieldIndicatedAnnual"))),
        "dividendRate": _num(metric.get("dividendIndicatedAnnual")) or _num(metric.get("dividendPerShareAnnual")),
        "payoutRatio": (_num(metric.get("payoutRatioTTM")) / 100.0
                        if _num(metric.get("payoutRatioTTM")) is not None else None),
    }

    stock = _FmpStock(info, financials, balance_sheet, cashflow, pd.DataFrame(), prices_df)
    return stock, info


def _growth_from_metric(metric, kind):
    """Forward-ish growth rate (fraction) from Finnhub's historical growth figures.

    No free source gives true analyst forward estimates, so we proxy the future with
    the company's own recent trajectory: blend the 5-year CAGR (durable trend) with
    the latest TTM-YoY (recent momentum), 60/40. Finnhub reports these as percents.
    Falls back across eps<->revenue if one is missing. Returns None if neither."""
    if not metric:
        return None
    if kind == "eps":
        five, ttm, alt = "epsGrowth5Y", "epsGrowthTTMYoy", "revenueGrowth5Y"
    else:
        five, ttm, alt = "revenueGrowth5Y", "revenueGrowthTTMYoy", "epsGrowth5Y"
    g5 = _num(metric.get(five))
    gt = _num(metric.get(ttm))
    ga = _num(metric.get(alt))
    parts = []
    if g5 is not None:
        parts.append((g5, 0.6))
    if gt is not None:
        parts.append((gt, 0.4))
    if not parts and ga is not None:
        parts.append((ga, 1.0))
    if not parts:
        return None
    wsum = sum(w for _, w in parts)
    pct = sum(v * w for v, w in parts) / wsum
    return pct / 100.0  # percent -> fraction


# Cache Finnhub's stock/metric per ticker (24h) so enriching every source doesn't
# add a call when we've already fetched it (e.g. the EDGAR path fetched it too).
_METRIC_CACHE: dict[str, tuple] = {}
_METRIC_TTL = 24 * 3600


def _finnhub_metric(ticker: str) -> dict:
    """Finnhub's free/uncapped stock/metric dict for a ticker (cached 24h)."""
    ticker = ticker.upper()
    hit = _METRIC_CACHE.get(ticker)
    if hit and time.time() - hit[0] < _METRIC_TTL:
        return hit[1]
    metric = {}
    try:
        from services import finnhub_fallback as FH
        met = FH._get(f"stock/metric?symbol={ticker}&metric=all")
        if isinstance(met, dict):
            metric = met.get("metric") or {}
    except Exception:
        pass
    _METRIC_CACHE[ticker] = (time.time(), metric)
    return metric


def enrich_growth(ticker: str, info: dict) -> None:
    """Fill in forward growth on `info` IN PLACE when the data source didn't provide
    it, so DCF quality is independent of which provider served the statements.

    Precedence, best -> good-enough:
      1. Source already supplied it (yfinance's analyst growth) -> leave untouched.
      2. BusinessQuant next-year analyst consensus (real forward estimates, free but
         30/day per key -> rotated). This is the ideal input.
      3. Finnhub historical-growth PROXY (free, uncapped) -> used when BQ has no
         coverage or all its keys are spent, so growth is always populated.

    Without any forward-ish growth the DCF falls back to a weak historical CAGR that
    undervalues growth names (AAPL -> ~$124), so we always end with *something*."""
    if info is None:
        return
    if info.get("earningsGrowth") is not None or info.get("revenueGrowth") is not None:
        info.setdefault("_growth_provider", "native")  # e.g. yfinance analyst growth
        return  # source already provided forward growth — leave it

    # 2. Real analyst forward estimates from BusinessQuant (when budget remains).
    try:
        from services.businessquant import forward_growth
        bq = forward_growth(ticker)
        if bq.get("earningsGrowth") is not None:
            info["earningsGrowth"] = bq["earningsGrowth"]
        if bq.get("revenueGrowth") is not None:
            info["revenueGrowth"] = bq["revenueGrowth"]
        if info.get("earningsGrowth") is not None or info.get("revenueGrowth") is not None:
            info["_growth_provider"] = "businessquant"
            return  # got real estimates — done
    except Exception:
        pass

    # 3. Finnhub historical-growth proxy (uncapped) — always-available fallback.
    # Tagged so the valuation cache holds proxy-based results only briefly (they
    # should be recomputed with real analyst estimates once budget returns).
    metric = _finnhub_metric(ticker)
    if not metric:
        return
    eg = _growth_from_metric(metric, "eps")
    rg = _growth_from_metric(metric, "revenue")
    if eg is not None:
        info["earningsGrowth"] = eg
    if rg is not None:
        info["revenueGrowth"] = rg
    if eg is not None or rg is not None:
        info["_growth_provider"] = "finnhub_proxy"


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
    """(current_price, sector, name, beta, shares, metric) for an EDGAR filer.

    The current PRICE is the one hard requirement (no price -> no valuation), and it
    must survive the FMP daily cap — the whole point of the EDGAR path. So price comes
    from Finnhub FIRST (free, 60/min, uncapped, never IP-blocked), which also yields
    sector/name/beta/shares. FMP quote is only a last-resort price backup (rotates
    across the configured keys). Finnhub is used *solely* for market data here, never
    statements. `shares` is a fallback for the handful of filers whose XBRL weighted-
    average diluted share count isn't in a standard tag (e.g. Visa, multi-class).

    `metric` is Finnhub's full `stock/metric` dict (free, uncapped): it carries the
    forward-ish growth rates (eps/revenue 5y) and current market multiples (P/E, P/S,
    EV/FCF) that EDGAR statements can't provide — this is what lets the EDGAR path
    produce a GOOD valuation (DCF growth + relative value) without any FMP call."""
    price = sector = name = beta = shares = industry = None
    metric = {}
    try:
        from services import finnhub_fallback as FH
        price = FH._quote_price(ticker)
        prof = FH._get(f"stock/profile2?symbol={ticker}")
        if isinstance(prof, dict):
            industry = prof.get("finnhubIndustry")  # raw (e.g. "Banking") — used to
            # distinguish balance-sheet financials from payment networks downstream
            sector = _map_sector(industry)
            name = prof.get("name")
            # Finnhub reports shareOutstanding in MILLIONS.
            so = _num(prof.get("shareOutstanding"))
            shares = so * 1e6 if so else None
        metric = _finnhub_metric(ticker)  # shared 24h cache (reused by enrich_growth)
        beta = _num(metric.get("beta"))
    except Exception:
        pass
    if price is None:  # last resort: FMP quote (only if it still has budget)
        from services.fmp_fallback import _first
        q = _first(_fmp_get(f"quote?symbol={ticker}")) or {}
        price = _num(q.get("price"))
        name = name or q.get("name")
        shares = shares or _num(q.get("sharesOutstanding"))
    return price, sector, name, beta, shares, metric, industry


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
