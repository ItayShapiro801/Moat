# Moat

Moat is an equity-research web application. Given a ticker, it computes a blended
intrinsic-value estimate, a Piotroski F-Score, and a set of derived metrics, then
layers on LLM-generated narrative analysis and a personal portfolio tracker.

It is a two-tier system: a **Next.js (App Router) frontend** and a **FastAPI
backend**, with **Supabase** (Postgres + Auth) for user data. The backend owns all
computation and third-party integrations; the frontend is a typed client that
renders results and manages auth/portfolio state.

> **Scope note.** This README describes only what is implemented in the codebase.
> Where a subsystem is intentionally simple (e.g. the screener runs as a batch job,
> the cache is in-memory), that is stated rather than dressed up. See
> [Limitations & Future Work](#limitations--future-work).

---

## Table of Contents

- [Overview](#overview)
- [System Design](#system-design)
- [Architecture](#architecture)
- [Core Components](#core-components)
- [Engineering Highlights](#engineering-highlights)
- [API Surface](#api-surface)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Developer Experience](#developer-experience)
- [Configuration](#configuration)
- [Scripts](#scripts)
- [Limitations & Future Work](#limitations--future-work)
- [Documentation](#documentation)

---

## Overview

The core idea is a **valuation pipeline**: pull a company's fundamentals from market
data providers, run several independent valuation methods, and blend them into a
single intrinsic-value estimate with a confidence rating. Narrative features
(thesis, investor-persona evaluations, deep-research report, portfolio insights)
are built on top by feeding the same computed data to an LLM under a fixed JSON
schema.

Responsibilities are split cleanly:

- **Frontend (`src/`)** — App Router routes, feature components, and a thin client
  layer (`src/lib/api.ts`). It calls Supabase directly for auth and portfolio rows
  (protected by Row-Level Security) and the FastAPI backend for everything else.
- **Backend (`backend/`)** — thin HTTP **routers** over reusable **services**. All
  market-data and LLM-provider keys live only here.
- **Supabase** — Postgres tables (`portfolio_holdings`, `portfolio_insights_cache`)
  with RLS, plus auth.

## System Design

### Data flow

```
Browser ──▶ Next.js (App Router, React 19)
   │              │
   │              ├─▶ Supabase            auth session, portfolio rows, insights cache
   │              │   (direct, RLS)
   │              │
   │              └─▶ FastAPI (REST)      all research / valuation / LLM endpoints
   │                     │                base URL from NEXT_PUBLIC_API_BASE_URL
   │                     │
   │                     ├─ yfinance / Financial Modeling Prep   fundamentals, prices, external DCF
   │                     ├─ SEC EDGAR                            Form 4 insiders, 13F holdings
   │                     ├─ LLM chain: Groq → Gemini → Cerebras  thesis, deep research, investor takes
   │                     └─ Resend                               PDF report email (optional)
```

### Separation of concerns

- **Routers are thin; services are reusable.** HTTP modules in `backend/routers/`
  validate input and shape responses. The valuation math, LLM orchestration, and
  scoring live in `backend/services/` and are imported by routers and by the
  standalone screener script alike.
- **Secrets never reach the browser.** The frontend holds only the Supabase anon
  key (safe by design, gated by RLS). FMP / Groq / Gemini / Cerebras / Resend keys
  are read exclusively in `backend/config.py`.
- **Configurable boundaries.** The frontend's backend URL
  (`NEXT_PUBLIC_API_BASE_URL`) and the backend's CORS allow-list
  (`CORS_ALLOWED_ORIGINS`) are environment-driven, so the same code runs locally and
  in production without edits.

### Why this stack (as reflected in the code)

- **FastAPI** for the backend: the work is CPU/IO-bound Python over `pandas`/
  `yfinance` and several SDKs (Groq, Gemini, Cerebras, Resend). Python keeps the
  data and provider code in one language, and FastAPI gives typed request models
  (Pydantic) and auto-generated OpenAPI docs (`/docs`) for free.
- **Next.js (App Router)** for the frontend: file-based routing, server/client
  components, and first-class TypeScript. Supabase auth is handled client-side via
  `@supabase/ssr`.

## Architecture

```
backend/
  main.py              FastAPI app: CORS + router registration only
  routers/             HTTP layer (one module per domain)
    analyze.py         /analyze, /price-history, /fx-rate, /financials, /metrics
    investors.py       /investors  (persona evaluations)
    thesis.py          /thesis, /deep-research
    portfolio.py       /portfolio-insights
    screener.py        /screener   (reads a precomputed cache)
    ownership.py       /insider-trades, /institutional-holdings  (SEC EDGAR)
    search.py          /search
    reports.py         /email-report
  services/            Domain logic (no HTTP concerns)
    dcf.py             internal 2-stage DCF + external DCF (FMP)
    relative_value.py  multiples valuation + merger/reorg detection
    blend.py           blend + confidence + sector/cyclical adjustments
    piotroski.py       F-Score (9 signals)
    llm_providers.py   provider fallback chain, response cache, per-key locks
  config.py            env vars + constants
  models.py            Pydantic request models
  utils.py             shared pure helpers
  run_screener.py      standalone batch job → screener_cache.json

src/
  app/                 App Router routes (each folder = a URL)
  components/          feature components + ui/ primitives
  lib/                 api.ts (backend base URL), auth-context, supabase client
```

## Core Components

### 1. Valuation engine (`backend/services/`, composed in `routers/analyze.py`)

`analyze(ticker)` orchestrates four independent valuation inputs and blends them.

- **Internal DCF (`dcf.py`)** — a 2-stage discounted-cash-flow model. Base free cash
  flow is projected 5 years, plus a Gordon-growth terminal value. The discount rate
  is a CAPM-style WACC (`0.045 + beta * 0.05`, clamped to `[0.06, 0.13]`); the growth
  input prefers forward earnings, then forward revenue, then a 3-year FCF CAGR
  (clamped to `[-0.15, 0.35]`); terminal growth is sector-aware (3.5% for
  Technology / Communication Services / Healthcare, else 2.5%). It runs three
  scenarios — **bear / base / bull** — by varying growth and WACC.
- **External DCF (`dcf.py`)** — an independent per-share DCF pulled from Financial
  Modeling Prep, used as a cross-check. Skipped gracefully if no API key.
- **Relative value (`relative_value.py`)** — multiples-based valuation computed from
  the company's **own 5-year history**: median P/E, P/S, EV/EBITDA, P/FCF, and P/B
  multiples are derived from past years, then applied to current per-share metrics.
- **Blend (`blend.py`)** — combines internal DCF, external DCF, and relative value
  into one estimate with weights and a confidence rating. Adjustments are applied
  based on company characteristics (see Engineering Highlights).

The same module computes the **Piotroski F-Score** (`piotroski.py`): the standard
9 binary signals across profitability (ROA, ROA trend, operating cash flow,
accruals), leverage/liquidity (long-term debt ratio, current ratio, share count),
and efficiency (gross margin, asset turnover), returning an integer 0–9.

### 2. Data aggregation layer (`backend/routers/`)

- **Three-provider data chain with automatic failover.** `yfinance` is an
  unofficial scraper whose requests get blocked from cloud IPs unpredictably, so it
  is treated as opportunistic rather than authoritative. The resolver
  (`routers/analyze.py` → `_resolve_market_data`) tries providers in order and the
  full valuation engine runs on whichever succeeds:
  1. **`yfinance`** — tried first for speed/freshness when the host IP isn't blocked.
  2. **Financial Modeling Prep (PRIMARY)** — an official API. When yfinance is
     blocked or empty, `services/fmp_fallback.py` builds a yfinance-shaped adapter
     (an `info` dict + `financials`/`balance_sheet`/`cashflow`/`quarterly_balance_sheet`
     DataFrames + `history()`) from FMP's statement endpoints, so the **entire** DCF /
     F-Score / relative-value / merger-guard pipeline runs unchanged on FMP data —
     a full valuation, not a degraded response.
  3. **Finnhub (last resort)** — `services/finnhub_fallback.py`. If both yfinance and
     FMP are unavailable, Finnhub (60 req/min free) provides at least basic
     quote/metric data. Its free fundamentals coverage is thinner, so this tier is
     intentionally degraded (price/name/sector with null valuation).
  Combining a daily-capped provider (FMP, 250/day) with a per-minute-capped one
  (Finnhub) yields more combined headroom than either alone. Unavailable fields
  return `null` rather than crashing; every request logs its source
  (`yfinance` | `fmp` | `fmp_fallback` | `finnhub_fallback`).
- **Full-valuation cache** (`routers/analyze.py`). Because FMP is now the primary
  source (not a rare fallback), a successful full valuation is cached per ticker for
  3 hours regardless of which provider produced it, to stay within FMP's 250/day
  budget. A cold `/analyze` costs ~8 FMP calls; a cache hit costs zero. Degraded
  (quote-only) responses are never cached, so the app recovers full data as soon as a
  provider does.
- **External DCF + FX** also use FMP. `/fx-rate` converts non-USD instruments to USD.
- **Ownership data** comes from **SEC EDGAR**: `/insider-trades` parses Form 4
  filings; `/institutional-holdings` reads 13F holdings. Both fan out concurrently
  with a `ThreadPoolExecutor` (see Engineering Highlights).
- **Deep research** (`thesis.py`) is a fan-in aggregator: it calls `analyze`,
  `metrics`, `financials`, `insider-trades`, and `institutional-holdings` internally
  through a `safe()` best-effort wrapper (each sub-fetch is isolated; a failure
  returns `{}` instead of failing the whole report), then passes the assembled data
  to the LLM.

### 3. Portfolio system (`src/app/portfolio/`, `backend/routers/portfolio.py`, Supabase)

- Holdings are stored per user in Supabase `portfolio_holdings` (RLS-enforced), with
  amount invested, shares, purchase price, currency, and asset type.
- The frontend computes live value, gain/loss, and allocation; non-USD holdings are
  converted to USD via the backend `/fx-rate` endpoint, and both native and USD
  values are shown.
- **Portfolio insights** (`/portfolio-insights`) sends the user's holdings to the
  LLM for a structured assessment. Results are cached in `portfolio_insights_cache`
  keyed by a **hash of the holdings**, so insights persist across visits and only
  regenerate when the portfolio actually changes.

### 4. LLM orchestration layer (`backend/services/llm_providers.py`)

- **Provider fallback chain.** `_llm_call` tries providers in order
  (**Groq → Gemini → Cerebras**), skipping any without a configured key, and returns
  the first response that parses as JSON. All providers are called in JSON-mode with
  a fixed temperature.
- **Response cache.** Generated narrative responses are cached in-memory with a TTL
  (12h for thesis/investors, 24h for deep research).
- **Cache-stampede protection.** Per-cache-key locks ensure that when multiple
  requests hit the same uncached ticker simultaneously, only the first triggers
  generation; the rest wait and reuse the result.

> **On the "investor" feature:** `/investors` evaluates a company against six
> configurable **investor-persona system prompts** (e.g. value, growth, contrarian
> styles). It is an LLM call constrained to a JSON schema (score + verdict +
> rationale per persona), grounded in the computed fundamentals — not a proprietary
> model. It is presented here as exactly that.

## Engineering Highlights

All of the following are present in the codebase:

- **Multi-method valuation with cross-checks** — internal DCF, an independent
  external DCF, and historical-multiples relative value are computed separately and
  blended, rather than relying on a single number.
- **Domain-aware blend adjustments** (`blend.py`):
  - *Financial-sector exclusion* — DCF is dropped for financials/insurance where it
    is unreliable.
  - *Dynamic cyclicality detection* — independent of the sector label, a company is
    treated as cyclical when its 5-year FCF coefficient of variation exceeds a
    threshold (or shows a negative year amid positive years).
  - *Merger / reorganization guard* (`relative_value.py` → `detect_reorganization`)
    — detects large share-count jumps that would distort per-share history and flags
    the result accordingly.
- **Provider fallback chain** — graceful degradation across three LLM providers;
  the system keeps working with any subset of keys configured.
- **Market-data fallback with rate-limit detection** — when the primary source
  (`yfinance`) throttles, the data endpoints transparently fail over to FMP and
  reshape responses to match, with per-source logging (see Data aggregation layer).
- **In-memory TTL cache + per-key stampede locks** — see the LLM orchestration layer
  above. The FMP fallback path adds its own short **60-second cache** (keyed by
  ticker), applied *only* to fallback responses, to protect FMP's limited daily
  budget during a throttling window. The primary `yfinance` path is never cached.
- **Concurrent API aggregation** — SEC EDGAR fan-out via `ThreadPoolExecutor`
  (`max_workers` of 3–4) in `ownership.py`.
- **Best-effort fan-in** — the deep-research aggregator isolates each sub-fetch with
  a `safe()` wrapper so one failing data source doesn't fail the report.
- **Asset-class branching** — `/analyze` inspects the instrument's `quoteType` and
  skips company-only metrics (DCF, F-Score, personas) for ETFs, crypto, and
  indices, returning price/metadata instead.
- **Environment-driven boundaries** — backend URL and CORS allow-list are config,
  not constants.

## API Surface

All backend endpoints (FastAPI, served with OpenAPI docs at `/docs`):

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/analyze/{ticker}` | Blended intrinsic value, F-Score, scenarios, confidence |
| GET | `/price-history/{ticker}` | Historical prices |
| GET | `/financials/{ticker}` | Financial statement series |
| GET | `/metrics/{ticker}` | Derived ratios + analyst data |
| GET | `/fx-rate` | Currency → USD rate |
| GET | `/investors/{ticker}` | Persona-based LLM evaluations |
| GET | `/thesis/{ticker}` | LLM investment thesis |
| GET | `/deep-research/{ticker}` | Aggregated multi-source LLM report |
| GET | `/screener` | Filter the precomputed S&P 500 cache |
| GET | `/insider-trades/{ticker}` | SEC Form 4 activity |
| GET | `/institutional-holdings/{ticker}` | 13F holdings |
| GET | `/search` | Ticker/instrument search |
| POST | `/portfolio-insights` | LLM assessment of a holdings set |
| POST | `/email-report` | Email a generated PDF (optional, off by default) |

## Tech Stack

**Frontend** — Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS v4,
Recharts, Framer Motion, jsPDF + html2canvas-pro, `@supabase/ssr`.

**Backend** — FastAPI, Uvicorn, market-data chain (Financial Modeling Prep as
primary, `yfinance` opportunistic, Finnhub as backup), SEC EDGAR, LLM SDKs (Groq,
Google Generative AI, Cerebras), Resend, `pandas`.

**Data & Auth** — Supabase (Postgres + Auth + Row-Level Security).

## Project Structure

See [docs/FolderStructure.md](docs/FolderStructure.md) for the full annotated tree.
Top level: `src/` (frontend), `backend/` (API), `docs/` (guides), `public/` (assets).

## Developer Experience

### Prerequisites

- Node.js ≥ 18 and npm
- Python 3.9+
- A Supabase project (free tier is fine)
- Provider API keys are optional individually; AI/valuation features degrade
  gracefully without them.

### Setup

```bash
git clone <repo-url> moat && cd moat

# Frontend
npm install

# Backend
pip install -r backend/requirements.txt

# Environment
cp .env.example .env.local            # frontend
cp backend/.env.example backend/.env  # backend
```

Apply the database schema (SQL in
[docs/Configuration.md](docs/Configuration.md#database-schema)) in the Supabase SQL
editor to create `portfolio_holdings` and `portfolio_insights_cache` with RLS.

### Run

```bash
# Terminal 1 — backend
cd backend && uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
npm run dev
```

App: http://localhost:3000 — API docs: http://localhost:8000/docs

### How frontend and backend communicate

- The frontend resolves the backend base URL from `NEXT_PUBLIC_API_BASE_URL`
  (defaults to `http://localhost:8000`). All calls go through `src/lib/api.ts` — no
  hardcoded URLs.
- The backend's CORS allow-list comes from `CORS_ALLOWED_ORIGINS` (defaults to
  `http://localhost:3000`). In production, set both to the deployed URLs.
- The frontend talks to Supabase directly for auth and portfolio reads/writes; RLS
  ensures a user can only see their own rows.

## Configuration

Environment variables (templates committed as `.env.example`; real values never
committed):

| Variable | Side | Purpose |
|----------|------|---------|
| `NEXT_PUBLIC_SUPABASE_URL` | frontend | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | frontend | Supabase anon key (RLS-protected) |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | Backend base URL (default `http://localhost:8000`) |
| `NEXT_PUBLIC_ENABLE_EMAIL` | frontend | Show the "Email PDF" button (default off) |
| `FMP_API_KEY` | backend | Financial Modeling Prep (external DCF) |
| `GROQ_API_KEY` / `GEMINI_API_KEY` / `CEREBRAS_API_KEY` | backend | LLM fallback chain |
| `RESEND_API_KEY` | backend | Email delivery (optional) |
| `CORS_ALLOWED_ORIGINS` | backend | Comma-separated allowed origins |

Details and the database schema: [docs/Configuration.md](docs/Configuration.md).

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Next.js dev server |
| `npm run build` | Production build |
| `npm run start` | Serve the production build |
| `npm run lint` | ESLint |
| `python backend/run_screener.py` | Rebuild the screener cache (batch job) |

## Limitations & Future Work

Stated honestly so the boundaries are clear:

- **No automated test suite.** Playwright is available and has been used for manual
  browser smoke checks of critical flows, but there are no committed unit or
  integration tests, and no CI pipeline.
- **Single-instance assumptions.** The LLM response cache and per-key stampede locks
  are **in-process**. Running multiple backend instances behind a load balancer
  would give each its own cache and locks — a shared store (e.g. Redis) would be
  required to scale horizontally. Not currently implemented.
- **No observability.** There is no structured logging, metrics, or tracing beyond
  stdout. No rate-limiting or auth on the backend API itself (it is a read-mostly
  data API; user data is protected at the Supabase/RLS layer, not the FastAPI layer).
- **Screener is a batch job.** `/screener` serves a precomputed `screener_cache.json`
  built by manually running `run_screener.py`; there is no scheduler.
- **Type/lint debt.** A small set of pre-existing TypeScript strictness warnings
  (mainly Recharts generic mismatches and Supabase callback `any`s) are currently
  not enforced at build time (`next.config.ts` → `ignoreBuildErrors`). Behavior is
  unaffected; resolving them is tracked work.
- **External-API dependence & FMP budget.** With FMP as the primary source, a cold
  `/analyze` costs ~8 FMP calls, so the free 250/day cap covers ~30 unique cold
  tickers/day. The 3-hour valuation cache, the yfinance-first ordering, and the
  Finnhub tier mitigate this, but high unique-ticker traffic can still exhaust FMP's
  daily quota (after which the app degrades to Finnhub/quote-only). The cache is
  in-process, so it shares the single-instance limitation above.
- **FMP-path valuation is more conservative.** FMP's free tier lacks forward
  analyst growth estimates, so on the FMP path the internal DCF falls back to a
  historical FCF CAGR — a supported but more conservative path that can lower the
  DCF leg of the consensus versus the yfinance path. Relative value and the external
  FMP DCF still anchor the estimate.
- **Stock-split distortion on the FMP path.** FMP reports per-period share counts
  as-reported (not split-adjusted), so a recent stock split shows up as a large
  share-count jump. The merger/reorganization guard correctly flags such cases as
  low-confidence rather than emitting a confident wrong number, but the relative-value
  multiple for a recently-split name on the FMP path can be unreliable until adjusted.
- **Finnhub key is optional/pending.** The third tier is built and structurally
  verified; live behavior requires `FINNHUB_API_KEY` to be set.

Planned and tracked improvements are detailed in
[docs/Development.md](docs/Development.md#future-improvements).

## Documentation

- [Architecture](docs/Architecture.md)
- [Folder Structure](docs/FolderStructure.md)
- [Development](docs/Development.md)
- [Configuration](docs/Configuration.md)
- [Contributing](docs/Contributing.md)
