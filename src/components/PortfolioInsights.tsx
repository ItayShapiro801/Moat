"use client";

import { useState, useEffect } from "react";
import { Card } from "./ui/Card";
import { getSupabaseClient } from "@/lib/supabase/client";

interface Holding {
  ticker: string;
  quote_type?: string;
  allocation_pct: number;
  current_price: number;
  intrinsic_value: number | null;
  margin_of_safety_pct: number | null;
  f_score: number | null;
  gain_loss_pct: number;
}

interface Insights {
  health_summary?: string;
  concentration_risk?: string;
  valuation_observations?: string;
  portfolio_score?: number;
  score_justification?: string;
}

function scoreColor(n: number | undefined): string {
  if (n == null) return "text-moat-text-muted";
  if (n >= 7) return "text-moat-accent";
  if (n >= 4) return "text-moat-warning";
  return "text-moat-danger";
}

export function PortfolioInsights({
  holdings,
  totalValue,
  totalGainPct,
  holdingsHash,
  userId,
}: {
  holdings: Holding[];
  totalValue: number;
  totalGainPct: number;
  holdingsHash: string;
  userId: string | null;
}) {
  const [data, setData] = useState<Insights | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  // On load (or when holdings change), check the cache. A row whose stored
  // holdings_hash matches the current one is shown immediately — no regeneration.
  // A mismatch (holdings changed) clears it so the "Get Key Insights" button shows.
  useEffect(() => {
    let active = true;
    setData(null);
    setError(false);
    const supabase = getSupabaseClient();
    if (!supabase || !userId) return;
    supabase
      .from("portfolio_insights_cache")
      .select("holdings_hash, insights")
      .eq("user_id", userId)
      .maybeSingle()
      .then(({ data: row }: { data: { holdings_hash: string; insights: Insights } | null }) => {
        if (active && row && row.holdings_hash === holdingsHash) {
          setData(row.insights as Insights);
        }
      });
    return () => {
      active = false;
    };
  }, [userId, holdingsHash]);

  function generate() {
    setLoading(true);
    setError(false);
    fetch("http://localhost:8000/portfolio-insights", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        holdings,
        total_value: totalValue,
        total_gain_loss_pct: totalGainPct,
      }),
    })
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.json();
      })
      .then((insights) => {
        setData(insights);
        // Persist so it stays stable across navigation until holdings change.
        const supabase = getSupabaseClient();
        if (supabase && userId) {
          supabase
            .from("portfolio_insights_cache")
            .upsert(
              {
                user_id: userId,
                holdings_hash: holdingsHash,
                insights,
                generated_at: new Date().toISOString(),
              },
              { onConflict: "user_id" }
            )
            .then(() => {});
        }
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }

  if (!data && !loading && !error) {
    return (
      <button
        onClick={generate}
        disabled={holdings.length === 0}
        className="self-start px-4 py-2 rounded-lg text-sm font-medium border border-moat-accent text-moat-accent hover:bg-moat-accent/10 transition-colors disabled:opacity-50"
      >
        ✨ Get Key Insights
      </button>
    );
  }

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
          Portfolio Key Insights
        </h2>
        {data && (
          <span className={`text-2xl font-bold font-mono ${scoreColor(data.portfolio_score)}`}>
            {data.portfolio_score != null ? `${data.portfolio_score}/10` : ""}
          </span>
        )}
      </div>

      {loading && (
        <div className="flex flex-col items-center gap-3 py-8">
          <div className="h-6 w-6 rounded-full border-2 border-moat-accent border-t-transparent animate-spin" />
          <p className="text-sm text-moat-text-muted">Analyzing your portfolio…</p>
        </div>
      )}

      {!loading && error && (
        <div className="flex flex-col items-start gap-2 py-2">
          <p className="text-sm text-moat-text-muted">Couldn&apos;t load right now.</p>
          <button
            onClick={generate}
            className="px-4 py-1.5 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
          >
            Try Again
          </button>
        </div>
      )}

      {!loading && data && (
        <div className="flex flex-col gap-4">
          {data.health_summary && (
            <div>
              <h3 className="text-[10px] font-semibold uppercase tracking-wider text-moat-accent mb-1">
                Health Summary
              </h3>
              <p className="text-sm text-moat-text-muted leading-relaxed">{data.health_summary}</p>
            </div>
          )}
          {data.concentration_risk && (
            <div>
              <h3 className="text-[10px] font-semibold uppercase tracking-wider text-moat-warning mb-1">
                Concentration Risk
              </h3>
              <p className="text-sm text-moat-text-muted leading-relaxed">{data.concentration_risk}</p>
            </div>
          )}
          {data.valuation_observations && (
            <div>
              <h3 className="text-[10px] font-semibold uppercase tracking-wider text-moat-text-muted mb-1">
                Valuation Observations
              </h3>
              <p className="text-sm text-moat-text-muted leading-relaxed">{data.valuation_observations}</p>
            </div>
          )}
          {data.score_justification && (
            <div className="rounded-xl border-l-2 border-moat-accent bg-moat-accent/5 px-4 py-3">
              <h3 className="text-[10px] font-semibold uppercase tracking-wider text-moat-accent mb-1">
                Score Rationale
              </h3>
              <p className="text-sm text-moat-text leading-relaxed">{data.score_justification}</p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
