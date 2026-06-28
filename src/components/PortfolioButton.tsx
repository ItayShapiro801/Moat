"use client";

import { useState, useEffect } from "react";
import { useAuth } from "@/lib/auth-context";
import { getSupabaseClient } from "@/lib/supabase/client";

interface Holding {
  shares: number | null;
  amount_invested: number | null;
}

export function PortfolioButton({ ticker }: { ticker: string }) {
  const { user, openAuth } = useAuth();
  const [holding, setHolding] = useState<Holding | null>(null);
  const [checked, setChecked] = useState(false);
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inPortfolio = holding != null;

  useEffect(() => {
    setChecked(false);
    if (!user) {
      setHolding(null);
      return;
    }
    const supabase = getSupabaseClient();
    if (!supabase) return;
    supabase
      .from("portfolio_holdings")
      .select("shares, amount_invested")
      .eq("user_id", user.id)
      .eq("ticker", ticker)
      .maybeSingle()
      .then(({ data }) => {
        setHolding(data ? (data as Holding) : null);
        setChecked(true);
      });
  }, [user, ticker]);

  function handleClick() {
    if (!user) {
      openAuth("login");
      return;
    }
    setError(null);
    setAmount("");
    setOpen((o) => !o);
  }

  async function submit() {
    const supabase = getSupabaseClient();
    if (!supabase || !user) return;
    const addAmount = parseFloat(amount);
    if (!addAmount || addAmount <= 0) {
      setError("Enter a positive dollar amount.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Current price now
      const res = await fetch(`http://localhost:8000/analyze/${ticker}`);
      if (!res.ok) throw new Error();
      const d = await res.json();
      const nativePrice = d.current_price;
      if (!nativePrice || nativePrice <= 0) throw new Error();
      const currency = d.currency || "USD";
      // amount_invested is in USD, but the price is in the asset's native
      // currency — convert to USD so shares & avg cost stay USD-consistent.
      let rate = 1;
      if (currency !== "USD") {
        try {
          const fx = await fetch(`http://localhost:8000/fx-rate?base=${currency}`).then((r) => r.json());
          if (fx.rate_to_usd && fx.rate_to_usd > 0) rate = fx.rate_to_usd;
        } catch {
          /* fall back to 1:1 */
        }
      }
      const price = nativePrice * rate; // USD per share
      const newShares = addAmount / price;

      if (holding && holding.shares != null && holding.amount_invested != null) {
        // Add more — weighted average
        const totalShares = holding.shares + newShares;
        const totalInvested = holding.amount_invested + addAmount;
        const avgCost = totalInvested / totalShares;
        await supabase
          .from("portfolio_holdings")
          .update({
            shares: totalShares,
            amount_invested: totalInvested,
            price_at_purchase: avgCost,
          })
          .eq("user_id", user.id)
          .eq("ticker", ticker);
        setHolding({ shares: totalShares, amount_invested: totalInvested });
      } else {
        await supabase.from("portfolio_holdings").upsert(
          {
            user_id: user.id,
            ticker,
            amount_invested: addAmount,
            price_at_purchase: price,
            shares: newShares,
            currency: d.currency || "USD",
            quote_type: d.quote_type || "EQUITY",
          },
          { onConflict: "user_id,ticker" }
        );
        setHolding({ shares: newShares, amount_invested: addAmount });
      }
      setOpen(false);
    } catch {
      setError("Couldn't add to portfolio. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    const supabase = getSupabaseClient();
    if (!supabase || !user) return;
    setBusy(true);
    try {
      await supabase
        .from("portfolio_holdings")
        .delete()
        .eq("user_id", user.id)
        .eq("ticker", ticker);
      setHolding(null);
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  const label = inPortfolio ? "✓ In Portfolio" : "+ Add to Portfolio";

  return (
    <div className="relative">
      <button
        onClick={handleClick}
        disabled={Boolean(user) && !checked}
        className={`px-3 py-1 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${
          inPortfolio
            ? "bg-moat-accent/15 text-moat-accent border border-moat-accent/30 hover:bg-moat-accent/25"
            : "bg-moat-accent text-moat-bg hover:bg-moat-accent/90"
        }`}
      >
        {label}
      </button>

      {open && user && (
        <div className="absolute right-0 mt-2 w-64 z-50 rounded-lg border border-moat-border bg-moat-surface p-3 shadow-lg flex flex-col gap-2">
          <span className="text-xs text-moat-text-muted">
            {inPortfolio
              ? `Add more to your ${ticker} position`
              : `How much are you investing in ${ticker}?`}
          </span>
          <div className="flex items-center gap-1">
            <span className="text-moat-text-muted">$</span>
            <input
              type="number"
              min="0"
              autoFocus
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="0"
              className="flex-1 rounded-md border border-moat-border bg-moat-bg px-2 py-1.5 text-moat-text text-sm font-mono focus:outline-none focus:ring-2 focus:ring-moat-accent"
            />
          </div>
          {error && <span className="text-xs text-moat-danger">{error}</span>}
          <button
            onClick={submit}
            disabled={busy}
            className="w-full py-2 rounded-md bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors disabled:opacity-50"
          >
            {busy ? "Saving…" : inPortfolio ? "Add more" : "Add to Portfolio"}
          </button>
          {inPortfolio && (
            <button
              onClick={remove}
              disabled={busy}
              className="w-full py-1.5 rounded-md text-xs text-moat-text-muted hover:text-moat-danger transition-colors"
            >
              Remove from portfolio
            </button>
          )}
        </div>
      )}
    </div>
  );
}
