"use client";

import { useState, useEffect, useCallback } from "react";
import { API_BASE_URL } from "@/lib/api";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";

interface Result {
  ticker: string;
  company_name: string;
  current_price: number | null;
  intrinsic_value: number | null;
  margin_of_safety_pct: number | null;
  f_score: number | null;
  // Model confidence for the valuation — critical context at the extremes, since
  // sorting by margin of safety floats the boldest calls to the top.
  confidence?: "high" | "medium" | "low" | null;
  moat_score?: number | null;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs > 1 ? "s" : ""} ago`;
  const days = Math.floor(hrs / 24);
  return `${days} day${days > 1 ? "s" : ""} ago`;
}

export default function ScreenerPage() {
  const router = useRouter();
  const [minMos, setMinMos] = useState(0);
  const [minFScore, setMinFScore] = useState(5);
  const [results, setResults] = useState<Result[]>([]);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [totalScreened, setTotalScreened] = useState(0);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    fetch(
      `${API_BASE_URL}/screener?min_margin_of_safety=${minMos}&min_f_score=${minFScore}`
    )
      .then((r) => r.json())
      .then((d) => {
        setResults(d.results || []);
        setLastUpdated(d.last_updated);
        setTotalScreened(d.total_screened || 0);
        setNote(d.note || null);
      })
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, [minMos, minFScore]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col gap-6 px-4 py-8 mx-auto w-full max-w-5xl">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-3xl font-bold text-moat-text">Stock Screener</h1>
        <span className="text-xs text-moat-text-muted">
          {totalScreened > 0 && `${totalScreened} S&P 500 stocks · `}
          Last updated: {timeAgo(lastUpdated)}
        </span>
      </div>

      {/* Filters */}
      <Card>
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
              Min Margin of Safety: <span className="text-moat-accent">{minMos}%</span>
            </label>
            <input
              type="range"
              min={-50}
              max={100}
              step={5}
              value={minMos}
              onChange={(e) => setMinMos(Number(e.target.value))}
              className="accent-moat-accent"
            />
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
              Min F-Score: <span className="text-moat-accent">{minFScore} / 9</span>
            </label>
            <input
              type="range"
              min={0}
              max={9}
              step={1}
              value={minFScore}
              onChange={(e) => setMinFScore(Number(e.target.value))}
              className="accent-moat-accent"
            />
          </div>
        </div>
      </Card>

      <Card>
        {loading ? (
          <div className="flex justify-center py-10 text-moat-text-muted animate-pulse">
            Filtering…
          </div>
        ) : results.length === 0 ? (
          <p className="text-sm text-moat-text-muted text-center py-8">
            {note ?? "No stocks match these filters. Try loosening them."}
          </p>
        ) : (
          <div className="overflow-x-auto">
            {note && (
              <p className="text-xs text-moat-warning mb-2">⚠ {note}</p>
            )}
            <p className="text-xs text-moat-text-muted mb-3">
              {results.length} match{results.length === 1 ? "" : "es"}
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wider text-moat-text-muted border-b border-moat-border">
                  <th className="py-2 pr-4 font-medium">Ticker</th>
                  <th className="py-2 pr-4 font-medium hidden sm:table-cell">Company</th>
                  <th className="py-2 pr-4 font-medium text-right">Price</th>
                  <th className="py-2 pr-4 font-medium text-right">Intrinsic</th>
                  <th className="py-2 pr-4 font-medium text-right">Margin</th>
                  <th className="py-2 pr-4 font-medium text-right hidden md:table-cell">Confidence</th>
                  <th className="py-2 font-medium text-right">F-Score</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr
                    key={r.ticker}
                    onClick={() => router.push(`/analyze/${r.ticker}`)}
                    className="border-b border-moat-border/40 hover:bg-moat-surface-hover transition-colors cursor-pointer"
                  >
                    <td className="py-2.5 pr-4 font-mono font-semibold text-moat-accent">
                      {r.ticker}
                    </td>
                    <td className="py-2.5 pr-4 text-moat-text-muted hidden sm:table-cell truncate max-w-[200px]">
                      {r.company_name}
                    </td>
                    <td className="py-2.5 pr-4 text-right font-mono text-moat-text">
                      {r.current_price != null ? `$${r.current_price.toFixed(2)}` : "—"}
                    </td>
                    <td className="py-2.5 pr-4 text-right font-mono text-moat-text">
                      {r.intrinsic_value != null ? `$${r.intrinsic_value.toFixed(2)}` : "N/A"}
                    </td>
                    <td className="py-2.5 pr-4 text-right">
                      {r.margin_of_safety_pct != null ? (
                        <Badge variant={r.margin_of_safety_pct > 0 ? "success" : "danger"}>
                          {r.margin_of_safety_pct > 0 ? "+" : ""}
                          {r.margin_of_safety_pct.toFixed(1)}%
                        </Badge>
                      ) : (
                        "N/A"
                      )}
                    </td>
                    <td className="py-2.5 pr-4 text-right hidden md:table-cell">
                      {r.confidence ? (
                        <Badge
                          variant={
                            r.confidence === "high"
                              ? "success"
                              : r.confidence === "medium"
                              ? "warning"
                              : "danger"
                          }
                        >
                          {r.confidence}
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-2.5 text-right font-mono text-moat-text">
                      {r.f_score != null ? `${r.f_score}/9` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="text-xs text-moat-text-muted text-center">
        Don&apos;t see your stock?{" "}
        <Link href="/" className="text-moat-accent hover:underline">
          Analyze any ticker directly
        </Link>
        .
      </p>
    </div>
  );
}
