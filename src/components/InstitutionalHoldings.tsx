"use client";

import { useState, useEffect, useRef } from "react";
import { API_BASE_URL } from "@/lib/api";
import { Card } from "./ui/Card";

interface FundHolding {
  fund: string;
  manager: string;
  holds: boolean;
  shares: number | null;
  value: number | null;
  period: string | null;
}

function fmtValue(v: number | null): string {
  if (!v) return "";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

export function InstitutionalHoldings({ ticker }: { ticker: string }) {
  const [funds, setFunds] = useState<FundHolding[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const fetchedTicker = useRef<string | null>(null);

  useEffect(() => {
    if (fetchedTicker.current === ticker) return;
    fetchedTicker.current = ticker;
    setLoading(true);
    setUnavailable(false);
    fetch(`${API_BASE_URL}/institutional-holdings/${ticker}`)
      .then((r) => r.json())
      .then((d) => {
        // The backend returns this when SEC EDGAR is unreachable and there's no
        // cached data — show an honest note instead of "—" for every fund.
        if (d.status === "temporarily_unavailable") {
          setUnavailable(true);
          setFunds([]);
        } else {
          setFunds(d.funds || []);
        }
      })
      .catch(() => setUnavailable(true))
      .finally(() => setLoading(false));
  }, [ticker]);

  return (
    <Card>
      <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
        Legendary Investor Holdings
      </h2>

      {loading ? (
        <div className="flex justify-center py-6 text-moat-text-muted animate-pulse">
          Checking fund filings…
        </div>
      ) : unavailable ? (
        <p className="py-4 text-center text-sm text-moat-text-muted">
          Institutional data temporarily unavailable — please check back shortly.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {funds.map((f) => (
            <div
              key={f.fund}
              className={`flex items-center justify-between rounded-lg border px-3 py-2.5 ${
                f.holds
                  ? "border-moat-accent/30 bg-moat-accent/10"
                  : "border-moat-border bg-moat-bg/40"
              }`}
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className={f.holds ? "text-moat-accent" : "text-moat-text-muted"}>
                  {f.holds ? "✓" : "—"}
                </span>
                <div className="flex flex-col min-w-0">
                  <span className="text-sm text-moat-text truncate">{f.fund}</span>
                  <span className="text-[10px] text-moat-text-muted truncate">
                    {f.manager}
                  </span>
                </div>
              </div>
              {f.holds && (
                <div className="flex flex-col items-end shrink-0 pl-2">
                  <span className="text-xs font-mono text-moat-text">
                    {f.shares?.toLocaleString()} sh
                  </span>
                  <span className="text-[10px] font-mono text-moat-text-muted">
                    {fmtValue(f.value)}
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
