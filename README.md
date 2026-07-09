# Moat

Moat is an equity-research web application. Given a ticker, its **Moat Valuation
Engine** computes a quality-weighted intrinsic-value estimate — a moat score that
sets the DCF's growth horizon, a reverse DCF quantifying market-implied growth, a
Monte Carlo fair-value distribution, and a diagnostic-weighted ensemble across
independent valuation methods — alongside a Piotroski F-Score and a set of derived
metrics, then layers on LLM-generated narrative analysis and a personal portfolio
tracker.

It is a two-tier system: a **Next.js (App Router) frontend** and a **FastAPI
backend**, with **Supabase** (Postgres + Auth, and a persistent cache) for user
data and cross-restart state. The backend owns all computation and third-party
integrations; the frontend is a typed client that renders results and manages
auth/portfolio state.

> **Scope note.** This README describes only what is implemented in the codebase.
> Where a subsystem is intentionally simple (e.g. the screener runs as a batch job),
> that is stated rather than dressed up. See
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
single intrinsic-value estimate with a confidence rating. An **AI reviewer** then
gives a second opinion on that estimate. Narrative features (thesis, investor-persona
evaluations, deep-research report, valuation review, portfolio insights)
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
   │                     ├─ yfinance → Financial Modeling Prep → SEC EDGAR + Finnhub
   │                     │     market-data resolver — an uncapped free floor
   │                     │     (EDGAR + Finnhub) under a capped primary (FMP)
   │                     ├─ BusinessQuant                       analyst forward-growth estimates
   │                     ├─ SEC EDGAR                            Form 4 insiders, 13F holdings
   │                     ├─ Supabase (service role)              persistent valuation + growth cache
   │                     │                                       (survives Render free-tier restarts)
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
    valuation_engine.py  Moat Score, CAP-fading DCF, reverse DCF, Monte Carlo,
                          excess-return model (financials), ensemble weighting
    dcf.py             legacy internal DCF (still derives the growth input +
                        capex-normalized base FCF) + external DCF (FMP)
    relative_value.py  multiples valuation + merger/reorg detection
    blend.py           cyclicality/mismatch flags + confidence rating
    piotroski.py       F-Score (9 signals)
    edgar_fundamentals.py  SEC EDGAR statements → yfinance-shaped adapter (free, uncapped)
    fmp_fallback.py    FMP client, multi-key rotation, stale-quote cross-validation
    finnhub_fallback.py  Finnhub client — uncapped price/quote backup
    businessquant.py   BusinessQuant client — analyst forward-growth, multi-key rotation
    supabase_cache.py  persistent cache (valuations, growth) across backend restarts
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

### 1. Moat Valuation Engine (`backend/services/valuation_engine.py`, composed in `routers/analyze.py`)

`analyze(ticker)` orchestrates up to four independent valuation inputs and combines
them with **diagnostic, reliability-based weights** — not a blind average.

- **Moat Score (0–100)** — five measurable components, each 0–1 and averaged (a
  missing component drops out and the rest renormalize, so partial data still
  scores fairly): return on equity (25%+ ROE = full marks), median FCF margin
  (20%+ = full marks), revenue-growth consistency (share of up-years over the
  trailing history), gross-margin stability (`1 − stdev(margins)/8%`, clamped), and
  the Piotroski F-Score (`/9`). This is the moat, quantified from filings rather
  than asserted.
- **Competitive-advantage-period (CAP) DCF (`valuation_engine.py`)** — the moat
  score sets **how long** the DCF's explicit growth window runs (`5 + 7 * moat/100`
  years — 5y for no moat, up to 12y for a fortress) and **how slowly growth fades**
  toward the terminal rate (a geometric decay factor from 0.78 to 0.93 per year,
  moat-scaled). This is deliberate: a flat 5-year projection undervalues durable
  compounders and overvalues cyclicals that don't deserve a decade of assumed
  growth. Growth is capped at 25% to start (uncapped compounding at a hot forward
  estimate for years running was producing 2–3× overvaluations). The discount rate
  is CAPM-style WACC (`0.045 + beta * 0.05`, floored at 7.5% so low-beta names don't
  get an unrealistically cheap terminal multiple, capped at 13%).
- **Reverse DCF** — the same model solved backwards by bisection: *what growth rate
  does the current market price imply?* The gap between that implied rate and the
  analyst-estimate growth actually used is a quantified read on whether the market
  is under- or over-pricing the story (surfaced as `implied_growth` /
  `expected_growth` in the payload and on the analyze page).
