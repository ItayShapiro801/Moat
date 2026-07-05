"""Moat Valuation Engine — the quantitative core of the site.

Four ideas, each a real (and defensible) piece of valuation theory, composed:

1. MOAT SCORE -> COMPETITIVE ADVANTAGE PERIOD (CAP).
   A commodity business sees excess returns competed away in a few years; a
   wide-moat compounder defends them for a decade+ (Mauboussin's "competitive
   advantage period"). We quantify the moat 0-100 from observable fundamentals
   (ROE, FCF margin, growth consistency, margin stability, Piotroski F-score)
   and map it to the DCF's explicit growth runway (5-12 years) and to how slowly
   growth fades. This is what a flat 5-year DCF structurally misses: it values
   Coca-Cola and a junior miner with the same horizon.

2. FADING GROWTH over the CAP. Growth starts at the analyst trend rate and decays
   geometrically toward the terminal rate — never compounded flat.

3. REVERSE DCF. Solve for the growth rate the CURRENT market price implies. The
   spread between market-implied growth and the analyst trend is a direct,
   quantified mispricing signal ("the market is pricing 6%; analysts see 15%").

4. MONTE CARLO fair-value distribution. ~1,000 paths sampling growth, discount
   rate and terminal growth around their point estimates produce P10-P90 fair
   value percentiles — an honest uncertainty band instead of three fake-precise
   scenario numbers. Deterministic seed so results are stable/cacheable.

The engine returns a dict shape-compatible with the legacy `compute_internal_dcf`
(scenarios / fair_value / wacc / ...) so the blending, guards and payload keep
working, plus an `engine` block with the moat/CAP/reverse-DCF/Monte-Carlo output.
"""
from __future__ import annotations

import math
import random
import statistics

from utils import safe_get

# ---------------------------------------------------------------------------
# Moat score (0-100) from observable fundamentals
# ---------------------------------------------------------------------------


def _series_from(df, label, n=5):
    """Row values (newest-first) from a statements DataFrame, or []."""
    try:
        if df is None or label not in df.index:
            return []
        vals = [v for v in df.loc[label].tolist()[:n] if v is not None and v == v]
        return vals
    except Exception:
        return []


def compute_moat_score(financials, balance_sheet, cashflow, info, f_score,
                       fcf_5yr=None, revenue_5yr=None):
    """(score 0-100, components dict). Each component is 0-1; missing data drops
    the component and the rest renormalize, so partial data still scores fairly."""
    components = {}

    # 1. Return on equity — high sustained ROE is the moat's footprint.
    ni = _series_from(financials, "Net Income")
    eq = _series_from(balance_sheet, "Stockholders Equity")
    if ni and eq and eq[0]:
        roe = ni[0] / eq[0] if eq[0] > 0 else None
        if roe is not None:
            components["roe"] = max(0.0, min(roe / 0.25, 1.0))  # 25%+ ROE = full marks

    # 2. FCF margin — pricing power shows up as cash conversion.
    if fcf_5yr and revenue_5yr:
        n = min(len(fcf_5yr), len(revenue_5yr))
        margins = [f / r for f, r in zip(fcf_5yr[-n:], revenue_5yr[-n:]) if r and r > 0]
        if margins:
            components["fcf_margin"] = max(0.0, min(statistics.median(margins) / 0.20, 1.0))

    # 3. Revenue growth consistency — moats compound steadily, not in lurches.
    rev = _series_from(financials, "Total Revenue")
    if len(rev) >= 3:
        chron = rev[::-1]  # oldest -> newest
        ups = sum(1 for a, b in zip(chron, chron[1:]) if b > a)
        components["growth_consistency"] = ups / (len(chron) - 1)

    # 4. Gross-margin stability — commodity businesses can't hold their margins.
    gp = _series_from(financials, "Gross Profit")
    if len(gp) >= 3 and len(rev) >= len(gp):
        gms = [g / r for g, r in zip(gp, rev[:len(gp)]) if r and r > 0]
        if len(gms) >= 3:
            vol = statistics.pstdev(gms)
            components["margin_stability"] = max(0.0, min(1.0, 1.0 - vol / 0.08))

    # 5. Piotroski F-score — overall fundamental health.
    if f_score is not None:
        components["f_score"] = max(0.0, min(f_score / 9.0, 1.0))

    if not components:
        return 50.0, {}  # nothing measurable: neutral moat
    score = 100.0 * sum(components.values()) / len(components)
    return round(score, 1), {k: round(v, 3) for k, v in components.items()}


