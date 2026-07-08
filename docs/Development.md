# Development

## Running locally

Two processes — run them in separate terminals.

```bash
# Backend (FastAPI, auto-reload)
cd backend
uvicorn main:app --reload --port 8000

# Frontend (Next.js, hot reload)
npm run dev
```

- App: http://localhost:3000
- API docs (Swagger): http://localhost:8000/docs

## Everyday commands

| Task | Command |
|------|---------|
| Frontend dev server | `npm run dev` |
| Type-check | `npx tsc --noEmit` |
| Lint | `npm run lint` |
| Production build | `npm run build` |
| Serve production build | `npm run start` |
| Rebuild screener cache | `python backend/run_screener.py` |

## The screener cache

`/screener` reads `backend/screener_cache.json`, which is produced by
`python backend/run_screener.py` (a manual, occasional job that fetches the S&P 500
and computes lightweight valuations). Unlike most generated data, **this file is
committed** (see the comment in `.gitignore`): Render's free tier has ephemeral
storage and doesn't run `run_screener.py`, so the prebuilt cache has to ship with
the app for the screener to have real data in production. To refresh it: re-run
the script locally, then commit the updated file. `/screener` treats a snapshot
older than 31 days as stale (still served, but flagged) — see
`backend/routers/screener.py`.

## Conventions

- **TypeScript**: 2-space indent, double quotes, semicolons (see `.prettierrc.json`).
- **Python**: 4-space indent, standard FastAPI/Pydantic idioms.
- **Imports**: use the `@/` alias on the frontend (`@/components/...`, `@/lib/...`)
  rather than long relative paths.
- **Backend calls**: never hardcode the backend URL; import `API_BASE_URL` (or
  `apiUrl`) from `@/lib/api`.
- **Secrets**: only in `.env.local` / `backend/.env` (both git-ignored). Add new
  variables to the matching `.env.example` template.

## Deployment notes

- **Frontend** deploys to Vercel out of the box (`npm run build`). Set
  `NEXT_PUBLIC_API_BASE_URL` to the deployed backend URL and the two Supabase vars.
- **Backend** is a standard ASGI app: `uvicorn main:app` behind any ASGI-capable
  host (Render, Fly, a container, etc.). Provide the provider keys via the host's
  secret manager. Update the CORS allow-list in `backend/main.py` to include the
  deployed frontend origin.

## Testing

[Playwright](https://playwright.dev) is available for browser-driven smoke checks of
critical flows. There is no formal unit-test suite yet.

## Future improvements

Tracked technical debt and opportunities (see the refactor report for context):

- Add a unit-test suite (Vitest/RTL on the frontend, pytest on the backend) and a CI
  workflow (GitHub Actions: lint + type-check + tests).
- Resolve the pre-existing TypeScript strictness warnings (Recharts `Formatter`
  typings; Supabase result `any`s) — currently tolerated by the build.
- Introduce a typed frontend API client wrapping `fetch` so response shapes are
  shared types rather than inline interfaces.
- Move `playwright` from runtime `dependencies` to `devDependencies`.
- Split the largest files (`src/app/portfolio/page.tsx`, `src/components/ExportReport.tsx`)
  into smaller units once a test net exists.
- Optionally exclude the `/style-guide` route from production builds.
