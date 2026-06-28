# Folder Structure

Annotated layout. Generated/ignored paths (`node_modules/`, `.next/`,
`__pycache__/`, `.env*`, logs, `screener_cache.json`) are omitted.

```
moat/
├── .editorconfig             # Cross-editor formatting defaults
├── .env.example              # Frontend env template (copy to .env.local)
├── .gitattributes            # Enforces LF line endings, marks binaries
├── .gitignore                # Ignores secrets, builds, caches, logs
├── .prettierrc.json          # Prettier config
├── .prettierignore
├── AGENTS.md                 # AI-agent project rules (Next.js version notes)
├── CLAUDE.md                 # Includes AGENTS.md
├── README.md                 # Project overview & getting started
├── eslint.config.mjs         # ESLint (eslint-config-next)
├── next.config.ts            # Next.js config
├── next-env.d.ts             # Next.js TS shims (generated)
├── package.json              # Frontend deps & scripts
├── postcss.config.mjs        # PostCSS / Tailwind v4
├── tsconfig.json             # TS config (path alias @/* -> src/*)
│
├── docs/                     # Project documentation
│   ├── Architecture.md
│   ├── Configuration.md
│   ├── Contributing.md
│   ├── Development.md
│   └── FolderStructure.md
│
├── public/                   # Static assets served as-is
│   └── Investors/            # Investor portrait images
│
├── src/                      # Next.js frontend
│   ├── app/                  # App Router — each folder is a route (URL)
│   │   ├── layout.tsx        # Root layout
│   │   ├── page.tsx          # Home (/)
│   │   ├── globals.css       # Global styles / Tailwind layers
│   │   ├── analyze/[ticker]/page.tsx   # /analyze/:ticker
│   │   ├── compare/page.tsx            # /compare
│   │   ├── portfolio/page.tsx          # /portfolio
│   │   ├── screener/page.tsx           # /screener
│   │   ├── reset-password/page.tsx     # /reset-password
│   │   └── style-guide/page.tsx        # /style-guide (dev component reference)
│   │
│   ├── components/           # Feature components
│   │   ├── CompanyThesis.tsx
│   │   ├── DeepResearchReport.tsx
│   │   ├── ExportReport.tsx
│   │   ├── FinancialTrends.tsx
│   │   ├── InsiderTrades.tsx
│   │   ├── InstitutionalHoldings.tsx
│   │   ├── InvestorCards.tsx
│   │   ├── KeyMetrics.tsx
│   │   ├── NavBar.tsx
│   │   ├── PEChart.tsx
│   │   ├── PortfolioButton.tsx
│   │   ├── PortfolioInsights.tsx
│   │   ├── PriceChart.tsx
│   │   ├── UsernamePrompt.tsx
│   │   └── ui/               # Reusable presentational primitives
│   │       ├── Badge.tsx
│   │       ├── Button.tsx
│   │       ├── Card.tsx
│   │       ├── Gauge.tsx
│   │       ├── StatBlock.tsx
│   │       └── TickerSearch.tsx
│   │
│   └── lib/                  # Cross-cutting, non-UI modules
│       ├── api.ts            # Backend base URL + apiUrl() helper
│       ├── auth-context.tsx  # Supabase auth provider/context
│       └── supabase/
│           └── client.ts     # Browser Supabase client factory
│
└── backend/                  # FastAPI service
    ├── main.py               # App + CORS + router registration
    ├── config.py             # Env vars + constants
    ├── models.py             # Pydantic request models
    ├── utils.py              # Shared pure helpers
    ├── requirements.txt      # Python deps
    ├── run_screener.py       # Manual script: rebuild screener cache
    ├── .env.example          # Backend env template (copy to backend/.env)
    ├── routers/              # HTTP endpoints (one module per domain)
    │   ├── analyze.py
    │   ├── investors.py
    │   ├── ownership.py
    │   ├── portfolio.py
    │   ├── reports.py
    │   ├── screener.py
    │   ├── search.py
    │   └── thesis.py
    └── services/             # Domain logic (valuation engine, LLMs)
        ├── blend.py
        ├── dcf.py
        ├── llm_providers.py
        ├── piotroski.py
        └── relative_value.py
```

## Conventions

- **Routes live only under `src/app/`** — the folder path *is* the URL. Do not move
  route files to "tidy up"; it changes the public URL.
- **Reusable, presentation-only widgets go in `src/components/ui/`.** Feature
  components (which fetch data or encode domain logic) live one level up in
  `src/components/`.
- **Non-UI shared logic goes in `src/lib/`** — API config, auth, third-party clients.
- **Backend: routers are thin, services are reusable.** New HTTP endpoints go in
  `routers/`; new computation goes in `services/` and is imported by routers.
- **Imports use the `@/` alias** (`@/components/...`, `@/lib/...`) instead of deep
  relative paths.