def cap_years_from_moat(moat_score):
    """Explicit DCF horizon: 5y (no moat) .. 12y (fortress)."""
    return int(round(5 + 7 * max(0.0, min(moat_score, 100.0)) / 100.0))


def decay_from_moat(moat_score):
    """Yearly geometric fade of excess growth: weak moats fade fast (0.78),
    fortress moats defend growth much longer (0.93)."""
    return 0.78 + 0.15 * max(0.0, min(moat_score, 100.0)) / 100.0


# ---------------------------------------------------------------------------
# Fading-growth DCF over the CAP
# ---------------------------------------------------------------------------


def fading_dcf(base_fcf, g0, wacc, tgr, years, decay, net_debt, shares):
    """Per-share value of a DCF whose growth fades geometrically from g0 toward
    tgr over `years`, with a Gordon terminal after. Returns (fv, ev, eq)."""
    if not shares or base_fcf is None:
        return 0.0, 0.0, 0.0
    wacc = max(wacc, tgr + 0.02)  # keep the terminal multiple sane
    fcf = base_fcf
    pv = 0.0
    for t in range(1, years + 1):
        g_t = tgr + (g0 - tgr) * (decay ** (t - 1))
        fcf *= (1 + g_t)
        pv += fcf / (1 + wacc) ** t
    tv = fcf * (1 + tgr) / (wacc - tgr)
    pv_tv = tv / (1 + wacc) ** years
    ev = pv + pv_tv
    eq = ev - net_debt
    return (eq / shares), ev, eq


def reverse_dcf(price, base_fcf, wacc, tgr, years, decay, net_debt, shares):
    """The starting growth rate the market price implies, via bisection.
    Returns a fraction (e.g. 0.11) or None when no growth in [-40%, +60%]
    reproduces the price (base FCF non-positive, price dominated by cash, ...)."""
    if not price or price <= 0 or not shares or not base_fcf or base_fcf <= 0:
        return None
    lo, hi = -0.40, 0.60
    f_lo = fading_dcf(base_fcf, lo, wacc, tgr, years, decay, net_debt, shares)[0] - price
    f_hi = fading_dcf(base_fcf, hi, wacc, tgr, years, decay, net_debt, shares)[0] - price
    if f_lo * f_hi > 0:
        return None
    for _ in range(48):
        mid = (lo + hi) / 2
        f_mid = fading_dcf(base_fcf, mid, wacc, tgr, years, decay, net_debt, shares)[0] - price
        if abs(f_mid) < 0.01:
            return round(mid, 4)
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return round((lo + hi) / 2, 4)


# ---------------------------------------------------------------------------
# Monte Carlo fair-value distribution
# ---------------------------------------------------------------------------

_MC_PATHS = 1000


def monte_carlo(base_fcf, g0, wacc, tgr, years, decay, net_debt, shares, seed=42):
    """Percentiles of per-share fair value across ~1000 sampled parameter paths.
    Sampling (all around the point estimates — this quantifies estimate
    uncertainty, it does not re-forecast):
      growth  ~ N(g0, max(2%, 25% of |g0|))   analyst estimates miss; more for fast growers
      wacc    ~ N(wacc, 1%)                    discount-rate regime uncertainty
      tgr     ~ N(tgr, 0.4%)                   terminal assumptions are soft
      base    ~ N(base_fcf, 5%)                the starting cash flow is lumpy
    Deterministic seed -> stable, cacheable output."""
    if not shares or not base_fcf or base_fcf <= 0:
        return None
    rng = random.Random(seed)
    g_sigma = max(0.02, abs(g0) * 0.25)
    vals = []
    for _ in range(_MC_PATHS):
        g = rng.gauss(g0, g_sigma)
        w = max(0.06, rng.gauss(wacc, 0.01))
        t = max(0.0, min(rng.gauss(tgr, 0.004), 0.045))
        b = base_fcf * max(0.5, rng.gauss(1.0, 0.05))
        fv, _, _ = fading_dcf(b, g, w, t, years, decay, net_debt, shares)
        if fv > 0:
            vals.append(fv)
    if len(vals) < _MC_PATHS // 2:
        return None
    vals.sort()

    def pct(p):
        return round(vals[min(len(vals) - 1, int(p / 100 * len(vals)))], 2)

    return {"p10": pct(10), "p25": pct(25), "p50": pct(50), "p75": pct(75), "p90": pct(90)}


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def _bank_excess_return_value(info, financials, balance_sheet, growth_rate, ke):
    """Fair value per share for balance-sheet financials (banks, insurers) via the
    justified price-to-book model: P/B* = (ROE - g) / (Ke - g), value = P/B* x BVPS.

    A free-cash-flow DCF is meaningless for a bank — deposits and float flow
    through operating cash flow, so 'FCF' is noise. The standard analyst tool is
    the excess-return framework: a bank earning its cost of equity is worth book
    value; every point of ROE above Ke earns a premium to book. Derived from the
    Gordon model on residual income; clamped so an extreme ROE year can't print a
    silly multiple. Returns (value_per_share|None, justified_pb|None, roe|None)."""
    ni = _series_from(financials, "Net Income")
    eq = _series_from(balance_sheet, "Stockholders Equity")
    shares = safe_get(info, "sharesOutstanding", 0)
    if not ni or not eq or not shares or not eq[0] or eq[0] <= 0:
        return None, None, None
    # Average ROE over up to 3 years — one hot/cold year shouldn't set the multiple.
    roes = [n / e for n, e in zip(ni[:3], eq[:3]) if e and e > 0]
    if not roes:
        return None, None, None
    roe = statistics.median(roes)
    if roe <= 0:
        return None, None, roe
    g = max(0.0, min(growth_rate if growth_rate is not None else 0.04, roe * 0.6, 0.06))
    if ke <= g:
        ke = g + 0.02
    pb = (roe - g) / (ke - g)
    pb = max(0.5, min(pb, 3.5))
    bvps = eq[0] / shares
    return round(pb * bvps, 2), round(pb, 2), round(roe, 4)


