# Configuration

All configuration is environment-based. Templates are committed as `.env.example`
files; real secrets are git-ignored and must never be committed.

## Environment variables

### Frontend (`.env.local`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `NEXT_PUBLIC_SUPABASE_URL` | yes | Supabase project URL, e.g. `https://xxxx.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | yes | Supabase anon/publishable key. Browser-safe; access is enforced by RLS. |
| `NEXT_PUBLIC_API_BASE_URL` | no | Backend base URL. Defaults to `http://localhost:8000` when unset. |

> Only `NEXT_PUBLIC_*` variables are exposed to the browser. Never put a secret
> behind that prefix.

### Backend (`backend/.env`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `FMP_API_KEY` | recommended | Financial Modeling Prep — primary market data + external DCF |
| `FMP_API_KEY_2`, `FMP_API_KEY_3` | optional | Extra FMP keys rotated to raise the effective daily cap |
| `FINNHUB_API_KEY` | recommended | Live-quote cross-validation + the uncapped price/multiples backup |
| `BUSINESSQUANT_API_KEY` | optional | Analyst forward-growth estimates for the DCF |
| `BUSINESSQUANT_API_KEY_2` .. `_7` | optional | Extra BusinessQuant keys rotated (30 calls/key/day each) |
| `SUPABASE_URL` | optional | Same project URL as the frontend — enables the persistent cache below |
| `SUPABASE_SERVICE_KEY` | optional | Service-role key (bypasses RLS; backend-only, never expose to the browser) |
| `GROQ_API_KEY` | recommended | Primary LLM provider |
| `GEMINI_API_KEY` | recommended | Second LLM provider (fallback) |
| `CEREBRAS_API_KEY` | optional | Third LLM provider (fallback) |
| `RESEND_API_KEY` | optional | Email delivery of PDF reports |

The LLM chain tries Groq → Gemini → Cerebras, so the app still functions with a
subset of keys; AI features simply have fewer fallbacks. Market data works with
**no keys at all** via the free, uncapped SEC EDGAR + Finnhub path, though FMP and
BusinessQuant materially improve accuracy and freshness while their budgets last.
`GET /health` on a running backend reports how many keys were actually loaded for
each rotated provider.

## Database schema

Run the following in the Supabase SQL editor.

### `portfolio_holdings`

Stores each user's positions. `amount_invested` and `price_at_purchase` are in USD;
`currency`/`quote_type` capture the native asset class for display and conversion.

```sql
create table if not exists portfolio_holdings (
  user_id uuid references auth.users not null,
  ticker text not null,
  amount_invested numeric not null default 0,
  price_at_purchase numeric,
  shares numeric,
  currency text default 'USD',
  quote_type text default 'EQUITY',
  added_at timestamptz default now(),
  primary key (user_id, ticker)
);

alter table portfolio_holdings enable row level security;
create policy "Users manage own holdings"
  on portfolio_holdings for all
  using (auth.uid() = user_id);
```

### `portfolio_insights_cache`

Persists the AI portfolio "Key Insights" so they stay stable across visits and only
regenerate when holdings change (tracked via `holdings_hash`).

```sql
create table if not exists portfolio_insights_cache (
  user_id uuid references auth.users primary key,
  holdings_hash text not null,
  insights jsonb not null,
  generated_at timestamptz default now()
);

alter table portfolio_insights_cache enable row level security;
create policy "Users manage own insights cache"
  on portfolio_insights_cache for all
  using (auth.uid() = user_id);
```

### `analysis_cache` (optional — persistent backend cache)

Only needed if `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` are set. Lets computed
valuations and BusinessQuant growth estimates survive a Render free-tier restart
instead of being recomputed (and re-spending capped-provider budget) on every cold
start. The backend accesses it with the **service-role key**, so RLS is enabled
with no permissive policy — only the backend can read/write it, never the browser.

```sql
create table if not exists analysis_cache (
  key text primary key,
  value jsonb not null,
  expires_at timestamptz not null
);

alter table analysis_cache enable row level security;
-- No policy is added: only the service-role key (used exclusively by the
-- backend) can bypass RLS and access this table. The browser's anon key
-- cannot read or write it.
```

> **Verifying the schema:** a `select` of the new columns/tables via the anon key
> returns `[]` (empty, HTTP 200) when the schema is correct and RLS is active. A
> `42703` ("column does not exist") or `PGRST205` ("table not found") error means a
> migration hasn't been applied.

## Tooling configuration

| File | Purpose |
|------|---------|
| `.editorconfig` | Charset, line endings, indentation across editors |
| `.gitattributes` | Forces LF line endings; marks binary assets |
| `.prettierrc.json` / `.prettierignore` | Code formatting |
| `eslint.config.mjs` | Linting (`eslint-config-next`) |
| `tsconfig.json` | TypeScript; `@/*` path alias → `src/*` |
| `next.config.ts` | Next.js build config |
| `postcss.config.mjs` | Tailwind CSS v4 pipeline |