- **Monte Carlo fair-value distribution** — ~1,000 simulated paths perturbing
  growth, WACC, terminal growth, and base FCF around their point estimates produce
  a fair-value distribution; the **bear/base/bull** scenarios shown on the analyze
  page are its P25/P50/P75, not three hand-picked parameter sets. Hidden when the
  DCF's own base case lands outside a sane band vs. the market price (thin/volatile
  FCF — see below), so the UI never shows false precision on a name where cash flow
  is the wrong lens.
- **Excess-return model for balance-sheet financials** — banks, insurers, and
  investment banks (`industry` contains "bank" / "insur" / "capital market") get
  `P/B* = (ROE − g) / (Ke − g)` applied to book value per share instead of a DCF
  (deposits/float make "free cash flow" noise for these businesses). Payment
  networks and other Financial-Services-sector names that *aren't* balance-sheet
  businesses (Visa, Mastercard) keep the normal DCF — the exclusion is
  industry-aware, not a blanket sector rule.
- **Earnings-multiple anchor** — `fair P/E × trailing EPS`, with the fair P/E
  derived from growth and capped at both a sector ceiling and 1.25× the company's
  *current* P/E (so a growth story argues for a premium, not an unmoored multiple).
  Rescues hyper-capex names (AMZN, TSLA-style) whose reported FCF is too thin for a
  DCF to be meaningful, and is the fallback anchor for financials.
- **External DCF (`dcf.py`)** — an independent per-share DCF pulled from Financial
  Modeling Prep, used as a cross-check. Skipped gracefully if no API key or budget.
- **Relative value (`relative_value.py`)** — multiples-based valuation computed from
  the company's **own 5-year history**: median P/E, P/S, EV/EBITDA, P/FCF, and P/B
  multiples are derived from past years, then applied to current per-share metrics.
