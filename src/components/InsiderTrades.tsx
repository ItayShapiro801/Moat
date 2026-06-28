"use client";

import { useState, useEffect, useRef } from "react";
import { Card } from "./ui/Card";

interface Trade {
  insider_name: string;
  title: string;
  date: string | null;
  transaction_code: string;
  transaction_type: string;
  shares: number;
  price: number;
  value: number;
  shares_owned_after: number | null;
}

function typeColor(type: string): string {
  if (type === "Purchase") return "text-moat-accent";
  if (type === "Sale") return "text-moat-danger";
  return "text-moat-text-muted";
}

function fmtValue(v: number): string {
  if (!v) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toLocaleString()}`;
}

export function InsiderTrades({ ticker }: { ticker: string }) {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const fetchedTicker = useRef<string | null>(null);

  useEffect(() => {
    if (fetchedTicker.current === ticker) return;
    fetchedTicker.current = ticker;
    setLoading(true);
    setError(false);
    fetch(`http://localhost:8000/insider-trades/${ticker}`)
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.json();
      })
      .then((d) => setTrades(d.trades || []))
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, [ticker]);

  return (
    <Card>
      <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
        Insider Trades
      </h2>

      {loading && (
        <div className="flex justify-center py-8 text-moat-text-muted animate-pulse">
          Loading insider activity…
        </div>
      )}

      {!loading && (error || trades.length === 0) && (
        <p className="text-sm text-moat-text-muted text-center py-6">
          No recent insider trades reported for this company.
        </p>
      )}

      {!loading && trades.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-moat-text-muted border-b border-moat-border">
                <th className="py-2 pr-4 font-medium">Insider</th>
                <th className="py-2 pr-4 font-medium hidden sm:table-cell">Title</th>
                <th className="py-2 pr-4 font-medium">Date</th>
                <th className="py-2 pr-4 font-medium">Type</th>
                <th className="py-2 pr-4 font-medium text-right">Shares</th>
                <th className="py-2 font-medium text-right">Value</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr
                  key={i}
                  className="border-b border-moat-border/40 hover:bg-moat-surface-hover transition-colors"
                >
                  <td className="py-2 pr-4 text-moat-text">{t.insider_name}</td>
                  <td className="py-2 pr-4 text-moat-text-muted hidden sm:table-cell">
                    {t.title}
                  </td>
                  <td className="py-2 pr-4 text-moat-text-muted font-mono text-xs">
                    {t.date || "—"}
                  </td>
                  <td className={`py-2 pr-4 font-medium ${typeColor(t.transaction_type)}`}>
                    {t.transaction_type}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono text-moat-text">
                    {t.shares.toLocaleString()}
                  </td>
                  <td className={`py-2 text-right font-mono ${typeColor(t.transaction_type)}`}>
                    {fmtValue(t.value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
