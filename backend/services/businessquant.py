"""BusinessQuant — free analyst FORWARD estimates for the DCF growth input.

EDGAR gives us statements and Finnhub gives us price/current-multiples, but neither
provides *forward* analyst estimates (next-year revenue/EPS consensus) — the single
best input for a DCF's growth rate. BusinessQuant does, sourced from SEC filings +
Street consensus, and its free tier includes them. The catch: 30 calls/day PER KEY,
so we rotate across several free-account keys (exactly like the FMP client) and,
once they're all spent, the caller falls back to the Finnhub historical-growth proxy
— valuations never break, they're just slightly less precise until the cap resets.

Public API:
  forward_growth(ticker) -> {"earningsGrowth": float|None, "revenueGrowth": float|None}
      Next-fiscal-year consensus growth (fraction) vs the latest reported actual,
      cached 24h. Returns Nones when BQ has no data or all keys are exhausted.
"""
from __future__ import annotations

import time
import threading
import json as json_mod
import urllib.request
import urllib.error

from config import BUSINESSQUANT_API_KEYS

BQ_BASE = "https://data.businessquant.com"

# --- multi-key rotation (mirrors services.fmp_fallback) ---------------------
# Each free key allows 30 requests/day; drain one until it 429s, then advance to
# the next. A spent key is put on a cooldown rather than retried every request.
# The daily cap resets at BQ's midnight, but a long cooldown is a safe re-probe.
_KEY_COOLDOWN = 60 * 60  # seconds before re-trying a capped key
_key_lock = threading.Lock()
_key_spent_until: dict[int, float] = {}
_key_cursor = [0]


def _mark_spent(i: int) -> None:
    with _key_lock:
        _key_spent_until[i] = time.time() + _KEY_COOLDOWN
        if BUSINESSQUANT_API_KEYS:
            _key_cursor[0] = (i + 1) % len(BUSINESSQUANT_API_KEYS)


def _key_order():
    now = time.time()
    n = len(BUSINESSQUANT_API_KEYS)
    return [
        (_key_cursor[0] + off) % n
        for off in range(n)
        if _key_spent_until.get((_key_cursor[0] + off) % n, 0) <= now
    ]


def _is_limit_body(data) -> bool:
    if isinstance(data, dict):
        msg = str(data.get("detail") or data.get("message") or "").lower()
        return "rate limit" in msg or "exceeded" in msg
    return False


def _bq_get(path: str):
    """GET a BusinessQuant endpoint, rotating across keys on the 30/day cap.
    Returns parsed JSON or None. Never raises."""
    if not BUSINESSQUANT_API_KEYS:
        return None
    sep = "&" if "?" in path else "?"
    for i in _key_order():
        url = f"{BQ_BASE}/{path}{sep}api_key={BUSINESSQUANT_API_KEYS[i]}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json_mod.loads(resp.read())
            if _is_limit_body(data):
                _mark_spent(i)
                continue
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 402, 403):
                _mark_spent(i)          # this key's daily cap is hit -> next key
                continue
            return None                 # 404/other -> ticker not covered; give up
        except Exception:
            return None                 # network/timeout -> give up
    return None                         # all keys exhausted for today


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# --- forward growth ---------------------------------------------------------
_GROWTH_CACHE: dict[str, tuple] = {}
_GROWTH_TTL = 24 * 3600  # estimates barely move day to day; conserve the 30/day cap
_MISS_TTL = 6 * 3600     # cache "no data / capped" briefly so we don't re-probe hard


