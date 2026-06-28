"use client";

import { useState, useEffect, useRef } from "react";
import { API_BASE_URL } from "@/lib/api";
import { Card } from "./ui/Card";

interface ThesisData {
  business_summary: string;
  sector: string | null;
  industry: string | null;
  employees: number | null;
  business_overview: string | null;
  investment_thesis: string | null;
  key_risks: string | null;
}

export function CompanyThesis({
  ticker,
  companyName,
}: {
  ticker: string;
  companyName: string;
}) {
  const [data, setData] = useState<ThesisData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const fetchedTicker = useRef<string | null>(null);

  function load(refresh = false) {
    setLoading(true);
    setError(false);
    fetch(`${API_BASE_URL}/thesis/${ticker}${refresh ? "?refresh=true" : ""}`)
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.json();
      })
      .then((d) => {
        setData(d);
        if (!d?.investment_thesis) setError(true);
      })
      .catch(() => {
        setData(null);
        setError(true);
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (fetchedTicker.current === ticker) return;
    fetchedTicker.current = ticker;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker]);

  const meta = [
    data?.sector,
    data?.industry,
    data?.employees ? `${data.employees.toLocaleString()} employees` : null,
  ].filter(Boolean);

  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
            About {companyName}
          </h2>
          {meta.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-moat-text-muted">
              {meta.map((m, i) => (
                <span key={i}>
                  {m}
                  {i < meta.length - 1 && (
                    <span className="ml-3 text-moat-border">·</span>
                  )}
                </span>
              ))}
            </div>
          )}
        </div>

        {loading ? (
          <div className="py-6 text-moat-text-muted animate-pulse text-sm">
            Generating equity research thesis…
          </div>
        ) : error ? (
          <div className="flex flex-col items-start gap-2 py-2">
            <p className="text-sm text-moat-text-muted">Couldn&apos;t load right now.</p>
            <button
              onClick={() => load(true)}
              className="px-4 py-1.5 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
            >
              Try Again
            </button>
          </div>
        ) : (
          <>
            {data?.business_overview && (
              <p className="text-sm text-moat-text-muted leading-relaxed">
                {data.business_overview}
              </p>
            )}

            {data?.investment_thesis && (
              <div className="rounded-xl border-l-2 border-moat-accent bg-moat-accent/5 px-4 py-3">
                <h3 className="text-[10px] font-semibold uppercase tracking-wider text-moat-accent mb-1.5">
                  Investment Thesis
                </h3>
                <p className="text-sm text-moat-text leading-relaxed">
                  {data.investment_thesis}
                </p>
              </div>
            )}

            {data?.key_risks && (
              <div>
                <h3 className="text-[10px] font-semibold uppercase tracking-wider text-moat-danger mb-1">
                  Key Risks
                </h3>
                <p className="text-sm text-moat-text-muted leading-relaxed">
                  {data.key_risks}
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
