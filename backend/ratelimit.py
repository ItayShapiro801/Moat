"""Lightweight per-IP rate limiting + ticker validation for a public, key-less API.

The backend has no auth, so a single visitor could loop `/analyze/<random>` or
`/deep-research/AAPL?refresh=true` and drain the FMP / BusinessQuant / LLM free-tier
budgets for everyone. This adds:

- A fixed-window per-IP limiter with two tiers: a stricter one for the expensive
  LLM/AI endpoints, a looser one for everything else. Implemented as ASGI-level
  middleware so no endpoint signatures change (slowapi's decorator needs a Request
  arg on every handler, which would touch dozens of functions).
- Ticker validation: reject anything that isn't a plausible symbol before it can
  reach an upstream provider (also closes a query-param injection where the ticker
  was interpolated raw into provider URLs).

Single-instance in-memory counters — correct for the free Render deploy (one
worker). If the app is ever scaled horizontally, swap the store for Redis.
"""
from __future__ import annotations

import re
import time
import threading
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse

# --- Ticker validation ------------------------------------------------------
# 1-10 chars, A-Z 0-9 . - only (covers BRK.B, BF.B, RDS.A, crypto like BTC-USD).
_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")


def is_valid_ticker(ticker: str) -> bool:
    return bool(ticker and _TICKER_RE.match(ticker))


# --- Rate limiter ------------------------------------------------------------
# Endpoints whose first path segment is LLM/AI-expensive get the stricter tier.
_LLM_PREFIXES = ("/deep-research", "/investors", "/thesis", "/valuation-review",
                 "/portfolio-insights")

# Per-IP fixed-window limits.
_GENERAL_LIMIT = 40      # requests
_GENERAL_WINDOW = 60     # seconds
_LLM_LIMIT = 8           # requests (a page load fires investors+thesis+review)
_LLM_WINDOW = 60         # seconds

_lock = threading.Lock()
_hits: dict[str, deque] = {}


def _client_ip(request: Request) -> str:
    # Render/most proxies set X-Forwarded-For; take the first hop.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_llm_path(path: str) -> bool:
    return any(path.startswith(p) for p in _LLM_PREFIXES)


def _allowed(ip: str, path: str) -> bool:
    limit, window = (_LLM_LIMIT, _LLM_WINDOW) if _is_llm_path(path) else (_GENERAL_LIMIT, _GENERAL_WINDOW)
    bucket = "llm" if _is_llm_path(path) else "gen"
    key = f"{ip}:{bucket}"
    now = time.time()
    with _lock:
        dq = _hits.get(key)
        if dq is None:
            dq = deque()
            _hits[key] = dq
        # drop timestamps outside the window
        cutoff = now - window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


# Ticker-carrying routes: the LAST path segment is a symbol we must validate
# before it reaches an upstream provider (blocks junk + query-param injection).
_TICKER_ROUTES = ("/analyze/", "/price-history/", "/financials/", "/metrics/",
                  "/investors/", "/thesis/", "/valuation-review/", "/deep-research/",
                  "/insider-trades/", "/institutional-holdings/")


def _reject_bad_ticker(path: str) -> bool:
    for prefix in _TICKER_ROUTES:
        if path.startswith(prefix):
            symbol = path[len(prefix):].split("/", 1)[0]
            return not is_valid_ticker(symbol)
    return False


async def rate_limit_middleware(request: Request, call_next):
    # Health/warmup are cheap and used by the platform — never limit them.
    path = request.url.path
    if path in ("/health", "/warmup", "/"):
        return await call_next(request)
    # Validate the ticker up front so malformed symbols never hit a provider.
    if _reject_bad_ticker(path):
        return JSONResponse(status_code=400, content={"detail": "Invalid ticker symbol."})
    ip = _client_ip(request)
    if not _allowed(ip, path):
        retry = _LLM_WINDOW if _is_llm_path(path) else _GENERAL_WINDOW
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please slow down and try again shortly."},
            headers={"Retry-After": str(retry)},
        )
    return await call_next(request)