def _next_year_growth(ticker: str, mode: str):
    """Consensus TREND growth (fraction/yr) from BusinessQuant's /estimates endpoint
    (mode = 'eps' | 'revenue'), or None. Costs one BQ call.

    Uses the multi-year CAGR from the latest reported actual to the FURTHEST annual
    forward estimate — not just next year vs last year. The single-year ratio is a
    trap for cyclical/rebound cases: a company whose base year was depressed by
    one-off charges shows next-year "growth" of +30-40% that is a RECOVERY, not a
    trend, and feeding that into a DCF produced fair values ~3x the market price.
    The CAGR across all published forward years (analysts typically publish 4+)
    smooths the rebound into the sustainable rate."""
    data = _bq_get(f"estimates?ticker={ticker}&mode={mode}")
    if not isinstance(data, dict):
        return None
    ann = None
    for dim in (data.get("data") or []):
        if dim.get("dimension") == "annual":
            ann = dim.get("estimates") or []
            break
    if not ann:
        return None
    # Latest reported actual = the anchor; furthest forward 'estimate' = the target.
    reported = [e for e in ann if e.get("data_type") == "reported"
                and _num(e.get("value_reported")) is not None]
    forward = [e for e in ann if e.get("data_type") == "estimate"
               and _num(e.get("value_estimate")) is not None]
    if not reported or not forward:
        return None
    base_row, last_row = reported[-1], forward[-1]
    base = _num(base_row.get("value_reported"))
    last = _num(last_row.get("value_estimate"))
    if not base or base <= 0 or last is None:
        return None

    def _year(row):
        try:
            return int(str(row.get("period", ""))[:4])
        except (ValueError, TypeError):
            return None

    y0, y1 = _year(base_row), _year(last_row)
    years = (y1 - y0) if (y0 and y1 and y1 > y0) else len(forward)
    if last <= 0:
        return None  # projected to stay unprofitable/negative — no usable trend
    return (last / base) ** (1.0 / max(years, 1)) - 1.0


_SB_GROWTH_PREFIX = "bqgrowth:"


def forward_growth(ticker: str) -> dict:
    """{"earningsGrowth": float|None, "revenueGrowth": float|None} — analyst
    consensus trend growth (fractions), cached 24h. All-None when BQ has no
    coverage or every key is exhausted (caller then uses the Finnhub proxy).

    Two cache tiers: in-memory (fast) and Supabase (persistent). Without the
    persistent tier, Render's free-tier restarts wiped the memory cache and every
    wake re-spent 2 of the 30/day key budget per stock — cutting the effective
    coverage well below the theoretical ~90 stocks/day. A fetched estimate now
    survives restarts for its full TTL, so each stock costs its 2 calls once/day."""
    ticker = ticker.upper()
    hit = _GROWTH_CACHE.get(ticker)
    now = time.time()
    if hit and now - hit[0] < (_GROWTH_TTL if hit[1].get("_hit") else _MISS_TTL):
        return {k: v for k, v in hit[1].items() if k != "_hit"}
    # Persistent tier (survives Render restarts). Only real hits are stored long;
    # rehydrate memory so subsequent lookups are instant.
    from services import supabase_cache as _sb
    sb_val = _sb.cache_get(f"{_SB_GROWTH_PREFIX}{ticker}")
    if isinstance(sb_val, dict) and ("earningsGrowth" in sb_val or "revenueGrowth" in sb_val):
        got = sb_val.get("earningsGrowth") is not None or sb_val.get("revenueGrowth") is not None
        _GROWTH_CACHE[ticker] = (now, {**sb_val, "_hit": got})
        return {k: v for k, v in sb_val.items() if k != "_hit"}
    if not BUSINESSQUANT_API_KEYS or not _key_order():
        return {"earningsGrowth": None, "revenueGrowth": None}  # no key/budget; don't cache a miss aggressively
    eg = _next_year_growth(ticker, "eps")
    rg = _next_year_growth(ticker, "revenue")
    result = {"earningsGrowth": eg, "revenueGrowth": rg}
    got = eg is not None or rg is not None
    # One concise line so the data source is visible in logs (BQ vs proxy fallback).
    print(f"[businessquant] {ticker}: eps={eg} rev={rg} usable_keys={len(_key_order())}", flush=True)
    _GROWTH_CACHE[ticker] = (now, {**result, "_hit": got})
    # Persist: real estimates for the full 24h; a no-coverage miss briefly (6h) so
    # restarts don't re-burn calls probing tickers BQ doesn't cover.
    _sb.cache_set(f"{_SB_GROWTH_PREFIX}{ticker}", result, _GROWTH_TTL if got else _MISS_TTL)
    return result
