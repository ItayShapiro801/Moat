# Data-provider free-tier evidence

Moat runs entirely on **free** data-provider tiers. Those terms can change without
notice (a provider can shrink or remove a free plan, or shut down). This folder is a
dated record of what each provider's free tier offered **at the time Moat was built**,
so a future maintainer can tell whether behaviour changed because of *our* code or
because a *provider changed the deal underneath us*.

## What to keep here

For each provider, drop in:

1. **A screenshot** of the provider's pricing / plans page showing the free-tier
   limits, named `<provider>-pricing-<YYYY-MM-DD>.png`.
2. **A PDF "print to PDF"** of the same page (survives even if the screenshot is
   ambiguous), named `<provider>-pricing-<YYYY-MM-DD>.pdf`.
3. Optionally, a screenshot of the **account/API-key dashboard** showing the daily
   quota, named `<provider>-dashboard-<YYYY-MM-DD>.png`.

Keep the date in the filename — never overwrite an old one. A new capture on a new
date sits alongside the old, so the history of terms is visible.

## Providers and the free-tier terms Moat was built against

| Provider | Free-tier limit (as built) | What Moat uses it for | Evidence files |
|----------|----------------------------|-----------------------|----------------|
| **SEC EDGAR** | Unlimited, official | Financial statements (companyfacts XBRL), Form 4, 13F | (government API; no plan to capture) |
| **Finnhub** | 60 req/min | Price, current multiples, sector, historical-growth proxy | `finnhub-pricing-YYYY-MM-DD.*` |
| **Financial Modeling Prep** | 250 req/day **per key** (3 keys ≈ 750/day) | Full statement bundle + external DCF while budget lasts | `fmp-pricing-YYYY-MM-DD.*` |
| **BusinessQuant** | **30 req/day per key** (6 keys ≈ 180/day ≈ 90 stocks) | Analyst **forward estimates** (forward EPS/revenue consensus) | `businessquant-pricing-YYYY-MM-DD.*` |
| **Groq / Gemini / Cerebras** | Free LLM tiers (rotated) | Narrative/thesis/investor-persona generation | `<llm>-pricing-YYYY-MM-DD.*` |

## If a provider changes its free tier later

Because each data *piece* has a fallback (see the data-strategy section in the root
`README.md`), losing one provider degrades gracefully rather than breaking:

- **BusinessQuant** shrinks/removed → forward estimates fall back to the **Finnhub
  historical-growth proxy** automatically (`enrich_growth`). Valuations get slightly
  less precise for growth names; nothing breaks.
- **FMP** shrinks → the **EDGAR + Finnhub** combo already covers statements + price
  uncapped; only the external-DCF cross-check is lost.
- **Finnhub** changes → price/multiples would need another free quote source; this is
  the one piece without a second free fallback today (noted as future work).

> Capture fresh screenshots/PDFs here whenever you (re)confirm a provider's terms, so
> this table stays honest.
