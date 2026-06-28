"use client";

import { useState, useEffect } from "react";
import { Card } from "./ui/Card";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";

interface PEChartProps {
  ticker: string;
  currentPE: number | null;
}

interface PEPoint {
  year: string;
  pe: number;
}

export function PEChart({ ticker, currentPE }: PEChartProps) {
  const [allData, setAllData] = useState<PEPoint[]>([]);
  const [period, setPeriod] = useState<"5y" | "all">("all");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`http://localhost:8000/financials/${ticker}`)
      .then((r) => r.json())
      .then((fin) => {
        const eps: { year: string; value: number }[] = fin.eps || [];
        if (eps.length === 0) {
          setAllData([]);
          return;
        }
        return fetch(
          `http://localhost:8000/price-history/${ticker}?period=max`
        )
          .then((r2) => r2.json())
          .then((ph) => {
            const priceByYear: Record<string, number> = {};
            for (let i = 0; i < ph.dates.length; i++) {
              const yr = ph.dates[i].substring(0, 4);
              priceByYear[yr] = ph.prices[i];
            }
            const points: PEPoint[] = [];
            for (const e of eps) {
              if (e.value > 0 && priceByYear[e.year]) {
                points.push({
                  year: e.year,
                  pe: Math.round((priceByYear[e.year] / e.value) * 10) / 10,
                });
              }
            }
            setAllData(points);
          });
      })
      .catch(() => setAllData([]))
      .finally(() => setLoading(false));
  }, [ticker]);

  const hasExtraHistory = allData.length > 5;
  const data = period === "5y" ? allData.slice(-5) : allData;

  const medianPE =
    data.length > 0
      ? (() => {
          const sorted = [...data.map((d) => d.pe)].sort((a, b) => a - b);
          const mid = Math.floor(sorted.length / 2);
          return sorted.length % 2 === 0
            ? (sorted[mid - 1] + sorted[mid]) / 2
            : sorted[mid];
        })()
      : null;

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
          Historical P/E Ratio
        </h2>
        <div className="flex items-center gap-3">
          {currentPE != null && (
            <div className="flex items-baseline gap-2">
              <span className="text-xs text-moat-text-muted">Current P/E</span>
              <span className="text-xl font-semibold font-mono text-moat-text">
                {currentPE.toFixed(1)}x
              </span>
            </div>
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
      {loading ? (
        <div className="flex items-center justify-center h-[200px] text-moat-text-muted animate-pulse">
          Loading...
        </div>
      ) : data.length > 0 ? (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data}>
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
              tick={{ fill: "#8b93a1", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
              width={40}
              tickFormatter={(v: number) => `${v}x`}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "#11161f",
                border: "1px solid #1f2733",
                borderRadius: "8px",
                color: "#e8eaed",
              }}
              formatter={(value: number) => [`${value.toFixed(1)}x`, "P/E"]}
            />
            {medianPE != null && (
              <ReferenceLine
                y={medianPE}
                stroke="#fbbf24"
                strokeDasharray="6 4"
                label={{
                  value: `Median ${medianPE.toFixed(1)}x`,
                  fill: "#fbbf24",
                  fontSize: 11,
                  position: "right",
                }}
              />
            )}
            <Line
              type="monotone"
              dataKey="pe"
              stroke="#34d399"
              strokeWidth={2}
              dot={{ fill: "#34d399", r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      ) : (
        <div className="flex items-center justify-center h-[200px] text-moat-text-muted">
          No P/E data available
        </div>
      )}
    </Card>
  );
}
