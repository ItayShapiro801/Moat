"use client";

import { useState, useEffect, useCallback } from "react";
import { API_BASE_URL } from "@/lib/api";
import Link from "next/link";
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { Card } from "@/components/ui/Card";
import { TickerSearch } from "@/components/ui/TickerSearch";
import { PortfolioInsights } from "@/components/PortfolioInsights";
import { useAuth } from "@/lib/auth-context";
import { getSupabaseClient } from "@/lib/supabase/client";

interface Row {
  ticker: string;
  company_name: string;
  shares: number;
  amount_invested: number;
  avg_cost: number; // in USD
  current_price: number; // native currency
  current_value: number; // USD (converted)
  current_value_native: number; // native currency
  currency: string;
  fx_rate: number; // native -> USD
  quote_type: string;
  gain_loss: number; // USD
  gain_loss_pct: number;
  intrinsic_value: number | null;
  margin_of_safety_pct: number | null;
  f_score: number | null;
}

// Cache FX rates per currency for the lifetime of one load pass.
async function getFxRate(currency: string, cache: Record<string, number>): Promise<number> {
  if (currency === "USD") return 1;
  if (cache[currency] != null) return cache[currency];
  try {
    const r = await fetch(`${API_BASE_URL}/fx-rate?base=${currency}`).then((x) => x.json());
    const rate = r.rate_to_usd && r.rate_to_usd > 0 ? r.rate_to_usd : 1;
    cache[currency] = rate;
    return rate;
  } catch {
    return 1;
  }
}

const fmtNative = (n: number, ccy: string) =>
  `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${ccy}`;

const DONUT_COLORS = [
  "#34d399", "#60a5fa", "#fbbf24", "#f87171", "#a78bfa",
  "#22d3ee", "#fb923c", "#4ade80", "#e879f9", "#94a3b8",
];

