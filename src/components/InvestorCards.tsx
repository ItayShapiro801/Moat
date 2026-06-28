"use client";

import { useState, useEffect, useRef } from "react";
import { Card } from "./ui/Card";
import { Badge } from "./ui/Badge";

interface Investor {
  name: string;
  slug: string;
  score: number | null;
  verdict: string | null;
  bull_case: string | null;
  bear_case: string | null;
}

function scoreVariant(score: number | null): "success" | "warning" | "danger" | "neutral" {
  if (score == null) return "neutral";
  if (score > 7) return "success";
  if (score >= 4) return "warning";
  return "danger";
}

function verdictVariant(verdict: string | null): "success" | "warning" | "danger" | "neutral" {
  if (verdict === "Buy") return "success";
  if (verdict === "Hold") return "warning";
  if (verdict === "Sell") return "danger";
  return "neutral";
}

export function InvestorCards({ ticker }: { ticker: string }) {
  const [investors, setInvestors] = useState<Investor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  // Guard against React Strict Mode's intentional double-invoke in dev: only
  // fetch once per ticker, not twice.
  const fetchedTicker = useRef<string | null>(null);

  function load(refresh = false) {
    setLoading(true);
    setError(false);
    fetch(`http://localhost:8000/investors/${ticker}${refresh ? "?refresh=true" : ""}`)
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.json();
      })
      .then((d) => {
        const list = d.investors || [];
        setInvestors(list);
        if (list.length === 0) setError(true);
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (fetchedTicker.current === ticker) return;
    fetchedTicker.current = ticker;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker]);

  return (
    <div>
      <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
        Legendary Investor Takes
      </h2>

      {loading && (
        <Card>
          <div className="flex justify-center py-10">
            <p className="text-moat-text-muted animate-pulse">
              Consulting the legends...
            </p>
          </div>
        </Card>
      )}

      {!loading && (error || investors.length === 0) && (
        <Card>
          <div className="flex flex-col items-center gap-3 py-6">
            <p className="text-moat-text-muted text-sm">
              Couldn&apos;t load right now.
            </p>
            <button
              onClick={() => load(true)}
              className="px-4 py-2 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
            >
              Try Again
            </button>
          </div>
        </Card>
      )}

      {!loading && investors.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {investors.map((inv) => (
            <Card key={inv.slug} className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <img
                  src={`/investors/${inv.slug}.jpg`}
                  alt={inv.name}
                  width={56}
                  height={56}
                  className="h-14 w-14 rounded-full object-cover border border-moat-border"
                />
                <div className="flex flex-col gap-1">
                  <span className="text-sm font-semibold text-moat-text">
                    {inv.name}
                  </span>
                  <div className="flex items-center gap-2">
                    <Badge variant={scoreVariant(inv.score)}>
                      {inv.score != null ? inv.score.toFixed(1) : "N/A"}/10
                    </Badge>
                    {inv.verdict && (
                      <Badge variant={verdictVariant(inv.verdict)}>
                        {inv.verdict}
                      </Badge>
                    )}
                  </div>
                </div>
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-moat-accent">
                  Bull Case
                </span>
                <p className="text-xs text-moat-text-muted leading-relaxed">
                  {inv.bull_case || "—"}
                </p>
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-moat-danger">
                  Bear Case
                </span>
                <p className="text-xs text-moat-text-muted leading-relaxed">
                  {inv.bear_case || "—"}
                </p>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
