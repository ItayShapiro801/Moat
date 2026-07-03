"""Persistent cache in Supabase — survives Render free-tier restarts.

Render's free tier stops the process after ~15 min idle, which wipes every
in-memory cache (`_VALUATION_CACHE`, etc.). That forces each stock to be re-fetched
on the next visit, re-spending ~7 FMP calls per analyze and draining the daily
budget fast. This module persists cache entries in a Supabase table so a valuation
computed once stays cached for its full TTL *across restarts* — a stock is then
fetched roughly once per 24h for real, not once per idle gap.

Design:
- Talks to Supabase's PostgREST endpoint with plain urllib (no new dependency) using
  the SERVICE-ROLE key (backend-only; bypasses RLS).
- Best-effort and fail-open: any error (no keys, network, missing table) returns
  None / silently no-ops, so the app falls back to the in-memory cache and never
  breaks because of the cache layer.
- Table `analysis_cache(key text primary key, value jsonb, expires_at timestamptz)`.

Public API:
  cache_get(key) -> dict | None      # fresh value, or None if missing/expired
  cache_get_stale(key) -> dict | None  # value regardless of age (stale-serve)
  cache_set(key, value, ttl_seconds)   # upsert with an expiry
"""
from __future__ import annotations

import time
import json as json_mod
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

_TABLE = "analysis_cache"
_TIMEOUT = 6


def enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _rest_url(path: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{path}"


def cache_get_stale(key: str):
    """Return the stored value for `key` regardless of expiry, or None. Also returns
    the expiry so the caller can tell fresh from stale via `cache_get`."""
    if not enabled():
        return None
    try:
        q = urllib.parse.urlencode({
            "key": f"eq.{key}",
            "select": "value,expires_at",
            "limit": "1",
        })
        req = urllib.request.Request(_rest_url(f"{_TABLE}?{q}"), headers=_headers())
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            rows = json_mod.loads(resp.read())
        if isinstance(rows, list) and rows:
            return rows[0]  # {"value": {...}, "expires_at": "..."}
    except Exception:
        pass
    return None


def cache_get(key: str):
    """Return the value only if present AND not past its expires_at, else None."""
    row = cache_get_stale(key)
    if not row:
        return None
    try:
        exp = row.get("expires_at")
        if exp:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= exp_dt:
                return None  # expired
        return row.get("value")
    except Exception:
        return None


def get_value_any_age(key: str):
    """Just the stored value regardless of age (for stale-serve), or None."""
    row = cache_get_stale(key)
    return row.get("value") if row else None


def cache_set(key: str, value, ttl_seconds: int) -> None:
    """Upsert (key, value, expires_at). Best-effort; silent on failure."""
    if not enabled():
        return
    try:
        expires_at = datetime.fromtimestamp(
            time.time() + ttl_seconds, tz=timezone.utc
        ).isoformat()
        body = json_mod.dumps({
            "key": key,
            "value": value,
            "expires_at": expires_at,
        }).encode()
        # on_conflict + Prefer: resolution=merge-duplicates => upsert by primary key.
        url = _rest_url(f"{_TABLE}?on_conflict=key")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
        )
        urllib.request.urlopen(req, timeout=_TIMEOUT).read()
    except Exception:
        pass  # cache write failures must never break a request
