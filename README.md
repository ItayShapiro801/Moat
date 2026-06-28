<div align="center">

# Moat

**Value-investing research for stocks, ETFs, and crypto — intrinsic value, quality scores, and legendary-investor analysis in one place.**

</div>

Moat turns a ticker into a research dossier: a blended intrinsic-value estimate (DCF + multiples), a Piotroski F-Score, simulated takes from six legendary investors, insider and institutional activity, an AI investment thesis and deep-research report, plus a personal portfolio tracker with currency-aware holdings and AI key insights.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Development Workflow](#development-workflow)
- [Scripts](#scripts)
- [Build & Deployment](#build--deployment)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Documentation](#documentation)

---

## Overview

Moat is a two-tier application:

- A **Next.js (App Router) frontend** that renders the research UI and talks to Supabase for auth and the user's portfolio.
- A **FastAPI backend** that does all the heavy lifting — fetching market data, computing valuations, and orchestrating LLM providers for narrative analysis.

The two communicate over a simple REST API. The frontend never holds backend API keys; all third-party provider keys live only on the backend.

## Features

| Area | What it does |
|------|--------------|
| **Analyze** | Intrinsic value (blended internal DCF + external DCF + relative multiples), margin of safety, Piotroski F-Score, confidence rating. Asset-class aware: ETFs/crypto/indices skip company-only metrics. |
| **Investors** | Six legendary investors (Buffett, Munger, Lynch, Burry, Ackman, Graham) each give a scored bull/bear verdict from the same fundamentals. |
| **Thesis & Deep Research** | On-demand AI investment thesis and a full diligence report (business model, moat, risks, scenarios). |
| **Screener** | Filter the S&P 500 by margin of safety and F-Score from a pre-built cache. |
| **Compare** | Side-by-side metric and investor-verdict comparison (individual stocks only). |
| **Portfolio** | Dollar-based holdings with currency conversion (native + USD), live gain/loss, allocation donut, and persisted AI "Key Insights". |
| **Ownership** | Insider trades (SEC Form 4) and legendary-fund 13F holdings. |
| **Reports** | Export a polished PDF research report or email it. |

## Tech Stack

**Frontend** — [Next.js 16](https://nextjs.org) (App Router), [React 19](https://react.dev), [TypeScript](https://www.typescriptlang.org), [Tailwind CSS v4](https://tailwindcss.com), [Recharts](https://recharts.org), [Framer Motion](https://www.framer.com/motion/), [jsPDF](https://github.com/parallax/jsPDF) + [html2canvas-pro](https://github.com/yorickshan/html2canvas-pro), [Supabase JS](https://supabase.com/docs/reference/javascript).

**Backend** — [FastAPI](https://fastapi.tiangolo.com), [Uvicorn](https://www.uvicorn.org), [yfinance](https://github.com/ranaroussi/yfinance), [Financial Modeling Prep](https://financialmodelingprep.com), SEC EDGAR, LLM provider chain ([Groq](https://groq.com) → [Gemini](https://ai.google.dev) → [Cerebras](https://cerebras.ai)), [Resend](https://resend.com).

**Data & Auth** — [Supabase](https://supabase.com) (Postgres + Auth + Row-Level Security).

## Architecture

```
┌─────────────────┐      REST        ┌──────────────────┐
│  Next.js (web)  │ ───────────────▶ │  FastAPI (api)   │
│  App Router     │  localhost:8000  │  routers/        │
│  React 19       │ ◀─────────────── │  services/       │
└────────┬────────┘                  └────────┬─────────┘
         │ auth + portfolio rows              │ market data + LLMs
         ▼                                    ▼
   ┌───────────┐               ┌───────────────────────────────┐
   │ Supabase  │               │ yfinance · FMP · SEC EDGAR ·   │
   │ (Postgres │               │ Groq · Gemini · Cerebras ·     │
   │  + Auth)  │               │ Resend                         │
   └───────────┘               └───────────────────────────────┘
```

The backend is organized as thin **routers** (HTTP layer) over reusable **services** (valuation engine, LLM providers). See [docs/Architecture.md](docs/Architecture.md) for details.

## Project Structure

```
moat/
├── src/                      # Next.js frontend
│   ├── app/                  # App Router routes (each folder = a URL)
│   ├── components/           # Feature components + ui/ primitives
│   └── lib/                  # Cross-cutting: api config, auth, Supabase client
├── backend/                  # FastAPI service
│   ├── routers/              # HTTP endpoints (one module per domain)
│   ├── services/             # Valuation engine + LLM providers
│   ├── config.py             # Env + constants
│   ├── models.py             # Pydantic request models
│   └── utils.py              # Shared helpers
├── docs/                     # Architecture, development, configuration guides
└── public/                   # Static assets
```

Full annotated tree: [docs/FolderStructure.md](docs/FolderStructure.md).

## Getting Started

### Prerequisites

- **Node.js** >= 18 and npm
- **Python** 3.9+
- A **Supabase** project (free tier is fine)
- API keys for the backend providers (see [Configuration](#configuration)) — optional, but AI/valuation features degrade without them

### 1. Clone & install

```bash
git clone <repo-url> moat
cd moat

# Frontend deps
npm install

# Backend deps
pip install -r backend/requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env.local           # frontend (Supabase + API base URL)
cp backend/.env.example backend/.env # backend (provider keys)
```

Fill in the values — see [Configuration](#configuration).

### 3. Set up the database

Run the SQL in [docs/Configuration.md](docs/Configuration.md#database-schema) in the Supabase SQL editor to create the `portfolio_holdings` and `portfolio_insights_cache` tables with Row-Level Security.

### 4. Run both servers

```bash
# Terminal 1 — backend
cd backend && uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
npm run dev
```

Open http://localhost:3000. The backend API docs are at http://localhost:8000/docs.

## Configuration

All configuration is via environment variables. Templates are committed as `.env.example`; real values are never committed.

| Variable | Side | Purpose |
|----------|------|---------|
| `NEXT_PUBLIC_SUPABASE_URL` | frontend | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | frontend | Supabase anon key (browser-safe, RLS-protected) |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | Backend base URL (defaults to `http://localhost:8000`) |
| `FMP_API_KEY` | backend | Financial Modeling Prep (external DCF) |
| `GROQ_API_KEY` / `GEMINI_API_KEY` / `CEREBRAS_API_KEY` | backend | LLM provider fallback chain |
| `RESEND_API_KEY` | backend | Transactional email for PDF reports |

See [docs/Configuration.md](docs/Configuration.md) for the database schema and per-variable detail.

## Development Workflow

- Frontend dev server with hot reload: `npm run dev`
- Backend with auto-reload: `uvicorn main:app --reload --port 8000`
- Type-check: `npx tsc --noEmit`
- Lint: `npm run lint`
- Rebuild the screener cache (manual, occasional): `python backend/run_screener.py`

More in [docs/Development.md](docs/Development.md).

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start the Next.js dev server |
| `npm run build` | Production build |
| `npm run start` | Serve the production build |
| `npm run lint` | Run ESLint |

## Build & Deployment

```bash
npm run build   # frontend production build
npm run start   # serve it
```

The frontend deploys cleanly to Vercel. The backend is a standard ASGI app — run it with `uvicorn main:app` behind any ASGI host. Set `NEXT_PUBLIC_API_BASE_URL` to the deployed backend URL. See [docs/Development.md](docs/Development.md).

## Testing

The repo includes [Playwright](https://playwright.dev) for browser-driven smoke checks of critical flows (analyze pages, compare, portfolio). There is no formal unit-test suite yet — see [Future Improvements](docs/Development.md#future-improvements).

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Frontend calls fail / CORS errors | Backend not running on `:8000`, or `NEXT_PUBLIC_API_BASE_URL` misconfigured |
| Portfolio is empty after login | Database migrations not applied — see [Configuration](#configuration) |
| AI features return errors | Missing/invalid provider keys, or free-tier rate limits (the chain falls back across providers) |
| Screener shows "cache not built" | Run `python backend/run_screener.py` |

## Contributing

See [docs/Contributing.md](docs/Contributing.md) for conventions, branch/commit style, and the PR checklist.

## Documentation

- [Architecture](docs/Architecture.md)
- [Folder Structure](docs/FolderStructure.md)
- [Development](docs/Development.md)
- [Configuration](docs/Configuration.md)
- [Contributing](docs/Contributing.md)
