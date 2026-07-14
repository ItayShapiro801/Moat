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




# A hung provider request must never stall an endpoint forever (that leaves the
# UI card spinning). Cap each attempt; on timeout the chain moves to the next
# provider, and the endpoint ultimately returns (or stale-serves) rather than hang.
_LLM_TIMEOUT = 20  # seconds per provider attempt


def _provider_groq(system_prompt, user_prompt, max_tokens):
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY, timeout=_LLM_TIMEOUT, max_retries=0)
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
        request_options={"timeout": _LLM_TIMEOUT},
    )
    return resp.text


def _provider_cerebras(system_prompt, user_prompt, max_tokens):
    from cerebras.cloud.sdk import Cerebras
    client = Cerebras(api_key=CEREBRAS_API_KEY, timeout=_LLM_TIMEOUT, max_retries=0)
    resp = client.chat.completions.create(
        # This key's catalog is gpt-oss-120b / zai-glm-4.7 / gemma-4-31b — the old
        # "llama-3.3-70b" 404s (model not available on this account), which silently
        # killed the whole Cerebras tier. gpt-oss-120b is the strongest that works.
        model="gpt-oss-120b",
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



def _parse_llm_json(text):
    """Parse JSON from an LLM response, tolerating markdown fences / preambles.
    Some models wrap JSON in ```json ... ``` or add a sentence before it even when
    asked not to; a bare json.loads would throw and wrongly discard a good answer."""
    if not text:
        return None
    try:
        return json_mod.loads(text)
    except Exception:
        pass
    # Strip a ```json ... ``` (or ``` ... ```) fence if present.
    t = text.strip()
    if "```" in t:
        import re
        m = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
        if m:
            try:
                return json_mod.loads(m.group(1).strip())
            except Exception:
                pass
    # Last resort: grab the outermost {...} object.
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json_mod.loads(t[start:end + 1])
        except Exception:
            pass
    return None


def _llm_call(system_prompt, user_prompt, max_tokens=900, order=("cerebras", "groq", "gemini")):
    """Try LLM providers in order until one returns parseable JSON.
    Returns (parsed_dict, source) or (None, None) if all fail.

    Order puts Cerebras FIRST: on these free tiers Groq and Gemini are frequently
    rate-limited/quota-exhausted (observed 0/3 success), while Cerebras (gpt-oss-120b)
    is reliably up (3/3). Leading with the working provider means calls succeed
    immediately instead of burning time failing through two dead providers first —
    which was making the single-shot cards (valuation-review) intermittently blank.
    Groq/Gemini remain as backups for when their daily quotas reset.

    Two passes over the providers: when only one provider is actually up (common on
    these free tiers), a single transient hiccup on it would otherwise blank the card
    entirely (cards don't cache failures, so the user sees "Couldn't load" until they
    reload). A second pass turns a one-off failure into a retry, which makes the
    first-paint of the cards reliable instead of flaky."""
    for attempt in range(2):
        for name in order:
            has_key, fn = _PROVIDERS[name]
            if not has_key():
                continue
            try:
                text = fn(system_prompt, user_prompt, max_tokens)
                parsed = _parse_llm_json(text)
                if parsed is not None:
                    return parsed, name
            except Exception:
                continue
    return None, None


def _llm_json(system_prompt, user_prompt, max_tokens=900, order=("cerebras", "groq", "gemini")):
    parsed, _ = _llm_call(system_prompt, user_prompt, max_tokens, order)
    return parsed


