"use client";

import { useState, useEffect } from "react";
import { Card } from "./ui/Card";
import { Badge } from "./ui/Badge";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

const METRICS = [
  { key: "revenue", label: "Revenue" },
  { key: "eps", label: "EPS" },
  { key: "fcf", label: "Free Cash Flow" },
  { key: "gross_profit", label: "Gross" },
  { key: "operating_income", label: "Op Income" },
  { key: "net_income", label: "Net Income" },
  { key: "shares_outstanding", label: "Shares" },
] as const;

type MetricKey = (typeof METRICS)[number]["key"];

interface DataPoint {
  year: string;
  value: number;
}

interface FinancialsData {
  revenue: DataPoint[];
  eps: DataPoint[];
  fcf: DataPoint[];
  gross_profit: DataPoint[];
  operating_income: DataPoint[];
  net_income: DataPoint[];
  shares_outstanding: DataPoint[];
}

function formatValue(val: number, metric: MetricKey): string {
  if (metric === "eps") return `$${val.toFixed(2)}`;
  const abs = Math.abs(val);
  const sign = val < 0 ? "-" : "";
  if (metric === "shares_outstanding") {
    if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(1)}B`;
    if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(0)}M`;
    return val.toLocaleString();
  }
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(0)}M`;
  return `$${val.toLocaleString()}`;
}

function computeCAGR(points: DataPoint[]): number | null {
  if (points.length < 2) return null;
  const first = points[0].value;
  const last = points[points.length - 1].value;
  if (first <= 0 || last <= 0) return null;
  const years = points.length - 1;
  return ((last / first) ** (1 / years) - 1) * 100;
}

export function FinancialTrends({ ticker }: { ticker: string }) {
  const [metric, setMetric] = useState<MetricKey>("revenue");
  const [period, setPeriod] = useState<"5y" | "all">("all");
  const [data, setData] = useState<FinancialsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`http://localhost:8000/financials/${ticker}`)
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [ticker]);

  const allPoints = data ? (data[metric] as DataPoint[]) : [];
  const hasExtraHistory = allPoints.length > 5;
  const points = period === "5y" ? allPoints.slice(-5) : allPoints;
  const cagr = computeCAGR(points);
  const metricLabel = METRICS.find((m) => m.key === metric)!.label;

  return (
    <Card id="export-financial-trends">
      <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
        <h2 className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
          Financial Trends
        </h2>
        <div className="flex items-center gap-2">
          {cagr !== null && (
            <Badge variant={cagr >= 0 ? "success" : "danger"}>
              CAGR {cagr >= 0 ? "+" : ""}
              {cagr.toFixed(1)}%
            </Badge>
          )}
          {hasExtraHistory && (
            <div className="flex gap-1">
              {(["5y", "all"] as const).map((p) => (
                <button
                  key={p}
                  onClick={() => setPeriod(p)}
                  className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                    period === p
                      ? "bg-moat-accent text-moat-bg"
                      : "text-moat-text-muted hover:text-moat-text hover:bg-moat-surface-hover"
                  }`}
                >
                  {p === "5y" ? "5Y" : "All"}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="flex gap-1 mb-4 flex-wrap">
        {METRICS.map((m) => (
          <button
            key={m.key}
            onClick={() => setMetric(m.key)}
            className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
              metric === m.key
                ? "bg-moat-accent text-moat-bg"
                : "text-moat-text-muted hover:text-moat-text hover:bg-moat-surface-hover"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      {loading ? (
        <div className="flex items-center justify-center h-[220px] text-moat-text-muted animate-pulse">
          Loading...
        </div>
      ) : points.length > 0 ? (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={points}>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="#1f2733"
              vertical={false}
            />
            <XAxis
              dataKey="year"
              tick={{ fill: "#8b93a1", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tickFormatter={(v: number) => formatValue(v, metric)}
              tick={{ fill: "#8b93a1", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={65}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "#11161f",
                border: "1px solid #1f2733",
                borderRadius: "8px",
                color: "#e8eaed",
              }}
              formatter={(value: number) => [
                formatValue(value, metric),
                metricLabel,
              ]}
              cursor={{ fill: "rgba(255,255,255,0.05)" }}
            />
            <Bar dataKey="value" fill="#34d399" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      ) : (
        <div className="flex items-center justify-center h-[220px] text-moat-text-muted text-sm text-center px-4">
          {metricLabel} isn&apos;t reported for this company&apos;s sector
        </div>
      )}
    </Card>
  );
}
