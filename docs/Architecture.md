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
                     ├─ yfinance / Financial Modeling Prep   (market data, fundamentals, external DCF)
                     ├─ SEC EDGAR                            (Form 4 insider trades, 13F holdings)
                     ├─ LLM chain: Groq → Gemini → Cerebras  (thesis, deep research, investor takes, insights)
                     └─ Resend                               (email delivery of PDF reports)
```

## Separation of concerns

- **Secrets stay on the backend.** Provider API keys (FMP, Groq, Gemini, Cerebras,
  Resend) are only ever read in `backend/config.py`. The browser only holds the
  Supabase anon key, which is safe by design and gated by Row-Level Security.
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
│   ├── dcf.py          # internal DCF + scenarios
│   ├── relative_value.py  # multiples valuation + merger-distortion guard
│   ├── blend.py        # blend/confidence engine
│   ├── piotroski.py    # F-Score
│   └── llm_providers.py# provider chain, response cache, per-key locks
├── config.py           # env vars + constants (sectors, investor list, fund CIKs)
├── models.py           # Pydantic request models
└── utils.py            # shared pure helpers
```

### The valuation engine

`analyze()` (in `routers/analyze.py`) composes the services:

1. **Internal DCF** (`services/dcf.py`) — discounted cash flow with sector-aware assumptions.
2. **External DCF** (`services/dcf.py`) — independent estimate from Financial Modeling Prep.
3. **Relative value** (`services/relative_value.py`) — multiples vs. the company's own
   5-year history, with a guard that detects recently merged/reorganized companies
   (large share-count jumps or earnings discontinuities) and re-anchors on forward
   data instead of corrupted history.
4. **Blend** (`services/blend.py`) — weights the three, applies cyclical/financial
   sector adjustments, and assigns a confidence rating.

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