def run_valuation_engine(info, financials, balance_sheet, cashflow,
                         fcf_5yr, revenue_5yr, sector, f_score, current_price,
                         base_fcf, growth_rate, growth_source, bank_mode=False):
    """Full engine run. Returns a dict shape-compatible with compute_internal_dcf
    (so blend/guards/payload keep working) plus an `engine` block.
    bank_mode swaps the FCF DCF for the excess-return (justified P/B) model —
    the correct lens for balance-sheet financials."""
    beta = safe_get(info, "beta", 1.0)
    shares = safe_get(info, "sharesOutstanding", 0)
    net_debt = safe_get(info, "totalDebt", 0) - safe_get(info, "totalCash", 0)

    # Discount rate: CAPM with an equity floor (no public equity discounts below
    # ~7.5% regardless of beta — a 6% WACC prints 30-60x terminal multiples).
    wacc = max(0.075, min(0.045 + beta * 0.05, 0.13))
    high_growth = {"Technology", "Communication Services", "Healthcare"}
    tgr = 0.035 if sector in high_growth else 0.025

    moat, moat_parts = compute_moat_score(
        financials, balance_sheet, cashflow, info, f_score, fcf_5yr, revenue_5yr
    )
    years = cap_years_from_moat(moat)
    decay = decay_from_moat(moat)

    g0 = max(-0.15, min(growth_rate if growth_rate is not None else 0.0, 0.25))

    if bank_mode:
        # Balance-sheet financial: FCF is meaningless (deposits/float flow through
        # OCF), so value on excess returns instead — justified P/B x book value.
        bank_fv, bank_pb, bank_roe = _bank_excess_return_value(
            info, financials, balance_sheet, g0, wacc
        )
        fv = bank_fv or 0.0
        ev = eq = round((bank_fv or 0) * shares) if shares else 0
        # Uncertainty band from the same model over sampled ROE/Ke/g.
        mc = None
        if bank_fv:
            rng = random.Random(42)
            vals = []
            for _ in range(_MC_PATHS):
                jitter_info = info  # ROE jitter handled via growth/ke sampling below
                v, _pb, _r = _bank_excess_return_value(
                    jitter_info, financials, balance_sheet,
                    max(0.0, rng.gauss(g0, 0.015)),
                    max(0.06, rng.gauss(wacc, 0.01)),
                )
                if v and v > 0:
                    vals.append(v * max(0.7, rng.gauss(1.0, 0.06)))
            if len(vals) >= _MC_PATHS // 2:
                vals.sort()
                p = lambda q: round(vals[min(len(vals) - 1, int(q / 100 * len(vals)))], 2)
                mc = {"p10": p(10), "p25": p(25), "p50": p(50), "p75": p(75), "p90": p(90)}
        implied = None  # reverse FCF-DCF doesn't apply to banks
        method = "excess_return"
    else:
        fv, ev, eq = fading_dcf(base_fcf, g0, wacc, tgr, years, decay, net_debt, shares)
        mc = monte_carlo(base_fcf, g0, wacc, tgr, years, decay, net_debt, shares)
        implied = reverse_dcf(current_price, base_fcf, wacc, tgr, years, decay, net_debt, shares)
        method = "fcf_dcf"

    # Scenarios from the Monte Carlo distribution (honest percentiles, not three
    # hand-tuned parameter sets). Falls back to the point estimate if MC failed.
    def _scenario(value, g, w):
        return {
            "value": round(value, 2) if (value and value > 0) else None,
            "growth": round(g, 4),
            "discount_rate": round(w, 4),
            "enterprise_value": round(ev),
            "equity_value": round(eq),
        }

    if mc:
        scenarios = {
            "bear": _scenario(mc["p25"], g0 * 0.6, wacc + 0.01),
            "base": _scenario(mc["p50"], g0, wacc),
            "bull": _scenario(mc["p75"], min(g0 * 1.3, 0.30), max(wacc - 0.01, 0.06)),
        }
        fair_value = mc["p50"]
    else:
        scenarios = {
            "bear": _scenario(fv * 0.75, g0 * 0.6, wacc + 0.01),
            "base": _scenario(fv, g0, wacc),
            "bull": _scenario(fv * 1.25, min(g0 * 1.3, 0.30), max(wacc - 0.01, 0.06)),
        }
        fair_value = round(fv, 2) if fv > 0 else 0

    meaningful = bool(fair_value and fair_value > 0
                      and (bank_mode or (base_fcf and base_fcf > 0)))

    # Is the FCF-DCF lens even the right tool for this company? When the DCF's own
    # answer lands wildly away from the price (thin/volatile FCF — AMZN-style), the
    # Monte Carlo band is precision theater; the UI hides it and the earnings/
    # relative anchors carry the consensus instead.
    dcf_reliable = bool(
        meaningful and current_price
        and 0.4 * current_price <= fair_value <= 2.5 * current_price
    )

    return {
        # ---- compute_internal_dcf-compatible surface ----
        "scenarios": scenarios,
        "fair_value": fair_value if meaningful else 0,
        "wacc": round(wacc, 4),
        "terminal_growth": tgr,
        "growth_rate": round(g0, 4),
        "growth_source": growth_source,
        "enterprise_value": round(ev),
        "equity_value": round(eq),
        "base_fcf": base_fcf,
        "meaningful": meaningful,
        # ---- engine block (new) ----
        "engine": {
            "method": method,  # "fcf_dcf" | "excess_return" (banks/insurers)
            "moat_score": moat,
            "moat_components": moat_parts,
            "cap_years": years,
            "growth_decay": round(decay, 3),
            "expected_growth": round(g0, 4),
            "implied_growth": implied,
            "monte_carlo": mc if dcf_reliable else None,
            "dcf_reliable": dcf_reliable,
        },
    }