const fmt$ = (n: number) =>
  `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function PortfolioPage() {
  const { user, loading: authLoading, openAuth } = useAuth();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [pendingTicker, setPendingTicker] = useState<string | null>(null);
  const [addAmount, setAddAmount] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const supabase = getSupabaseClient();
    if (!supabase || !user) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const { data: holdings } = await supabase
      .from("portfolio_holdings")
      .select("ticker, shares, amount_invested, price_at_purchase, currency, quote_type")
      .eq("user_id", user.id)
      .order("added_at", { ascending: true });

    const fxCache: Record<string, number> = {};
    const results = await Promise.all(
      (holdings || []).map(async (h) => {
        const ticker = h.ticker as string;
        const shares = Number(h.shares) || 0;
        const amount_invested = Number(h.amount_invested) || 0;
        const avg_cost = shares > 0 ? amount_invested / shares : 0; // USD
        const currency = (h.currency as string) || "USD";
        const quote_type = (h.quote_type as string) || "EQUITY";
        try {
          const res = await fetch(`${API_BASE_URL}/analyze/${ticker}`);
          if (!res.ok) throw new Error();
          const d = await res.json();
          const current_price = d.current_price || 0; // native currency
          const ccy = d.currency || currency;
          const rate = await getFxRate(ccy, fxCache); // native -> USD
          const current_value_native = shares * current_price;
          const current_value = current_value_native * rate; // USD
          const gain_loss = current_value - amount_invested;
          const gain_loss_pct = amount_invested > 0 ? (gain_loss / amount_invested) * 100 : 0;
          return {
            ticker,
            company_name: d.company_name,
            shares,
            amount_invested,
            avg_cost,
            current_price,
            current_value,
            current_value_native,
            currency: ccy,
            fx_rate: rate,
            quote_type: d.quote_type || quote_type,
            gain_loss,
            gain_loss_pct,
            intrinsic_value: d.intrinsic_value?.consensus ?? null,
            margin_of_safety_pct: d.margin_of_safety_pct ?? null,
            f_score: d.f_score ?? null,
          } as Row;
        } catch {
          return {
            ticker,
            company_name: "—",
            shares,
            amount_invested,
            avg_cost,
            current_price: 0,
            current_value: 0,
            current_value_native: 0,
            currency,
            fx_rate: 1,
            quote_type,
            gain_loss: -amount_invested,
            gain_loss_pct: -100,
            intrinsic_value: null,
            margin_of_safety_pct: null,
            f_score: null,
          } as Row;
        }
      })
    );
    setRows(results);
    setLoading(false);
  }, [user]);

  useEffect(() => {
    if (!authLoading) load();
  }, [authLoading, load]);

  async function addHolding() {
    const supabase = getSupabaseClient();
    if (!supabase || !user || !pendingTicker) return;
    const amt = parseFloat(addAmount);
    if (!amt || amt <= 0) {
      setAddError("Enter a positive dollar amount.");
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/analyze/${pendingTicker}`);
      if (!res.ok) throw new Error();
      const d = await res.json();
      const nativePrice = d.current_price;
      if (!nativePrice || nativePrice <= 0) throw new Error();
      const currency = d.currency || "USD";
      let rate = 1;
      if (currency !== "USD") {
        try {
          const fx = await fetch(`${API_BASE_URL}/fx-rate?base=${currency}`).then((r) => r.json());
          if (fx.rate_to_usd && fx.rate_to_usd > 0) rate = fx.rate_to_usd;
        } catch {
          /* 1:1 fallback */
        }
      }
      const priceUsd = nativePrice * rate;
      const newShares = amt / priceUsd;

      const existing = rows.find((r) => r.ticker === pendingTicker);
      let dbError = null;
      if (existing) {
        // Weighted-average add-more
        const totalShares = existing.shares + newShares;
        const totalInvested = existing.amount_invested + amt;
        const { error } = await supabase
          .from("portfolio_holdings")
          .update({
            shares: totalShares,
            amount_invested: totalInvested,
            price_at_purchase: totalInvested / totalShares,
          })
          .eq("user_id", user.id)
          .eq("ticker", pendingTicker);
        dbError = error;
      } else {
        // Some portfolio_holdings schemas don't have a quote_type column; retry
        // without it if the first insert is rejected for that reason, so adding a
        // holding never silently fails on a schema mismatch.
        const base = {
          user_id: user.id,
          ticker: pendingTicker,
          amount_invested: amt,
          price_at_purchase: priceUsd,
          shares: newShares,
          currency,
        };
        let { error } = await supabase
          .from("portfolio_holdings")
          .upsert({ ...base, quote_type: d.quote_type || "EQUITY" }, { onConflict: "user_id,ticker" });
        if (error && /quote_type|column/i.test(error.message || "")) {
          ({ error } = await supabase
            .from("portfolio_holdings")
            .upsert(base, { onConflict: "user_id,ticker" }));
        }
        dbError = error;
      }
      if (dbError) {
        setAddError(`Couldn't add: ${dbError.message}`);
        return;
      }
      setPendingTicker(null);
      setAddAmount("");
      await load();
    } catch {
      setAddError("Couldn't add. Check the ticker and try again.");
    } finally {
      setAdding(false);
    }
  }

  async function remove(ticker: string) {
    const supabase = getSupabaseClient();
    if (!supabase || !user) return;
    await supabase
      .from("portfolio_holdings")
      .delete()
      .eq("user_id", user.id)
      .eq("ticker", ticker);
    setRows((prev) => prev.filter((r) => r.ticker !== ticker));
  }

  if (!authLoading && !user) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-4 py-20">
        <p className="text-lg text-moat-text">Log in to view your portfolio</p>
        <button
          onClick={() => openAuth("login")}
          className="px-4 py-2 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
        >
          Log in
        </button>
      </div>
    );
  }

  const totalValue = rows.reduce((s, r) => s + r.current_value, 0);
  const totalInvested = rows.reduce((s, r) => s + r.amount_invested, 0);
  const totalGain = totalValue - totalInvested;
  const totalGainPct = totalInvested > 0 ? (totalGain / totalInvested) * 100 : 0;

  const donutData = rows
    .filter((r) => r.current_value > 0)
    .map((r) => ({ name: r.ticker, value: r.current_value }));

  const insightsHoldings = rows.map((r) => ({
    ticker: r.ticker,
    quote_type: r.quote_type,
    allocation_pct: totalValue > 0 ? (r.current_value / totalValue) * 100 : 0,
    current_price: r.current_price,
    intrinsic_value: r.intrinsic_value,
    margin_of_safety_pct: r.margin_of_safety_pct,
    f_score: r.f_score,
    gain_loss_pct: r.gain_loss_pct,
  }));

  // Stable hash of holdings: changes only when a holding is added/removed/resized,
  // which invalidates cached insights and re-prompts generation.
  const holdingsHash = rows
    .map((r) => `${r.ticker}:${r.amount_invested}:${r.shares}`)
    .sort()
    .join("|");

  return (
    <div className="flex flex-col gap-6 px-4 py-8 mx-auto w-full max-w-5xl">
      <h1 className="text-3xl font-bold text-moat-text">Your Portfolio</h1>

      {/* Summary */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
              Total Value
            </span>
            <span className="text-3xl font-semibold font-mono text-moat-text">
              {fmt$(totalValue)}
            </span>
          </div>
        </Card>
        <Card>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
              Total Invested
            </span>
            <span className="text-3xl font-semibold font-mono text-moat-text">
              {fmt$(totalInvested)}
            </span>
          </div>
        </Card>
        <Card>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
              Total Gain / Loss
            </span>
            <span
              className={`text-3xl font-semibold font-mono ${
                totalGain >= 0 ? "text-moat-accent" : "text-moat-danger"
              }`}
            >
              {totalGain >= 0 ? "+" : ""}
              {fmt$(totalGain)}
            </span>
            <span
              className={`text-sm font-mono ${
                totalGain >= 0 ? "text-moat-accent" : "text-moat-danger"
              }`}
            >
              {totalGain >= 0 ? "+" : ""}
              {totalGainPct.toFixed(2)}%
            </span>
          </div>
        </Card>
      </div>

      {/* Add a holding inline */}
      <Card>
        <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
          Add a Holding
        </h2>
        {!pendingTicker ? (
          <TickerSearch
            placeholder="Search a stock, ETF or crypto (e.g. VOO, BTC-USD)…"
            onSelect={(sym) => {
              setPendingTicker(sym.toUpperCase());
              setAddError(null);
            }}
          />
        ) : (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <span className="font-mono font-semibold text-moat-accent">{pendingTicker}</span>
            <div className="flex items-center gap-2 flex-1">
              <span className="text-moat-text-muted">$</span>
              <input
                type="number"
                autoFocus
                value={addAmount}
                onChange={(e) => setAddAmount(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addHolding()}
                placeholder="How much are you investing? (USD)"
                className="flex-1 rounded-md border border-moat-border bg-moat-bg px-3 py-1.5 text-moat-text placeholder:text-moat-text-muted text-sm focus:outline-none focus:ring-2 focus:ring-moat-accent"
              />
            </div>
            <div className="flex gap-2">
              <button
                onClick={addHolding}
                disabled={adding}
                className="px-4 py-1.5 rounded-md bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors disabled:opacity-50"
              >
                {adding ? "Adding…" : "Add"}
              </button>
              <button
                onClick={() => {
                  setPendingTicker(null);
                  setAddAmount("");
                  setAddError(null);
                }}
                className="px-3 py-1.5 rounded-md border border-moat-border text-moat-text-muted text-sm hover:bg-moat-surface-hover transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {addError && <p className="mt-2 text-xs text-moat-danger">{addError}</p>}
      </Card>

      {/* Key Insights (on-demand) */}
      {!loading && rows.length > 0 && (
        <PortfolioInsights
          holdings={insightsHoldings}
          totalValue={totalValue}
          totalGainPct={totalGainPct}
          holdingsHash={holdingsHash}
          userId={user?.id ?? null}
        />
      )}

      {loading ? (
        <Card>
          <div className="flex justify-center py-10 text-moat-text-muted animate-pulse">
            Loading your holdings…
          </div>
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center gap-3 py-10">
            <p className="text-moat-text-muted">Your portfolio is empty.</p>
            <Link href="/" className="text-sm text-moat-accent hover:underline">
              Search a stock to add one
            </Link>
          </div>
        </Card>
      ) : (
        <>
          {/* Allocation donut */}
          {donutData.length > 0 && (
            <Card>
              <h2 className="mb-2 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                Allocation
              </h2>
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie
                    data={donutData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={100}
                    paddingAngle={2}
                  >
                    {donutData.map((_, i) => (
                      <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#11161f",
                      border: "1px solid #1f2733",
                      borderRadius: "8px",
                      color: "#e8eaed",
                    }}
                    formatter={(v: number) => [
                      `${fmt$(v)} (${((v / totalValue) * 100).toFixed(1)}%)`,
                      "Value",
                    ]}
                  />
                  <Legend wrapperStyle={{ fontSize: 12, color: "#8b93a1" }} />
                </PieChart>
              </ResponsiveContainer>
            </Card>
          )}

          {/* Holdings table */}
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wider text-moat-text-muted border-b border-moat-border">
                    <th className="py-2 pr-4 font-medium">Ticker</th>
                    <th className="py-2 pr-4 font-medium text-right">Shares</th>
                    <th className="py-2 pr-4 font-medium text-right">Avg Cost</th>
                    <th className="py-2 pr-4 font-medium text-right">Price</th>
                    <th className="py-2 pr-4 font-medium text-right">Value</th>
                    <th className="py-2 pr-4 font-medium text-right">Gain / Loss</th>
                    <th className="py-2 pr-4 font-medium text-right">Alloc</th>
                    <th className="py-2 font-medium"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const alloc = totalValue > 0 ? (r.current_value / totalValue) * 100 : 0;
                    const up = r.gain_loss >= 0;
                    return (
                      <tr
                        key={r.ticker}
                        className="border-b border-moat-border/40 hover:bg-moat-surface-hover transition-colors"
                      >
                        <td className="py-2.5 pr-4">
                          <Link
                            href={`/analyze/${r.ticker}`}
                            className="font-mono font-semibold text-moat-accent hover:underline"
                          >
                            {r.ticker}
                          </Link>
                          {r.quote_type && r.quote_type !== "EQUITY" && (
                            <span className="ml-2 rounded bg-moat-surface-hover px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-moat-text-muted">
                              {r.quote_type === "CRYPTOCURRENCY" ? "Crypto" : r.quote_type}
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 pr-4 text-right font-mono text-moat-text-muted">
                          {r.shares.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                        </td>
                        <td className="py-2.5 pr-4 text-right font-mono text-moat-text-muted">
                          {fmt$(r.avg_cost)}
                        </td>
                        <td className="py-2.5 pr-4 text-right font-mono text-moat-text">
                          {r.currency !== "USD" && (
                            <span className="block text-xs text-moat-text-muted">
                              {fmtNative(r.current_price, r.currency)}
                            </span>
                          )}
                          {fmt$(r.current_price * r.fx_rate)}
                        </td>
                        <td className="py-2.5 pr-4 text-right font-mono text-moat-text">
                          {r.currency !== "USD" && (
                            <span className="block text-xs text-moat-text-muted">
                              {fmtNative(r.current_value_native, r.currency)}
                            </span>
                          )}
                          {fmt$(r.current_value)}
                        </td>
                        <td className={`py-2.5 pr-4 text-right font-mono ${up ? "text-moat-accent" : "text-moat-danger"}`}>
                          {up ? "+" : ""}
                          {fmt$(r.gain_loss)}
                          <span className="block text-xs">
                            {up ? "+" : ""}
                            {r.gain_loss_pct.toFixed(2)}%
                          </span>
                        </td>
                        <td className="py-2.5 pr-4 text-right font-mono text-moat-text-muted">
                          {alloc.toFixed(1)}%
                        </td>
                        <td className="py-2.5 text-right">
                          <button
                            onClick={() => remove(r.ticker)}
                            className="text-xs text-moat-text-muted hover:text-moat-danger transition-colors"
                          >
                            Remove
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
