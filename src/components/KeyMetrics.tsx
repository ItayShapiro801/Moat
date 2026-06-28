"use client";

import { useState, useEffect } from "react";
import { Card } from "./ui/Card";
import { Badge } from "./ui/Badge";

interface AnalystRatings {
  recommendation: string | null;
  num_analysts: number | null;
  target_mean_price: number | null;
  target_high_price: number | null;
  target_low_price: number | null;
}

interface MetricsData {
  valuation: Record<string, number | null>;
  dividends: Record<string, number | null>;
  quality: Record<string, number | null>;
  financial_health: Record<string, number | null>;
  analyst_ratings?: AnalystRatings;
}

function ratingVariant(rec: string | null): "success" | "warning" | "danger" | "neutral" {
  const r = (rec || "").toLowerCase();
  if (r.includes("buy")) return "success";
  if (r.includes("hold")) return "warning";
  if (r.includes("sell") || r.includes("underperform")) return "danger";
  return "neutral";
}

interface MetricDef {
  key: string;
  label: string;
  tip: string;
  format: (v: number) => string;
}

const fmt = {
  x: (v: number) => `${v.toFixed(1)}x`,
  pct: (v: number) => `${v.toFixed(1)}%`,
  pct2: (v: number) => `${v.toFixed(2)}%`,
  usd: (v: number) => `$${v.toFixed(2)}`,
  ratio: (v: number) => v.toFixed(2),
  cap: (v: number) => {
    const abs = Math.abs(v);
    if (abs >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
    if (abs >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
    if (abs >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
    return `$${v.toLocaleString()}`;
  },
};

const GROUPS: {
  title: string;
  source: keyof MetricsData;
  metrics: MetricDef[];
}[] = [
  {
    title: "Valuation",
    source: "valuation",
    metrics: [
      { key: "pe_ratio", label: "P/E Ratio", tip: "Price / Earnings (TTM)", format: fmt.x },
      { key: "forward_pe", label: "Forward P/E", tip: "Price / Forward Earnings estimate", format: fmt.x },
      { key: "pb_ratio", label: "P/B Ratio", tip: "Price / Book Value", format: fmt.x },
      { key: "ev_ebitda", label: "EV/EBITDA", tip: "Enterprise Value / EBITDA", format: fmt.x },
      { key: "p_fcf", label: "P/FCF", tip: "Price / Free Cash Flow", format: fmt.x },
      { key: "peg_ratio", label: "PEG Ratio", tip: "P/E / Earnings Growth Rate", format: fmt.ratio },
    ],
  },
  {
    title: "Dividends & Income",
    source: "dividends",
    metrics: [
      { key: "dividend_yield", label: "Dividend Yield", tip: "Annual dividend / share price", format: fmt.pct2 },
      { key: "annual_dividend", label: "Annual Dividend", tip: "Dividend per share per year", format: fmt.usd },
      { key: "payout_ratio", label: "Payout Ratio", tip: "Dividends paid / Net Income", format: fmt.pct },
    ],
  },
  {
    title: "Quality & Liquidity",
    source: "quality",
    metrics: [
      { key: "current_ratio", label: "Current Ratio", tip: "Current Assets / Current Liabilities", format: fmt.ratio },
      { key: "quick_ratio", label: "Quick Ratio", tip: "(Current Assets - Inventory) / Current Liabilities", format: fmt.ratio },
      { key: "roic", label: "ROA", tip: "Return on Assets", format: fmt.pct },
      { key: "profit_margin", label: "Profit Margin", tip: "Net Income / Revenue", format: fmt.pct },
    ],
  },
  {
    title: "Financial Health",
    source: "financial_health",
    metrics: [
      { key: "eps_ttm", label: "EPS (TTM)", tip: "Earnings Per Share, trailing 12 months", format: fmt.usd },
      { key: "fcf_per_share", label: "FCF/Share", tip: "Free Cash Flow per Share", format: fmt.usd },
      {
        key: "net_debt_per_share",
        label: "Net Debt/Share",
        tip: "(Total Debt - Cash) / Shares. Negative = net cash",
        format: (v: number) => (v < 0 ? `($${Math.abs(v).toFixed(2)}) net cash` : `$${v.toFixed(2)}`),
      },
      { key: "debt_equity", label: "Debt/Equity", tip: "Total Debt / Total Equity", format: fmt.ratio },
      { key: "market_cap", label: "Market Cap", tip: "Total market capitalization", format: fmt.cap },
    ],
  },
];

export function KeyMetrics({
  ticker,
  fairValue,
  currentPrice,
}: {
  ticker: string;
  fairValue: number | null;
  currentPrice: number;
}) {
  const [data, setData] = useState<MetricsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`http://localhost:8000/metrics/${ticker}`)
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [ticker]);

  if (loading) {
    return (
      <Card>
        <div className="flex items-center justify-center py-8 text-moat-text-muted animate-pulse">
          Loading metrics...
        </div>
      </Card>
    );
  }
  if (!data) return null;

  const efficiencyGap =
    fairValue != null && currentPrice > 0
      ? ((fairValue - currentPrice) / currentPrice) * 100
      : null;

  const ar = data.analyst_ratings;
  const hasAnalysts = ar && (ar.recommendation || ar.target_mean_price);

  return (
    <Card>
      <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
        Key Metrics
      </h2>

      {hasAnalysts && (
        <div className="mb-5 rounded-xl border border-moat-border bg-moat-bg/40 p-4 flex flex-col gap-4">
          {/* Line 1: consensus + analyst count */}
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-moat-text-muted">
              Analyst Consensus
            </span>
            {ar!.recommendation && (
              <Badge variant={ratingVariant(ar!.recommendation)}>
                {ar!.recommendation.toUpperCase()}
              </Badge>
            )}
            {ar!.num_analysts != null && (
              <span className="text-xs text-moat-text-muted">
                Based on {ar!.num_analysts} analysts
              </span>
            )}
          </div>

          {/* Line 2: price target block */}
          {ar!.target_mean_price != null && (
            <div className="border-t border-moat-border/50 pt-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-moat-text-muted">
                  Price Target
                </span>
                <span
                  className={`text-xs font-mono font-medium ${
                    ar!.target_mean_price > currentPrice ? "text-moat-accent" : "text-moat-danger"
                  }`}
                >
                  {ar!.target_mean_price > currentPrice ? "+" : ""}
                  {(((ar!.target_mean_price - currentPrice) / currentPrice) * 100).toFixed(0)}% vs current
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between gap-2 text-center">
                <div className="flex-1">
                  <div className="text-[10px] uppercase tracking-wider text-moat-text-muted">Low</div>
                  <div className="font-mono text-sm text-moat-text-muted">
                    {ar!.target_low_price != null ? `$${ar!.target_low_price.toFixed(0)}` : "—"}
                  </div>
                </div>
                <div className="flex-1">
                  <div className="text-[10px] uppercase tracking-wider text-moat-text-muted">Mean</div>
                  <div className="font-mono text-base font-semibold text-moat-text">
                    ${ar!.target_mean_price.toFixed(0)}
                  </div>
                </div>
                <div className="flex-1">
                  <div className="text-[10px] uppercase tracking-wider text-moat-text-muted">High</div>
                  <div className="font-mono text-sm text-moat-text-muted">
                    {ar!.target_high_price != null ? `$${ar!.target_high_price.toFixed(0)}` : "—"}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {GROUPS.map((group) => (
          <div key={group.title}>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-moat-accent mb-3">
              {group.title}
            </h3>
            <div className="flex flex-col gap-2">
              {group.metrics.map((m) => {
                const raw = data[group.source][m.key];
                const display =
                  raw != null ? m.format(raw as number) : "N/A";
                return (
                  <div
                    key={m.key}
                    className="flex items-center justify-between py-1 border-b border-moat-border/50"
                    title={m.tip}
                  >
                    <span className="text-sm text-moat-text-muted">
                      {m.label}
                    </span>
                    <span
                      className={`text-sm font-mono font-medium ${
                        raw != null ? "text-moat-text" : "text-moat-text-muted"
                      }`}
                    >
                      {display}
                    </span>
                  </div>
                );
              })}
              {group.title === "Valuation" && (
                <div
                  className="flex items-center justify-between py-1 border-b border-moat-border/50"
                  title="(DCF Fair Value - Price) / Price"
                >
                  <span className="text-sm text-moat-text-muted">
                    DCF Gap
                  </span>
                  <span
                    className={`text-sm font-mono font-medium ${
                      efficiencyGap == null
                        ? "text-moat-text-muted"
                        : efficiencyGap >= 0
                          ? "text-moat-accent"
                          : "text-moat-danger"
                    }`}
                  >
                    {efficiencyGap != null
                      ? `${efficiencyGap >= 0 ? "+" : ""}${efficiencyGap.toFixed(1)}% ${efficiencyGap >= 0 ? "Undervalued" : "Overvalued"}`
                      : "N/A"}
                  </span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