# ---------------------------------------------------------------------------
# Diagnostic ensemble weights
# ---------------------------------------------------------------------------


def ensemble_weights(sources, fcf_5yr, revenue_5yr, info, growth_source,
                     dcf_reliable=True):
    """{label: weight} for the consensus, from measurable reliability diagnostics
    instead of blind equal averaging:
      - moat_dcf gets more weight the cleaner the FCF record (positive years) and
        when its growth input is a real forward estimate; when the engine itself
        flags the DCF unreliable (thin/cyclical FCF, value far from price — MU,
        AMZN), it is dropped to a token weight so a $52 DCF can't drag a $975
        stock's consensus;
      - earnings_multiple gets more weight the more stable the earnings;
      - relative_value / external_dcf carry fixed moderate weights.
    `sources` is the list of (label, value) actually available; weights renormalize
    over what's present so they always sum to 1."""
    labels = {label for label, _ in sources}
    raw = {}
    if "internal_dcf" in labels:
        pos_years = sum(1 for f in (fcf_5yr or []) if f and f > 0)
        fcf_quality = pos_years / max(len(fcf_5yr or []), 1)
        forward = 1.0 if str(growth_source).startswith("forward") else 0.7
        raw["internal_dcf"] = (0.25 + 0.35 * fcf_quality) * forward
        if not dcf_reliable:
            raw["internal_dcf"] = 0.05  # wrong lens for this company — token weight
    if "earnings_multiple" in labels:
        eps = safe_get(info, "trailingEps")
        raw["earnings_multiple"] = 0.35 if (eps and eps > 0) else 0.15
    if "relative_value" in labels:
        raw["relative_value"] = 0.30
    if "external_dcf" in labels:
        raw["external_dcf"] = 0.15
    total = sum(raw.values()) or 1.0
    return {k: round(v / total, 4) for k, v in raw.items()}