- **Diagnostic ensemble weighting (`ensemble_weights`)** — rather than averaging
  whatever sources are available, each source's weight reflects a measurable
  reliability signal: the internal model's weight scales with how many of the last
  5 years had positive FCF and whether its growth input was a real forward estimate
  (dropped to a token 5% weight when the engine itself flags the DCF unreliable —
  the case that used to let a $52 DCF drag a $975 stock's consensus down 80%); the
  earnings anchor's weight scales with whether trailing EPS is even positive;
  relative value and the external DCF carry fixed moderate weights. Weights always
  renormalize to sum to 1 over whatever sources are actually present.
- **Confidence isn't just a label** — it's capped at "medium" when the consensus
  diverges from the market price by more than ~35% (a bold call can still be
  *right*, but it doesn't get a green "high confidence" badge), and forced to "low"
  when the DCF is the sole source and structurally unreliable for the company. The
  screener surfaces this per-row so sorting by margin-of-safety doesn't just float
  the model's least-certain calls to the top unlabeled.

The same module computes the **Piotroski F-Score** (`piotroski.py`): the standard
9 binary signals across profitability (ROA, ROA trend, operating cash flow,
accruals), leverage/liquidity (long-term debt ratio, current ratio, share count),
and efficiency (gross margin, asset turnover), returning an integer 0–9.

**AI valuation reviewer** (`/valuation-review`, in `routers/thesis.py`) — a *second
opinion* on the quantitative model. The deterministic engine remains the auditable
anchor; an LLM then judges its output against the fundamentals and, when it
disagrees, supplies its own grounded intrinsic-value estimate and range. It reuses
the already-cached `analyze` + `metrics` data (no extra upstream cost) and is
prompted to reason **only** from the provided numbers, never fabricate figures, and
prefer a low-confidence/wide range over false precision — it reviews and judges, it
does not do the DCF math. Returns a structured verdict (`reasonable` / `too_high` /
`too_low` / `unreliable`), an agree/disagree flag, its own fair value + low/high
range, a confidence level, and a short rationale.

### 2. Data aggregation layer (`backend/routers/`)

- **Mixed free-source data strategy — each provider for what it's best at.** The
  root constraint: the best single source (`yfinance`) is IP-blocked from cloud
  hosts, and every affordable API is daily-capped. So instead of one "primary,"
  the engine assembles a valuation from the source that's strongest (and cheapest)
  for each *piece*, with an always-on uncapped floor so the site never fully breaks.
  The resolver (`routers/analyze.py` → `_resolve_market_data`) yields a
  yfinance-shaped adapter so the **entire** DCF / F-Score / relative-value /
  merger-guard pipeline runs unchanged on whatever data was assembled.

  | Piece | Source | Cap | Role |
  |-------|--------|-----|------|
  | Financial statements | **SEC EDGAR** (`services/edgar_fundamentals.py`) | none | Uncapped floor — straight from filings, never IP-blocked |
  | Price / multiples / sector | **Finnhub** | 60/min | Uncapped floor for market data |
  | Forward analyst estimates | **BusinessQuant** (`services/businessquant.py`) | 30/day per key | The one input the others lack; free-account keys rotated |
  | Full-bundle convenience + external DCF | **Financial Modeling Prep** | 750/day (3 keys) | Nice-to-have while budget lasts |
  | Opportunistic fresh data | **yfinance** | blocked on cloud | Used first *only* when the host IP isn't blocked |

  Resolver order is `yfinance → FMP → (EDGAR + Finnhub)`. FMP is tried while it has
  budget (one call bundles everything); once its keys are spent the request falls to
  the **EDGAR + Finnhub** combo, which is uncapped and always available — so a stock
  is *always* valuable, at any time, for free.
- **Source-independent forward growth** (`enrich_growth`). A DCF's growth rate is its
  most important input, and only *forward* analyst estimates get it right. yfinance
  supplies them; FMP's free tier does not. So growth is backfilled for any source
  that lacks it, best-to-good: **(1)** the source's own estimate (yfinance) →
  **(2)** BusinessQuant's real next-year analyst consensus (free, 30/day per key,
  rotated across accounts — 7 keys ≈ 100 stocks/day) → **(3)** a Finnhub
  historical-growth **proxy** (uncapped). The proxy guarantees growth is always
  populated; without it the DCF collapses to a weak historical FCF CAGR that badly
  undervalues growth names (AAPL → ~$124). For a reinvesting growth company whose
  forward *earnings* growth is transiently depressed while *revenue* compounds
  (e.g. TTD: +4% earnings, +19% revenue), the two are blended so the DCF isn't
  anchored to the low figure. An earnings-multiple anchor
  (`compute_earnings_multiple_value`) additionally rescues hyper-capex names
  (AMZN/TSLA) whose free cash flow is too thin for a DCF.
- **Real analyst consensus** (`analyst_recommendation`, Finnhub). The analyst card
  shows Wall Street's actual Buy/Hold/Sell breakdown and a derived consensus rating
  (e.g. AAPL *Buy*, 54 analysts), not a fabricated price target — free-tier price
  targets are premium-gated, so we report only what is genuinely available.
- **Foreign-filer currency normalization** (`_normalize_statement_currency`). An ADR
  that trades in USD but reports in another currency (NVO: statements in DKK) would
  otherwise feed DKK cash flows into a USD-priced DCF and print a ~$2,268 value on a
  $49 stock. When `financialCurrency != currency`, statement DataFrames and the
  statement-derived info fields are converted to the trading currency via a live FX
  rate; share counts and the USD market cap are left untouched.
- **Share-count scale guard.** Some filers report weighted-average shares in a scaled
  unit (MCD's XBRL gives "716.4" meaning 716.4 M). Read literally this exploded every
  per-share figure ~1e6× (MCD intrinsic value showed *$291 million*); when the market
  provider's share count differs by >100×, the mis-scaled XBRL figure is discarded.
- **Honest "data limit reached" banner.** Reserved for the genuinely degraded
  quote-only fallback. The EDGAR path is now a *complete* valuation (SEC statements +
  BusinessQuant estimates + Finnhub price/ratios), so it is **not** flagged — on the
  free tier FMP is usually capped, and flagging every normal EDGAR result cried wolf.
  When a valuation IS degraded, the payload carries `data_limited` and the UI shows a
  clear notice that the estimate is from SEC filings and may be less precise — a
  backup number is never dressed up as the primary feed.
- **Full-valuation cache + stale-serve** (`routers/analyze.py`). A successful full
  valuation is cached per ticker for **24 h** regardless of source (statements barely
  move intraday and are what drains the capped budgets); a cache **hit re-fetches
  only the live price** so the headline quote is never stale. When every provider is
  exhausted, analyze **serves the last good full valuation (any age)** rather than
  degrading — a day-old full analysis beats "limited data mode". Only a ticker that
  has *never* been fetched falls back to a quote-only response.
- **Supporting-data cache + stampede lock** (`/metrics`, `/financials`,
  `/price-history`). One page load fetches these from several components at once, so
  they carry a 15-minute cache and a per-key lock that collapses a burst of identical
  concurrent requests into a single upstream call — cutting redundant yfinance/FMP
  traffic and speeding repeat loads to ~0.2s.
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

- Holdings are stored per user in Supabase `portfolio_holdings` (RLS-enforced with
  SELECT/INSERT/**UPDATE**/DELETE policies keyed on `auth.uid() = user_id`), with
  amount invested, shares, purchase price, currency, and asset type. The UPDATE
  policy is required so an upsert that resolves to an update (adding to an existing
  holding) isn't silently blocked.
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
- **Response cache + stale-serve.** Generated narrative responses (thesis,
  investors, deep-research, valuation-review) are cached in-memory for **2 days**.
  If a fresh generation fails (all LLM providers rate-limited, or the underlying
  market data is unavailable), the cache **serves the last good result** rather than
  an empty "unavailable" payload.
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
- **Domain-aware adjustments** (`blend.py`, `valuation_engine.py`):
  - *Industry-aware financial-model routing* — balance-sheet financials (banks,
    insurers, investment banks — detected from `industry`, not the broader
    "Financial Services" sector) get the excess-return model instead of a DCF;
    payment networks in the same sector (Visa, Mastercard) correctly keep their DCF.
  - *Dynamic cyclicality detection* — independent of the sector label, a company is
    treated as cyclical when its 5-year FCF coefficient of variation exceeds a
    threshold (or shows a negative year amid positive years).
  - *Merger / reorganization guard* (`relative_value.py` → `detect_reorganization`)
    — detects large share-count jumps that would distort per-share history and flags
    the result accordingly.
  - *Stale-quote guard* (`fmp_fallback.py` → `_cross_validate_price`) — every FMP
    price is cross-checked against Finnhub's live quote; a >25% divergence (an
    observed real case: a free-tier quote stale by months after a large run-up)
    triggers the fresher timestamp winning, with all price-linear ratios (P/E, P/B,
    P/FCF, market cap) rescaled to match rather than silently poisoning downstream
    numbers.
- **Provider fallback chain** — graceful degradation across three LLM providers;
  the system keeps working with any subset of keys configured.
- **Market-data fallback with rate-limit detection** — when the primary source
  (`yfinance`) throttles, the data endpoints transparently fail over to FMP and
  reshape responses to match, with per-source logging (see Data aggregation layer).
- **Two-tier caching (in-memory + persistent Supabase), stale-serve, stampede
  locks & warmup** — to survive a free-tier backend (Render sleeps after ~15 min
  idle, which wipes any in-process-only cache; its cloud IP is also blocked by
  Yahoo/SEC). Full valuations cache 24h **and persist to Supabase**
  (`services/supabase_cache.py`, service-role key, fails open to in-memory-only if
  unset) so a computed valuation survives a cold start instead of being recomputed
  from scratch on every wake; a cache **hit still re-fetches only the live price**
  (`_with_fresh_price`) so the headline quote is never a day stale. Valuations
  built from the EDGAR/Finnhub backup, or with the Finnhub growth *proxy* instead
  of a real analyst estimate, get a short **3-hour** TTL instead of 24h — so a
  ticker doesn't stay "stuck" showing degraded data long after the primary
  provider recovers. Narrative endpoints cache 2 days, supporting data 15 min, the
  FMP degraded path 60s; per-key locks collapse concurrent duplicate requests into
  one upstream call; when providers are exhausted the caches **serve the last good
  result** instead of degrading; and a background thread **pre-warms** popular
  tickers 30s after startup so the first real user isn't the one paying the
  cold-start cost.
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
| GET | `/valuation-review/{ticker}` | AI second opinion on the model's valuation |
| GET | `/price-history/{ticker}` | Historical prices |
| GET | `/financials/{ticker}` | Financial statement series |
| GET | `/metrics/{ticker}` | Derived ratios + analyst data |
| GET | `/fx-rate` | Currency → USD rate |
| GET | `/investors/{ticker}` | Persona-based LLM evaluations |
| GET | `/thesis/{ticker}` | LLM investment thesis |
| GET | `/deep-research/{ticker}` | Aggregated multi-source LLM report |
| GET | `/screener` | Filter the precomputed S&P 500 cache |
| GET | `/insider-trades/{ticker}` | SEC Form 4 activity |
| GET | `/institutional-holdings/{ticker}` | 13F holdings (stale-serves when EDGAR is down) |
| GET | `/search` | Ticker/instrument search |
| POST | `/portfolio-insights` | LLM assessment of a holdings set |
| POST | `/email-report` | Email a generated PDF (optional, off by default) |
| GET | `/warmup` | Cache warmup (also auto-runs 30s post-startup) |
| GET | `/health` | Liveness + loaded provider-key counts (FMP / BusinessQuant / Finnhub) |

## Tech Stack

**Frontend** — Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS v4,
Recharts, Framer Motion, jsPDF + html2canvas-pro, `@supabase/ssr`.

**Backend** — FastAPI, Uvicorn, market-data resolver chain (`yfinance` opportunistic
→ Financial Modeling Prep primary → SEC EDGAR + Finnhub uncapped backup),
BusinessQuant (analyst forward-growth estimates), LLM SDKs (Groq, Google
Generative AI, Cerebras), Resend, `pandas`.

**Data, Auth & Cache** — Supabase (Postgres + Auth + Row-Level Security; also used
as a persistent cache for valuations and growth estimates so they survive
free-tier backend restarts).

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
| `FMP_API_KEY` (+ optional `_2`, `_3`) | backend | Financial Modeling Prep — primary market data + external DCF; multiple keys rotate to raise the effective daily cap |
| `FINNHUB_API_KEY` | backend | Live-quote cross-validation + the uncapped price/multiples backup |
| `BUSINESSQUANT_API_KEY` (+ optional `_2`..`_7`) | backend | Analyst forward-growth estimates for the DCF; multiple keys rotate (30 calls/key/day) |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | backend | Persistent valuation/growth cache (service-role key; optional — falls back to in-memory-only if unset) |
| `GROQ_API_KEY` / `GEMINI_API_KEY` / `CEREBRAS_API_KEY` | backend | LLM fallback chain |
| `RESEND_API_KEY` | backend | Email delivery (optional) |
| `CORS_ALLOWED_ORIGINS` | backend | Comma-separated allowed origins |

Every provider key is individually optional — the app degrades gracefully as keys
are omitted (down to the uncapped EDGAR + Finnhub floor with no keys at all beyond
the required `SUPABASE_*` frontend pair). `GET /health` on the backend reports how
many keys were actually loaded for each rotated provider, useful for confirming a
deploy picked up the full key set.

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
- **External-API dependence, mitigated by an uncapped floor.** FMP is the primary
  data source (fastest, most complete) but is daily-capped even with multiple
  rotated keys; BusinessQuant's analyst-estimate tier is capped harder still
  (30 calls/key/day). Both degrade to the SEC EDGAR + Finnhub combo, which is
  **free and uncapped**, so the app never goes fully dark — a ticker served from
  the backup is explicitly flagged (`data_limited`) rather than presented as
  primary-quality, and typically recovers within the hour as FMP's key cooldowns
  expire (not "the next day" — a common misreading the in-app copy used to invite).
  The persistent Supabase cache (above) means a previously-computed ticker also
  doesn't re-spend budget on every cold start.
- **Stock-split distortion on the FMP path.** FMP reports per-period share counts
  as-reported (not split-adjusted), so a recent stock split shows up as a large
  share-count jump. The merger/reorganization guard correctly flags such cases as
  low-confidence rather than emitting a confident wrong number, but the relative-value
  multiple for a recently-split name on the FMP path can be unreliable until adjusted.
- **Screener freshness window.** `/screener` serves a batch snapshot valid for 31
  days; past that it still serves (stale beats blank) but is flagged `stale: true`
  with a visible banner rather than silently aging forever.
- **PDF text wrapping has a safety margin, not a guarantee.** `jsPDF`'s
  `splitTextToSize` only breaks on spaces; a single very long unbroken run in an
  LLM-generated sentence (rare, but possible) could still overflow a column despite
  the wrap-slack margin added around each text block.

Planned and tracked improvements are detailed in
[docs/Development.md](docs/Development.md#future-improvements).

## Documentation

- [Architecture](docs/Architecture.md)
- [Folder Structure](docs/FolderStructure.md)
- [Development](docs/Development.md)
- [Configuration](docs/Configuration.md)
- [Contributing](docs/Contributing.md)
