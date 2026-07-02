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

from config import (GROQ_API_KEY, GEMINI_API_KEY, CEREBRAS_API_KEY,
                    LLM_TEMPERATURE, AI_CACHE_TTL, DEEP_CACHE_TTL)

__all__ = ["_cache_get","_cache_set","_get_key_lock","_cached_or_generate","_llm_call","_llm_json","AI_CACHE_TTL","DEEP_CACHE_TTL"]

_AI_CACHE: dict[str, tuple] = {}

def _cache_get(key, ttl):
    import time
    entry = _AI_CACHE.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None


def _cache_set(key, value):
    import time
    _AI_CACHE[key] = (time.time(), value)


def _cache_get_stale(key):
    """Return the last cached value regardless of age (None if never cached).
    Used to serve stale-but-real data when a fresh generation fails."""
    entry = _AI_CACHE.get(key)
    return entry[1] if entry else None


# Per-key locks prevent a "cache stampede": when many requests hit the SAME
# uncached ticker at once, only the first generates; the rest wait and reuse it.

import threading as _threading
_KEY_LOCKS: dict[str, "_threading.Lock"] = {}
_KEY_LOCKS_GUARD = _threading.Lock()


def _get_key_lock(key):
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = _threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _cached_or_generate(key, ttl, refresh, generate_fn):
    """Return cached value, or generate it once under a per-key lock so
    concurrent cache-misses for the same key don't stampede the LLM.
    generate_fn() must return (payload, should_cache)."""
    if not refresh:
        cached = _cache_get(key, ttl)
        if cached is not None:
            return cached
    lock = _get_key_lock(key)
    with lock:
        if not refresh:
            cached = _cache_get(key, ttl)  # filled while we waited on the lock
            if cached is not None:
                return cached
        payload, should_cache = generate_fn()
        if should_cache:
            _cache_set(key, payload)
            return payload
        # Generation failed/empty (e.g. all LLM providers rate-limited, or the
        # underlying market data was unavailable). Prefer a stale-but-real cached
        # result over an empty/"unavailable" one.
        stale = _cache_get_stale(key)
        if stale is not None:
            return stale
        return payload



def _ask_groq(groq_client, investor, facts_json, stats=None):
    if stats is not None:
        stats.bump("groq")
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": investor["system"]},
            {"role": "user", "content": _build_investor_prompt(facts_json)},
        ],
        temperature=LLM_TEMPERATURE,
        max_tokens=700,
        response_format={"type": "json_object"},
    )
    return _parse_investor(investor, resp.choices[0].message.content, "groq")


def _ask_gemini(investor, facts_json, stats=None):
    if stats is not None:
        stats.bump("gemini")
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        "gemini-2.5-flash",
        system_instruction=investor["system"],
    )
    resp = model.generate_content(
        _build_investor_prompt(facts_json),
        generation_config={
            "temperature": LLM_TEMPERATURE,
            # gemini-2.5-flash spends "thinking" tokens against this budget, so it
            # must be generous or the JSON answer gets truncated mid-string.
            "max_output_tokens": 3000,
            "response_mime_type": "application/json",
        },
    )
    return _parse_investor(investor, resp.text, "gemini")


class _CallStats:
    """Thread-safe counter of provider API calls per /investors request."""
    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self.counts = {"groq": 0, "gemini": 0}

    def bump(self, provider):
        with self._lock:
            self.counts[provider] = self.counts.get(provider, 0) + 1

    def total(self):
        return sum(self.counts.values())


def _ask_investor(groq_client, investor, facts_json, stats=None):
    """Evaluate one investor: exactly ONE Groq attempt; if it fails (e.g. rate
    limit), exactly ONE Gemini fallback attempt. This keeps the happy path at
    precisely one provider call per investor (6 total) while still being
    resilient. Returns dict, or None only if BOTH providers fail."""
    try:
        return _ask_groq(groq_client, investor, facts_json, stats)
    except Exception:
        pass
    if GEMINI_API_KEY:
        try:
            return _ask_gemini(investor, facts_json, stats)
        except Exception:
            pass
    return None



def _provider_groq(system_prompt, user_prompt, max_tokens):
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=LLM_TEMPERATURE,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def _provider_gemini(system_prompt, user_prompt, max_tokens):
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=system_prompt)
    resp = model.generate_content(
        user_prompt,
        generation_config={
            "temperature": LLM_TEMPERATURE,
            # gemini-2.5-flash spends "thinking" tokens against this budget
            "max_output_tokens": max(max_tokens * 2, 4000),
            "response_mime_type": "application/json",
        },
    )
    return resp.text


def _provider_cerebras(system_prompt, user_prompt, max_tokens):
    from cerebras.cloud.sdk import Cerebras
    client = Cerebras(api_key=CEREBRAS_API_KEY)
    resp = client.chat.completions.create(
        model="llama-3.3-70b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=LLM_TEMPERATURE,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


_PROVIDERS = {
    "groq": (lambda: bool(GROQ_API_KEY), _provider_groq),
    "gemini": (lambda: bool(GEMINI_API_KEY), _provider_gemini),
    "cerebras": (lambda: bool(CEREBRAS_API_KEY), _provider_cerebras),
}



def _llm_call(system_prompt, user_prompt, max_tokens=900, order=("groq", "gemini", "cerebras")):
    """Try LLM providers in order until one returns parseable JSON.
    Returns (parsed_dict, source) or (None, None) if all fail."""
    for name in order:
        has_key, fn = _PROVIDERS[name]
        if not has_key():
            continue
        try:
            text = fn(system_prompt, user_prompt, max_tokens)
            return json_mod.loads(text), name
        except Exception:
            continue
    return None, None


def _llm_json(system_prompt, user_prompt, max_tokens=900, order=("groq", "gemini", "cerebras")):
    parsed, _ = _llm_call(system_prompt, user_prompt, max_tokens, order)
    return parsed


