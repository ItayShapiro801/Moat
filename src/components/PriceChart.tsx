"use client";

import { useState, useEffect } from "react";
import { API_BASE_URL } from "@/lib/api";
import { Card } from "./ui/Card";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

const PERIODS = [
  { label: "1M", value: "1mo" },
  { label: "3M", value: "3mo" },
  { label: "6M", value: "6mo" },
  { label: "1Y", value: "1y" },
  { label: "5Y", value: "5y" },
  { label: "All", value: "max" },
] as const;

interface PriceChartProps {
  ticker: string;
}

export function PriceChart({ ticker }: PriceChartProps) {
  const [period, setPeriod] = useState("1y");
  const [data, setData] = useState<{ date: string; price: number }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Guard against out-of-order responses: only the latest (ticker, period)
    // fetch is allowed to commit state. Fast period re-clicks or navigation
    // between tickers can otherwise resolve out of order and show stale data.
    let active = true;
    setLoading(true);
    fetch(`${API_BASE_URL}/price-history/${ticker}?period=${period}`)
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.json();
      })
      .then((d) => {
        if (!active) return;
        const points = d.dates.map((date: string, i: number) => ({
          date,
          price: d.prices[i],
        }));
        setData(points);
      })
      .catch(() => {
        if (active) setData([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [ticker, period]);

  const priceChange =
    data.length >= 2 ? data[data.length - 1].price - data[0].price : 0;
  const pctChange =
    data.length >= 2 && data[0].price
      ? (priceChange / data[0].price) * 100
      : 0;
  const isPositive = priceChange >= 0;
  const color = isPositive ? "#34d399" : "#f87171";

  return (
    <Card id="export-price-chart">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
          Price History
        </h2>
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                period === p.value
                  ? "bg-moat-accent text-moat-bg"
                  : "text-moat-text-muted hover:text-moat-text hover:bg-moat-surface-hover"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      {!loading && data.length > 0 && (
        <div className="mb-2 flex items-baseline gap-2">
          <span className="text-2xl font-semibold font-mono text-moat-text">
            ${data[data.length - 1].price.toFixed(2)}
          </span>
          <span
            className={`text-sm font-mono font-medium ${
              isPositive ? "text-moat-accent" : "text-moat-danger"
            }`}
          >
            {isPositive ? "+" : ""}
            {priceChange.toFixed(2)} ({isPositive ? "+" : ""}
            {pctChange.toFixed(1)}%)
          </span>
        </div>
      )}
      {loading ? (
        <div className="flex items-center justify-center h-[250px] text-moat-text-muted animate-pulse">
          Loading...
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={250}>
          <AreaChart data={data}>
            <defs>
              <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.2} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="#1f2733"
              vertical={false}
            />
            <XAxis
              dataKey="date"
              tick={{ fill: "#8b93a1", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              minTickGap={40}
              tickFormatter={(d: string) => {
                const dt = new Date(d);
                return period === "5y" || period === "max"
                  ? dt.getFullYear().toString()
                  : `${dt.getMonth() + 1}/${dt.getDate()}`;
              }}
            />
            <YAxis
              tick={{ fill: "#8b93a1", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={55}
              domain={["auto", "auto"]}
              tickFormatter={(v: number) => `$${v.toFixed(0)}`}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "#11161f",
                border: "1px solid #1f2733",
                borderRadius: "8px",
                color: "#e8eaed",
              }}
              formatter={(value: number) => [`$${value.toFixed(2)}`, "Price"]}
              labelFormatter={(label: string) =>
                new Date(label).toLocaleDateString()
              }
            />
            <Area
              type="monotone"
              dataKey="price"
              stroke={color}
              strokeWidth={1.5}
              fill="url(#priceGrad)"
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}
