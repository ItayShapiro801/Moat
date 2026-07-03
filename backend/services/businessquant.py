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
    """Consensus growth (fraction) for the next fiscal year vs the latest reported
    actual, from BusinessQuant's /estimates endpoint (mode = 'eps' | 'revenue'),
    or None. Costs one BQ call."""
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
    # Latest reported actual = the anchor; first forward 'estimate' = next year.
    reported = [e for e in ann if e.get("data_type") == "reported"
                and _num(e.get("value_reported")) is not None]
    forward = [e for e in ann if e.get("data_type") == "estimate"
               and _num(e.get("value_estimate")) is not None]
    if not reported or not forward:
        return None
    base = _num(reported[-1].get("value_reported"))
    nxt = _num(forward[0].get("value_estimate"))
    if not base or base <= 0 or nxt is None:
        return None
    return nxt / base - 1.0


def forward_growth(ticker: str) -> dict:
    """{"earningsGrowth": float|None, "revenueGrowth": float|None} — next-year
    analyst consensus growth (fractions), cached 24h. All-None when BQ has no
    coverage or every key is exhausted (caller then uses the Finnhub proxy)."""
    ticker = ticker.upper()
    hit = _GROWTH_CACHE.get(ticker)
    now = time.time()
    if hit and now - hit[0] < (_GROWTH_TTL if hit[1].get("_hit") else _MISS_TTL):
        return {k: v for k, v in hit[1].items() if k != "_hit"}
    if not BUSINESSQUANT_API_KEYS or not _key_order():
        return {"earningsGrowth": None, "revenueGrowth": None}  # no key/budget; don't cache a miss aggressively
    eg = _next_year_growth(ticker, "eps")
    rg = _next_year_growth(ticker, "revenue")
    result = {"earningsGrowth": eg, "revenueGrowth": rg}
    _GROWTH_CACHE[ticker] = (now, {**result, "_hit": (eg is not None or rg is not None)})
    return result
