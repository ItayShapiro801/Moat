# Contributing

Thanks for contributing to Moat. This guide keeps the codebase consistent and easy
to review.

## Getting set up

Follow [Getting Started](../README.md#getting-started) to install dependencies,
configure environment variables, and run both servers.

## Branching & commits

- Branch off `main`: `feat/...`, `fix/...`, `chore/...`, `docs/...`, `refactor/...`.
- Write [Conventional Commits](https://www.conventionalcommits.org):
  `feat: add crypto support to portfolio`, `fix: correct FX conversion rounding`.
- Keep commits focused and reviewable; avoid mixing refactors with behavior changes.

## Before opening a PR

Run the checks locally:

```bash
npx tsc --noEmit   # type-check
npm run lint       # lint
npm run build      # build must succeed
```

If you touched a critical flow (analyze, compare, portfolio), do a quick manual
smoke test against a running backend.

## Code style

- Follow `.editorconfig` and `.prettierrc.json` (2-space TS, 4-space Python).
- Frontend: import via the `@/` alias; resolve backend URLs through `@/lib/api`.
- Keep **routers thin** and put reusable logic in **services** (backend) or
  `lib`/`ui` (frontend).
- Prefer self-documenting code; add comments only where intent isn't obvious.

## What not to do

- Don't move files under `src/app/` to reorganize — those paths are the routes.
- Don't commit secrets. Use `.env.local` / `backend/.env` and update the
  `.env.example` templates when adding a variable.
- Don't introduce behavior changes inside a "refactor" PR.

## PR checklist

- [ ] Type-check, lint, and build pass
- [ ] No secrets or generated files committed
- [ ] New env vars documented in `.env.example` and `docs/Configuration.md`
- [ ] Critical flows smoke-tested if affected
- [ ] Commit messages follow Conventional Commits
