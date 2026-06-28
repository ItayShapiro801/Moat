"use client";

import { useState, useEffect, useRef } from "react";
import { API_BASE_URL } from "@/lib/api";
import { useRouter } from "next/navigation";
import { Button } from "./Button";

interface SearchResult {
  symbol: string;
  name: string;
  quote_type?: string;
}

interface TickerSearchProps {
  placeholder?: string;
  // When provided, selecting a ticker calls this instead of navigating to /analyze.
  onSelect?: (symbol: string) => void;
  // Compare-only: restrict results to individual stocks (EQUITY).
  equityOnly?: boolean;
}

export function TickerSearch({
  placeholder = "Search by ticker or company name...",
  onSelect,
  equityOnly = false,
}: TickerSearchProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node))
        setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function navigate(symbol: string) {
    setOpen(false);
    setQuery("");
    if (onSelect) {
      onSelect(symbol);
    } else {
      router.push(`/analyze/${symbol}`);
    }
  }

  function handleChange(value: string) {
    setQuery(value);
    setHighlighted(-1);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!value.trim()) {
      setResults([]);
      setOpen(false);
      return;
    }
    debounceRef.current = setTimeout(() => {
      const eq = equityOnly ? "&equity_only=true" : "";
      fetch(`${API_BASE_URL}/search?q=${encodeURIComponent(value.trim())}${eq}`)
        .then((r) => r.json())
        .then((data: SearchResult[]) => {
          setResults(data);
          setOpen(data.length > 0);
        })
        .catch(() => {});
    }, 300);
  }

  function bestSymbol(): string | null {
    if (highlighted >= 0 && results[highlighted]) return results[highlighted].symbol;
    if (results.length > 0) return results[0].symbol;
    const trimmed = query.trim().toUpperCase();
    return trimmed || null;
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (!open || results.length === 0) {
      if (e.key === "Enter") {
        e.preventDefault();
        const sym = bestSymbol();
        if (sym) navigate(sym);
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((h) => (h < results.length - 1 ? h + 1 : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((h) => (h > 0 ? h - 1 : results.length - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const sym = bestSymbol();
      if (sym) navigate(sym);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const sym = bestSymbol();
    if (sym) navigate(sym);
  }

  return (
    <div ref={wrapperRef} className="relative">
      <form onSubmit={handleSubmit} className="flex gap-3">
        <input
          type="text"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder={placeholder}
          className="flex-1 rounded-lg border border-moat-border bg-moat-bg px-4 py-2 text-moat-text placeholder:text-moat-text-muted focus:outline-none focus:ring-2 focus:ring-moat-accent font-mono"
        />
        {!onSelect && <Button type="submit">Analyze</Button>}
      </form>
      {open && results.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full rounded-lg border border-moat-border bg-moat-surface shadow-lg overflow-hidden">
          {results.map((r, i) => (
            <li key={r.symbol}>
              <button
                type="button"
                onMouseDown={() => navigate(r.symbol)}
                onMouseEnter={() => setHighlighted(i)}
                className={`w-full px-4 py-2.5 text-left flex items-center gap-3 transition-colors ${
                  i === highlighted
                    ? "bg-moat-surface-hover"
                    : "hover:bg-moat-surface-hover"
                }`}
              >
                <span className="font-mono font-semibold text-moat-accent text-sm">
                  {r.symbol}
                </span>
                <span className="text-moat-text-muted text-sm truncate flex-1">
                  {r.name}
                </span>
                {r.quote_type && r.quote_type !== "EQUITY" && (
                  <span className="rounded bg-moat-surface px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-moat-text-muted shrink-0">
                    {r.quote_type === "CRYPTOCURRENCY" ? "Crypto" : r.quote_type}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
