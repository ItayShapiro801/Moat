"use client";

import { useState, useEffect } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { TickerSearch } from "@/components/ui/TickerSearch";

interface CompareData {
  ticker: string;
  company_name: string;
  current_price: number;
  intrinsic_value: number | null;
  margin_of_safety_pct: number | null;
  f_score: number;
  pe_ratio: number | null;
  recommendation: string | null;
  target_low: number | null;
  target_mean: number | null;
  target_high: number | null;
  investors: { name: string; score: number | null; verdict: string | null }[];
}

function ratingVariant(rec: string | null): "success" | "warning" | "danger" | "neutral" {
  const r = (rec || "").toLowerCase();
  if (r.includes("buy")) return "success";
  if (r.includes("hold")) return "warning";
  if (r.includes("sell") || r.includes("underperform")) return "danger";
  return "neutral";
}

async function loadTicker(ticker: string): Promise<CompareData> {
  // analyze is the essential data — if it fails, treat the whole column as failed.
  const aRes = await fetch(`http://localhost:8000/analyze/${ticker}`);
  if (!aRes.ok) throw new Error("analyze failed");
  const a = await aRes.json();
  // Compare relies on DCF / F-Score / investor cards — none apply to ETFs,
  // crypto or indices. Reject non-equities (handles a typed-Enter bypass).
  if (a.quote_type && a.quote_type !== "EQUITY") throw new Error("NON_EQUITY");
  const [metricsRes, investorsRes] = await Promise.allSettled([
    fetch(`http://localhost:8000/metrics/${ticker}`).then((r) => r.json()),
    fetch(`http://localhost:8000/investors/${ticker}`).then((r) => r.json()),
  ]);
  const m = metricsRes.status === "fulfilled" ? metricsRes.value : {};
  const inv = investorsRes.status === "fulfilled" ? investorsRes.value.investors || [] : [];
  return {
    ticker,
    company_name: a.company_name || ticker,
    current_price: a.current_price ?? 0,
    intrinsic_value: a.intrinsic_value?.consensus ?? null,
    margin_of_safety_pct: a.margin_of_safety_pct ?? null,
    f_score: a.f_score ?? 0,
    pe_ratio: m.valuation?.pe_ratio ?? null,
    recommendation: m.analyst_ratings?.recommendation ?? null,
    target_low: m.analyst_ratings?.target_low_price ?? null,
    target_mean: m.analyst_ratings?.target_mean_price ?? null,
    target_high: m.analyst_ratings?.target_high_price ?? null,
    investors: inv.map((i: { name: string; score: number | null; verdict: string | null }) => ({
      name: i.name,
      score: i.score,
      verdict: i.verdict,
    })),
  };
}

const ALL_INVESTORS = [
  "Warren Buffett",
  "Charlie Munger",
  "Peter Lynch",
  "Michael Burry",
  "Bill Ackman",
  "Benjamin Graham",
];

type SlotState = CompareData | "loading" | "error" | "non_equity";

