# Architecture

Moat is a two-tier system: a Next.js frontend and a FastAPI backend, with Supabase
providing auth and user-data persistence.

## High-level flow

```
Browser ──▶ Next.js (App Router, React 19)
              │
              ├─▶ Supabase  (auth session, portfolio rows, insights cache)  [direct, RLS-protected]
              │
              └─▶ FastAPI    (all research/valuation/LLM endpoints)          [REST over NEXT_PUBLIC_API_BASE_URL]
                     │
                     ├─ yfinance → FMP → SEC EDGAR + Finnhub  (market-data resolver: capped
                     │                                          primary over an uncapped free floor)
                     ├─ BusinessQuant                        (analyst forward-growth estimates)
                     ├─ SEC EDGAR                            (Form 4 insider trades, 13F holdings)
                     ├─ Supabase (service role)               (persistent valuation/growth cache)
                     ├─ LLM chain: Groq → Gemini → Cerebras  (thesis, deep research, investor takes, insights)
                     └─ Resend                               (email delivery of PDF reports)
```

## Separation of concerns

- **Secrets stay on the backend.** Provider API keys (FMP, Finnhub, BusinessQuant,
  Groq, Gemini, Cerebras, Resend, the Supabase service-role key) are only ever read
  in `backend/config.py`. The browser only holds the Supabase anon key, which is
  safe by design and gated by Row-Level Security.
- **The frontend owns presentation and user state**; the backend owns computation.
- **Routers are thin; services are reusable.** HTTP endpoints validate input and
  shape responses; the valuation math and LLM orchestration live in `services/`.

## Backend layering

```
main.py                 # FastAPI app, CORS, router registration only
├── routers/            # HTTP layer — one module per domain
│   ├── analyze.py      # /analyze, /price-history, /fx-rate, /financials, /metrics
│   ├── investors.py    # /investors (six personas)
│   ├── thesis.py       # /thesis, /deep-research
│   ├── portfolio.py    # /portfolio-insights
│   ├── screener.py     # /screener
│   ├── ownership.py    # /insider-trades, /institutional-holdings
│   ├── search.py       # /search
│   └── reports.py      # /email-report
├── services/           # Domain logic — reusable, no HTTP concerns
│   ├── valuation_engine.py  # Moat Score → CAP-fading DCF, reverse DCF, Monte
│   │                        # Carlo, excess-return model (financials), ensemble weighting
│   ├── dcf.py          # legacy internal DCF (growth input + capex-normalized base
│   │                    # FCF) + external DCF (FMP)
│   ├── relative_value.py  # multiples valuation + merger-distortion guard
│   ├── blend.py        # cyclicality/mismatch flags + confidence rating
│   ├── piotroski.py    # F-Score
│   ├── edgar_fundamentals.py  # SEC EDGAR → yfinance-shaped adapter (free, uncapped)
│   ├── fmp_fallback.py # FMP client, multi-key rotation, stale-quote guard
│   ├── finnhub_fallback.py  # Finnhub client — uncapped price/quote backup
│   ├── businessquant.py  # analyst forward-growth estimates, multi-key rotation
│   ├── supabase_cache.py  # persistent cache across backend restarts
│   └── llm_providers.py# provider chain, response cache, per-key locks
├── config.py           # env vars + constants (sectors, investor list, fund CIKs)
├── models.py           # Pydantic request models
└── utils.py            # shared pure helpers
```

### The Moat Valuation Engine

`analyze()` (in `routers/analyze.py`) resolves market data through the
yfinance → FMP → EDGAR/Finnhub chain, then composes:

1. **Moat Score (0–100)** — ROE, FCF margin, revenue-growth consistency,
   gross-margin stability, and the F-Score, each measured from the filings.
2. **CAP-fading DCF** (`services/valuation_engine.py`) — the moat score sets the
   DCF's explicit growth horizon (5–12 years) and how slowly growth decays toward
   the terminal rate, instead of a flat 5-year projection for every company.
3. **Reverse DCF** — solves backwards for the growth rate the market price implies,
   surfaced against the analyst-estimate growth actually used.
4. **Monte Carlo** — ~1,000 sampled paths produce a fair-value distribution; the
   bear/base/bull scenarios are its P25/P50/P75, not fixed parameter guesses.
5. **Excess-return model** — balance-sheet financials (banks/insurers, detected by
   `industry`) get `P/B* = (ROE−g)/(Ke−g)` instead of a DCF; payment networks in the
   same broad sector keep the normal DCF.
6. **External DCF** (`services/dcf.py`) — independent estimate from Financial Modeling Prep.
7. **Relative value** (`services/relative_value.py`) — multiples vs. the company's own
   5-year history, with a guard that detects recently merged/reorganized companies
   (large share-count jumps or earnings discontinuities) and re-anchors on forward
   data instead of corrupted history.
8. **Diagnostic ensemble weighting** — each source's weight reflects a measurable
   reliability signal (FCF-record cleanliness, growth-input quality, EPS
   positivity) rather than a blind average; `blend.py` separately flags
   cyclicality/source-mismatch and sets the confidence rating.

### LLM provider chain

`services/llm_providers.py` exposes a single `_llm_call` that tries Groq, then
Gemini, then Cerebras, so a rate-limited or failing provider degrades gracefully.
Generated responses are cached in-memory with a TTL, and a **per-key lock** prevents
a "cache stampede" — when many requests hit the same uncached ticker at once, only
the first triggers generation and the rest reuse the result.

## Frontend layering

- `src/app/` — App Router routes. **Each folder maps to a URL**; this structure is
  dictated by the framework and must not be reorganized arbitrarily.
- `src/components/` — feature components, with reusable primitives isolated in
  `src/components/ui/`.
- `src/lib/` — cross-cutting concerns: `api.ts` (backend base URL), `auth-context.tsx`
  (Supabase auth), `supabase/client.ts` (browser client).

All backend calls resolve their base URL from `src/lib/api.ts`, so the API target
is configurable per environment via `NEXT_PUBLIC_API_BASE_URL`.

## Asset-class awareness

`analyze()` branches on yfinance `quoteType`. Operating companies (EQUITY) get the
full valuation pipeline; ETFs, crypto, and indices return a price-only response
(plus ETF metadata) because DCF, F-Score, and investor personas require company
financials that these instruments do not have. The frontend mirrors this branch.