export default function ComparePage() {
  const [slots, setSlots] = useState<(string | null)[]>([null, null, null]);
  const [dataMap, setDataMap] = useState<Record<string, SlotState>>({});

  useEffect(() => {
    slots.forEach((t) => {
      if (t && !dataMap[t]) {
        setDataMap((prev) => ({ ...prev, [t]: "loading" }));
        loadTicker(t)
          .then((d) => setDataMap((prev) => ({ ...prev, [t]: d })))
          .catch((e) =>
            setDataMap((prev) => ({
              ...prev,
              [t]: e?.message === "NON_EQUITY" ? "non_equity" : "error",
            }))
          );
      }
    });
  }, [slots, dataMap]);

  function setSlot(i: number, ticker: string) {
    setSlots((prev) => {
      const next = [...prev];
      next[i] = ticker.toUpperCase();
      return next;
    });
  }
  function clearSlot(i: number) {
    setSlots((prev) => {
      const next = [...prev];
      next[i] = null;
      return next;
    });
  }

  // Columns aligned to filled slots; each carries the ticker (shown instantly)
  // and its load state.
  const cols = slots
    .map((t) => (t ? { ticker: t, state: dataMap[t] as SlotState | undefined } : null))
    .filter((c): c is { ticker: string; state: SlotState | undefined } => c != null);
  const hasCols = cols.length > 0;

  const Skeleton = () => (
    <span className="inline-block h-3 w-16 rounded bg-moat-border/60 animate-pulse" />
  );

  const metricRows: { label: string; render: (d: CompareData) => React.ReactNode }[] = [
    { label: "Current Price", render: (d) => `$${d.current_price.toFixed(2)}` },
    {
      label: "Intrinsic Value",
      render: (d) => (d.intrinsic_value != null ? `$${d.intrinsic_value.toFixed(2)}` : "N/A"),
    },
    {
      label: "Margin of Safety",
      render: (d) =>
        d.margin_of_safety_pct != null ? (
          <Badge variant={d.margin_of_safety_pct > 0 ? "success" : "danger"}>
            {d.margin_of_safety_pct > 0 ? "+" : ""}
            {d.margin_of_safety_pct.toFixed(1)}%
          </Badge>
        ) : (
          "N/A"
        ),
    },
    { label: "F-Score", render: (d) => `${d.f_score} / 9` },
    { label: "P/E Ratio", render: (d) => (d.pe_ratio != null ? `${d.pe_ratio}x` : "N/A") },
    {
      label: "Analyst Rating",
      render: (d) =>
        d.recommendation ? (
          <Badge variant={ratingVariant(d.recommendation)}>
            {d.recommendation.toUpperCase()}
          </Badge>
        ) : (
          "N/A"
        ),
    },
    {
      label: "Price Target (L / M / H)",
      render: (d) =>
        d.target_mean != null
          ? `$${d.target_low?.toFixed(0) ?? "—"} / $${d.target_mean.toFixed(0)} / $${d.target_high?.toFixed(0) ?? "—"}`
          : "N/A",
    },
  ];

  return (
    <div className="flex flex-col gap-6 px-4 py-8 mx-auto w-full max-w-5xl">
      <h1 className="text-3xl font-bold text-moat-text">Compare Stocks</h1>

      {/* Search inputs */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {slots.map((t, i) => (
          <Card key={i}>
            {t ? (
              <div className="flex items-center justify-between">
                <span className="font-mono font-semibold text-moat-accent">{t}</span>
                <button
                  onClick={() => clearSlot(i)}
                  className="text-xs text-moat-text-muted hover:text-moat-danger"
                >
                  Clear
                </button>
              </div>
            ) : (
              <TickerSearch
                placeholder={`Stock ${i + 1}…`}
                onSelect={(sym) => setSlot(i, sym)}
                equityOnly
              />
            )}
          </Card>
        ))}
      </div>

      {hasCols && (
        <>
          {/* Metric comparison */}
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-moat-border">
                    <th className="py-2 pr-4 text-left text-xs uppercase tracking-wider text-moat-text-muted font-medium">
                      Metric
                    </th>
                    {cols.map((c, i) => (
                      <th key={i} className="py-2 px-3 text-left">
                        {/* Ticker shows immediately on selection */}
                        <span className="text-moat-text font-semibold">{c.ticker}</span>
                        {c.state === "loading" && (
                          <span className="block text-[10px] font-normal text-moat-text-muted animate-pulse">
                            Loading {c.ticker}…
                          </span>
                        )}
                        {c.state === "error" && (
                          <span className="block text-[10px] font-normal text-moat-danger">
                            Couldn&apos;t load {c.ticker}
                          </span>
                        )}
                        {c.state === "non_equity" && (
                          <span className="block text-[10px] font-normal text-moat-warning">
                            Stocks only — no ETFs/crypto
                          </span>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {metricRows.map((row) => (
                    <tr key={row.label} className="border-b border-moat-border/40">
                      <td className="py-2.5 pr-4 text-moat-text-muted">{row.label}</td>
                      {cols.map((c, i) => (
                        <td key={i} className="py-2.5 px-3 font-mono text-moat-text">
                          {c.state === "loading" || c.state === undefined ? (
                            <Skeleton />
                          ) : c.state === "error" || c.state === "non_equity" ? (
                            <span className="text-moat-text-muted">—</span>
                          ) : (
                            row.render(c.state)
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Investor verdict comparison */}
          <Card>
            <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
              Investor Verdicts
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-moat-border">
                    <th className="py-2 pr-4 text-left text-xs uppercase tracking-wider text-moat-text-muted font-medium">
                      Investor
                    </th>
                    {cols.map((c, i) => (
                      <th key={i} className="py-2 px-3 text-left text-moat-text font-semibold">
                        {c.ticker}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {ALL_INVESTORS.map((name) => (
                    <tr key={name} className="border-b border-moat-border/40">
                      <td className="py-2.5 pr-4 text-moat-text-muted">{name}</td>
                      {cols.map((c, i) => {
                        if (c.state === "loading" || c.state === undefined)
                          return (
                            <td key={i} className="py-2.5 px-3">
                              <Skeleton />
                            </td>
                          );
                        if (c.state === "error" || c.state === "non_equity")
                          return (
                            <td key={i} className="py-2.5 px-3 text-moat-text-muted">—</td>
                          );
                        const inv = c.state.investors.find((x) => x.name === name);
                        return (
                          <td key={i} className="py-2.5 px-3">
                            {inv ? (
                              <span className="flex items-center gap-2">
                                <span className="font-mono text-moat-text">
                                  {inv.score != null ? inv.score.toFixed(1) : "—"}
                                </span>
                                <Badge variant={ratingVariant(inv.verdict)}>
                                  {inv.verdict || "—"}
                                </Badge>
                              </span>
                            ) : (
                              <span className="text-moat-text-muted">—</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
