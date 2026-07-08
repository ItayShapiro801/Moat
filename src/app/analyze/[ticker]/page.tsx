"use client";

import { use, useState, useEffect } from "react";
import { API_BASE_URL } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { StatBlock } from "@/components/ui/StatBlock";
import { Badge } from "@/components/ui/Badge";
import { Gauge } from "@/components/ui/Gauge";
import { TickerSearch } from "@/components/ui/TickerSearch";
import { PriceChart } from "@/components/PriceChart";
import { FinancialTrends } from "@/components/FinancialTrends";
import { PEChart } from "@/components/PEChart";
import { KeyMetrics } from "@/components/KeyMetrics";
import { InvestorCards } from "@/components/InvestorCards";
import { ValuationReview } from "@/components/ValuationReview";
import { InsiderTrades } from "@/components/InsiderTrades";
import { InstitutionalHoldings } from "@/components/InstitutionalHoldings";
import { PortfolioButton } from "@/components/PortfolioButton";
import { ExportReport } from "@/components/ExportReport";
import { CompanyThesis } from "@/components/CompanyThesis";
import { DeepResearchButton } from "@/components/DeepResearchReport";

interface Scenario {
  value: number | null;
  growth: number;
  discount_rate: number;
  enterprise_value: number;
  equity_value: number;
}

interface EtfInfo {
  category: string | null;
  expense_ratio: number | null;
  total_assets: number | null;
  summary: string | null;
}

interface AnalysisData {
  ticker: string;
  company_name: string;
  current_price: number;
  quote_type: string;
  currency: string;
  etf_info?: EtfInfo | null;
  // Present only when the backend served degraded data via the FMP fallback
  // (yfinance rate-limited). In that mode valuation fields are legitimately null.
  data_source?: string | null;
  // Set when the primary (FMP) data budget is spent and the valuation came from
  // the free SEC-EDGAR + Finnhub backup — a good estimate, but flagged so the UI
  // is honest that live data is temporarily limited.
  data_limited?: boolean;
  data_limited_note?: string | null;
  intrinsic_value: {
    bear: Scenario;
    base: Scenario;
    bull: Scenario;
    consensus: number | null;
    partial: boolean;
  };
  margin_of_safety_pct: number | null;
  // confidence and f_score are null in fallback mode, but they are only ever
  // read in the full-data (valuation_breakdown present) branch, so they are
  // typed non-null to reflect that invariant and satisfy downstream consumers.
  confidence: "high" | "medium" | "low";
  valuation_note: string | null;
  f_score: number;
  revenue_5yr: number[];
  fcf_5yr: number[];
  valuation_breakdown: {
    internal_dcf: number | null;
    dcf_excluded: boolean;
    external_dcf: number | null;
    relative_value: number | null;
    earnings_multiple?: number | null;
    blend_weights: { dcf: number; external: number; multiples: number };
    // Actual equal weights of the sources averaged into the consensus, keyed by
    // source name (internal_dcf | external_dcf | relative_value | earnings_multiple).
    consensus_weights?: Record<string, number>;
    adjustments_applied: string[];
    source_mismatch_warning: boolean;
  };
  dcf_breakdown: {
    wacc: number;
    terminal_growth: number;
    growth_rate: number;
    growth_source: string;
    sector: string;
    enterprise_value: number;
    equity_value: number;
    net_debt: number;
  };
  // Moat Valuation Engine: quality-driven CAP horizon, reverse-DCF implied
  // growth, and a Monte Carlo fair-value distribution.
  valuation_engine?: {
    method?: "fcf_dcf" | "excess_return";
    moat_score: number;
    moat_components?: Record<string, number>;
    cap_years: number;
    growth_decay?: number;
    expected_growth: number | null;
    implied_growth: number | null;
    monte_carlo: { p10: number; p25: number; p50: number; p75: number; p90: number } | null;
    dcf_reliable?: boolean;
  } | null;
}

interface MetricsValuation {
  pe_ratio: number | null;
}

const formatB = (val: number) => {
  const abs = Math.abs(val);
  if (abs >= 1e12) return `$${(val / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(val / 1e6).toFixed(0)}M`;
  return `$${val.toLocaleString()}`;
};

const confidenceColor = {
  high: "text-moat-accent",
  medium: "text-moat-warning",
  low: "text-moat-danger",
};

// Human-readable labels for the model-adjustment codes the backend reports —
// raw snake_case internals (e.g. "cyclical_avg_fcf") look broken to visitors.
const ADJUSTMENT_LABELS: Record<string, string> = {
  sector_excluded_dcf: "DCF not applicable for this sector",
  excess_return_model: "valued on excess returns (justified P/B)",
  cyclical_avg_fcf: "cyclical cash flow — multi-year average used",
  hyper_capex: "capex surge normalized",
  multiples_unreliable: "historical multiples de-emphasized",
};
const adjustmentLabel = (code: string) =>
  ADJUSTMENT_LABELS[code] ?? code.replace(/_/g, " ");

export default function AnalyzePage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = use(params);
  const [data, setData] = useState<AnalysisData | null>(null);
  const [currentPE, setCurrentPE] = useState<number | null>(null);
  // null = no error. 404 = genuinely not found. Any other status / network
  // failure = a transient problem (e.g. data provider rate-limited), which is
  // NOT the same as "not found".
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setErrorStatus(null);
    setData(null);
    fetch(`${API_BASE_URL}/analyze/${ticker}`)
      .then(async (res) => {
        if (!res.ok) {
          // A 200 (including degraded fmp_fallback data) is success; only a
          // non-2xx is an error. Remember the status to tailor the message.
          const status = res.status;
          setErrorStatus(status);
          throw new Error(`HTTP ${status}`);
        }
        return res.json();
      })
      .then(setData)
      .catch(() => {
        // Network/parse failures have no HTTP status; treat as transient (-1).
        setErrorStatus((s) => (s == null ? -1 : s));
      })
      .finally(() => setLoading(false));

    fetch(`${API_BASE_URL}/metrics/${ticker}`)
      .then((r) => r.json())
      .then((m: { valuation: MetricsValuation }) =>
        setCurrentPE(m.valuation.pe_ratio)
      )
      .catch(() => {});
  }, [ticker]);

  const baseValue = data?.intrinsic_value.base.value ?? null;
  const hasFV = baseValue != null;
  // Degraded mode: backend served a valid quote via FMP because yfinance was
  // rate-limited. Valuation/F-Score/investor sections are intentionally absent.
  const isFallback = data?.data_source === "fmp_fallback";

  return (
    <div className="flex flex-col gap-6 px-4 py-8 mx-auto w-full max-w-5xl">
      <Card>
        <TickerSearch placeholder="Search another ticker..." />
      </Card>

      {loading && (
        <div className="flex justify-center py-20">
          <p className="text-moat-text-muted text-lg animate-pulse">
            Analyzing {ticker.toUpperCase()}...
          </p>
        </div>
      )}

      {/* "Couldn't find" ONLY for a genuine 404. */}
      {!loading && errorStatus === 404 && (
        <Card>
          <div className="flex flex-col items-center gap-3 py-4">
            <p className="text-moat-danger text-lg font-medium">
              Couldn&apos;t find &ldquo;{ticker.toUpperCase()}&rdquo;
            </p>
            <p className="text-moat-text-muted text-sm">
              Try searching by company name instead
            </p>
            <button
              onClick={() => {
                document.querySelector<HTMLInputElement>("input[type=text]")?.focus();
              }}
              className="mt-1 px-4 py-2 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
            >
              Back to search
            </button>
          </div>
        </Card>
      )}

      {/* Any other failure (rate-limit with no fallback data, network, 5xx) is
          transient — not "not found". */}
      {!loading && errorStatus != null && errorStatus !== 404 && (
        <Card>
          <div className="flex flex-col items-center gap-3 py-4">
            <p className="text-moat-warning text-lg font-medium">
              {ticker.toUpperCase()} is temporarily unavailable
            </p>
            <p className="text-moat-text-muted text-sm text-center max-w-md">
              The market-data provider is rate-limited right now. Please try again in
              a moment.
            </p>
            <button
              onClick={() => window.location.reload()}
              className="mt-1 px-4 py-2 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
            >
              Retry
            </button>
          </div>
        </Card>
      )}

      {/* Calm "limited data" banner for degraded (FMP fallback) responses. */}
      {!loading && data && isFallback && (
        <div className="rounded-lg border border-moat-warning/40 bg-moat-warning/10 px-4 py-3">
          <p className="text-sm text-moat-warning">
            <span className="font-medium">Limited data mode</span>
            {" — "}
            {data.valuation_note ||
              "some valuation features are temporarily unavailable."}
          </p>
        </div>
      )}

      {/* Non-operating assets (ETF / crypto / index): price + chart only.
          Intrinsic value, F-Score and investor analysis do not apply. */}
      {data && !loading && !data.valuation_breakdown && (
        <>
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-3xl font-bold text-moat-text">{data.company_name || data.ticker}</h1>
            <Badge variant="neutral">{data.ticker}</Badge>
            {/* Asset-class badge applies to genuine ETF/crypto/index, not to an
                equity that merely fell back to degraded data. */}
            {!isFallback && (
              <Badge variant="warning">
                {data.quote_type === "CRYPTOCURRENCY"
                  ? "Crypto"
                  : data.quote_type === "ETF"
                  ? "ETF"
                  : data.quote_type === "INDEX"
                  ? "Index"
                  : data.quote_type}
              </Badge>
            )}
            <div className="ml-auto">
              <PortfolioButton ticker={data.ticker} />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card>
              <StatBlock
                label="Current Price"
                value={`${data.current_price?.toFixed(2) ?? "—"} ${data.currency}`}
              />
            </Card>
            {data.etf_info && (
              <Card>
                <div className="grid grid-cols-2 gap-3">
                  {data.etf_info.category && (
                    <StatBlock label="Category" value={data.etf_info.category} />
                  )}
                  {data.etf_info.expense_ratio != null && (
                    <StatBlock
                      label="Expense Ratio"
                      value={`${data.etf_info.expense_ratio.toFixed(2)}%`}
                    />
                  )}
                  {data.etf_info.total_assets != null && (
                    <StatBlock label="Total Assets" value={formatB(data.etf_info.total_assets)} />
                  )}
                </div>
              </Card>
            )}
          </div>

          {/* In fallback mode the note is already shown in the banner above. */}
          {data.valuation_note && !isFallback && (
            <Card>
              <p className="text-sm text-moat-text-muted">{data.valuation_note}</p>
            </Card>
          )}

          {data.etf_info?.summary && (
            <Card>
              <h2 className="mb-2 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                About
              </h2>
              <p className="text-sm text-moat-text-muted leading-relaxed">
                {data.etf_info.summary}
              </p>
            </Card>
          )}

          <PriceChart ticker={data.ticker} />
        </>
      )}

      {data && !loading && data.valuation_breakdown && (
        <>
          {data.data_limited && (
            <div className="flex items-start gap-3 rounded-lg border border-moat-warning/40 bg-moat-warning/10 px-4 py-3">
              <span className="text-moat-warning text-lg leading-none mt-0.5">⚠</span>
              <p className="text-sm text-moat-text-muted">
                {data.data_limited_note ||
                  "Live market-data limit reached for today. This estimate is built from SEC filings and may be less precise than usual. Full data resumes tomorrow."}
              </p>
            </div>
          )}
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-3xl font-bold text-moat-text">
              {data.company_name || data.ticker}
            </h1>
            <Badge variant="neutral">{data.ticker}</Badge>
            {hasFV ? (
              <Badge variant={data.margin_of_safety_pct! > 0 ? "success" : "danger"}>
                {data.margin_of_safety_pct! > 0 ? "Undervalued" : "Overvalued"}
              </Badge>
            ) : (
              <Badge variant="warning">N/A</Badge>
            )}
            <Badge variant={data.confidence === "high" ? "success" : data.confidence === "medium" ? "warning" : "danger"}>
              {data.confidence} confidence
            </Badge>
            <div className="ml-auto flex items-center gap-2 flex-wrap">
              <PortfolioButton ticker={data.ticker} />
              <DeepResearchButton ticker={data.ticker} companyName={data.company_name} />
              <ExportReport data={data} ticker={data.ticker} />
            </div>
          </div>

          {/* Company Thesis / About */}
          <CompanyThesis ticker={data.ticker} companyName={data.company_name} />

          {/* Summary stat cards */}
          <div className="grid grid-cols-3 gap-4">
            <Card>
              <StatBlock label="Current Price" value={`$${data.current_price.toFixed(2)}`} />
            </Card>
            <Card>
              <StatBlock label="F-Score" value={`${data.f_score} / 9`} />
            </Card>
            <Card>
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                  Margin of Safety
                </span>
                {hasFV && data.margin_of_safety_pct != null ? (
                  <>
                    <span className="text-3xl font-semibold font-mono text-moat-text">
                      {data.margin_of_safety_pct > 0 ? "+" : ""}{data.margin_of_safety_pct.toFixed(1)}%
                    </span>
                    <span className={`inline-flex items-center gap-1 text-sm font-mono font-medium ${data.margin_of_safety_pct >= 0 ? "text-moat-accent" : "text-moat-danger"}`}>
                      <span>{data.margin_of_safety_pct >= 0 ? "▲" : "▼"}</span>
                      {Math.abs(data.margin_of_safety_pct).toFixed(1)}% vs Base
                    </span>
                  </>
                ) : (
                  <>
                    <span className="text-3xl font-semibold font-mono text-moat-text">N/A</span>
                    <span className="text-xs text-moat-warning">Insufficient data</span>
                  </>
                )}
              </div>
            </Card>
          </div>

          {/* Intrinsic Value (single headline = consensus) */}
          <Card>
            <div className="flex flex-col items-center gap-1">
              <span className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                Intrinsic Value
              </span>
              <span
                className={`text-5xl font-semibold font-mono ${confidenceColor[data.confidence ?? "low"]}`}
              >
                {data.intrinsic_value.consensus != null
                  ? `$${data.intrinsic_value.consensus.toFixed(2)}`
                  : "N/A"}
              </span>
              <span className="text-xs text-moat-text-muted">
                blended DCF & multiples · {data.confidence} confidence
              </span>
              {data.valuation_note && (
                <p className="mt-1 text-center text-xs text-moat-warning">
                  {data.valuation_note}
                </p>
              )}
              {(data.intrinsic_value.bear.value != null ||
                data.intrinsic_value.bull.value != null) && (
                <div className="mt-4 grid w-full grid-cols-3 gap-3 border-t border-moat-border pt-4">
                  {(
                    [
                      ["Bearish", data.intrinsic_value.bear, "text-moat-danger"],
                      ["Base", data.intrinsic_value.base, "text-moat-text"],
                      ["Bullish", data.intrinsic_value.bull, "text-moat-accent"],
                    ] as const
                  ).map(([label, scenario, colorClass]) => (
                    <div key={label} className="flex flex-col items-center gap-0.5">
                      <span className="text-[10px] uppercase tracking-wider text-moat-text-muted">
                        {label}
                      </span>
                      <span className={`font-mono text-lg font-semibold ${colorClass}`}>
                        {scenario.value != null ? `$${scenario.value.toFixed(2)}` : "N/A"}
                      </span>
                      <span className="text-[10px] text-moat-text-muted">
                        {(scenario.growth * 100).toFixed(1)}% growth
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Card>

          {/* Valuation Breakdown */}
          <Card>
            <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
              Valuation Breakdown
            </h2>
            {(() => {
              // True consensus weights (equal-weight average of included sources).
              // Falls back to 0% for sources that weren't included.
              const cw = data.valuation_breakdown.consensus_weights ?? {};
              const w = (k: string) => `${((cw[k] ?? 0) * 100).toFixed(0)}%`;
              return (
                <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
                  <div className="flex flex-col gap-1">
                    <span className="text-xs text-moat-text-muted">
                      {data.valuation_engine?.method === "excess_return"
                        ? "Excess-Return Model"
                        : "Internal DCF"}
                    </span>
                    {data.valuation_breakdown.dcf_excluded ? (
                      <>
                        <span className="text-lg font-mono font-semibold text-moat-text-muted">
                          Not applicable
                        </span>
                        <span className="text-xs text-moat-text-muted">
                          model unavailable for this company
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="text-lg font-mono font-semibold text-moat-text">
                          {data.valuation_breakdown.internal_dcf != null
                            ? `$${data.valuation_breakdown.internal_dcf.toFixed(2)}`
                            : "N/A"}
                        </span>
                        <span className="text-xs text-moat-text-muted">
                          Weight: {w("internal_dcf")}
                        </span>
                      </>
                    )}
                  </div>
                  <div className="flex flex-col gap-1">
                    <span className="text-xs text-moat-text-muted">External DCF</span>
                    <span className="text-lg font-mono font-semibold text-moat-text">
                      {data.valuation_breakdown.external_dcf != null
                        ? `$${data.valuation_breakdown.external_dcf.toFixed(2)}`
                        : "N/A"}
                    </span>
                    <span className="text-xs text-moat-text-muted">
                      Weight: {w("external_dcf")}
                      {data.valuation_breakdown.source_mismatch_warning &&
                        " · outlier — excluded"}
                    </span>
                  </div>
                  <div className="flex flex-col gap-1">
                    <span className="text-xs text-moat-text-muted">Relative Value</span>
                    <span className="text-lg font-mono font-semibold text-moat-text">
                      {data.valuation_breakdown.relative_value != null
                        ? `$${data.valuation_breakdown.relative_value.toFixed(2)}`
                        : "N/A"}
                    </span>
                    <span className="text-xs text-moat-text-muted">
                      Weight: {w("relative_value")}
                    </span>
                  </div>
                  <div className="flex flex-col gap-1">
                    <span className="text-xs text-moat-text-muted">Earnings Multiple</span>
                    <span className="text-lg font-mono font-semibold text-moat-text">
                      {data.valuation_breakdown.earnings_multiple != null
                        ? `$${data.valuation_breakdown.earnings_multiple.toFixed(2)}`
                        : "N/A"}
                    </span>
                    <span className="text-xs text-moat-text-muted">
                      Weight: {w("earnings_multiple")}
                    </span>
                  </div>
                  <div className="flex flex-col gap-1">
                    <span className="text-xs text-moat-text-muted">Intrinsic Value</span>
                    <span className={`text-lg font-mono font-semibold ${confidenceColor[data.confidence ?? "low"]}`}>
                      {data.intrinsic_value.consensus != null
                        ? `$${data.intrinsic_value.consensus.toFixed(2)}`
                        : "N/A"}
                    </span>
                    {data.valuation_breakdown.adjustments_applied.length > 0 && (
                      <span className="text-xs text-moat-warning">
                        {data.valuation_breakdown.adjustments_applied
                          .map(adjustmentLabel)
                          .join(" · ")}
                      </span>
                    )}
                  </div>
                </div>
              );
            })()}
          </Card>

          {/* Moat Valuation Engine — quality-driven CAP, reverse DCF, Monte Carlo */}
          {data.valuation_engine && (
            <Card>
              <div className="mb-4 flex items-baseline justify-between flex-wrap gap-2">
                <h2 className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                  Moat Valuation Engine
                </h2>
                <span className="text-xs text-moat-text-muted">
                  quality-adjusted DCF · reverse DCF · {data.valuation_engine.monte_carlo ? "1,000-path Monte Carlo" : "scenario model"}
                </span>
              </div>
              <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
                {/* Moat score + growth runway */}
                <div className="flex flex-col gap-2">
                  <span className="text-xs text-moat-text-muted">Moat Score</span>
                  <div className="flex items-baseline gap-2">
                    <span className="text-3xl font-mono font-semibold text-moat-text">
                      {data.valuation_engine.moat_score.toFixed(0)}
                    </span>
                    <span className="text-xs text-moat-text-muted">/ 100</span>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-moat-border">
                    <div
                      className="h-1.5 rounded-full bg-moat-accent"
                      style={{ width: `${Math.min(data.valuation_engine.moat_score, 100)}%` }}
                    />
                  </div>
                  <span className="text-xs text-moat-text-muted">
                    → {data.valuation_engine.cap_years}-year growth runway (competitive advantage period)
                  </span>
                </div>

                {/* Market-implied vs analyst growth (reverse DCF) */}
                <div className="flex flex-col gap-2">
                  <span className="text-xs text-moat-text-muted">Growth: market vs analysts</span>
                  {data.valuation_engine.implied_growth != null && data.valuation_engine.expected_growth != null ? (
                    (() => {
                      const imp = data.valuation_engine!.implied_growth! * 100;
                      const exp = data.valuation_engine!.expected_growth! * 100;
                      const gap = exp - imp;
                      const verdict =
                        gap > 2 ? { label: "growth underpriced", cls: "text-moat-accent" }
                        : gap < -2 ? { label: "growth richly priced", cls: "text-moat-danger" }
                        : { label: "fairly priced", cls: "text-moat-text-muted" };
                      return (
                        <>
                          <div className="flex flex-col gap-1 text-sm font-mono">
                            <div className="flex justify-between">
                              <span className="text-moat-text-muted">market implies</span>
                              <span className="text-moat-text">{imp.toFixed(1)}%/yr</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-moat-text-muted">analyst trend</span>
                              <span className="text-moat-text">{exp.toFixed(1)}%/yr</span>
                            </div>
                          </div>
                          <span className={`text-xs font-medium ${verdict.cls}`}>
                            {gap > 0 ? "+" : ""}{gap.toFixed(1)} pts → {verdict.label}
                          </span>
                          <span className="text-xs text-moat-text-muted">
                            reverse DCF: the growth rate today&apos;s price implies vs the analyst consensus trend
                          </span>
                        </>
                      );
                    })()
                  ) : (
                    <span className="text-sm text-moat-text-muted">
                      {data.valuation_engine.method === "excess_return"
                        ? "Financials are valued on excess returns (ROE vs cost of equity), so a cash-flow reverse DCF doesn't apply."
                        : "Not solvable for this company (cash-flow profile out of range)."}
                    </span>
                  )}
                </div>

                {/* Monte Carlo fair-value distribution */}
                <div className="flex flex-col gap-2">
                  <span className="text-xs text-moat-text-muted">Fair-value distribution</span>
                  {data.valuation_engine.monte_carlo ? (
                    (() => {
                      const mc = data.valuation_engine!.monte_carlo!;
                      const price = data.current_price;
                      const lo = Math.min(mc.p10, price) * 0.92;
                      const hi = Math.max(mc.p90, price) * 1.08;
                      const pos = (v: number) => `${(((v - lo) / (hi - lo)) * 100).toFixed(1)}%`;
                      const width = (a: number, b: number) =>
                        `${(((b - a) / (hi - lo)) * 100).toFixed(1)}%`;
                      return (
                        <>
                          <div className="relative mt-3 h-2 w-full rounded-full bg-moat-border">
                            {/* P10–P90 */}
                            <div
                              className="absolute h-2 rounded-full bg-moat-accent-dim"
                              style={{ left: pos(mc.p10), width: width(mc.p10, mc.p90) }}
                            />
                            {/* P25–P75 */}
                            <div
                              className="absolute h-2 rounded-full bg-moat-accent/50"
                              style={{ left: pos(mc.p25), width: width(mc.p25, mc.p75) }}
                            />
                            {/* median */}
                            <div
                              className="absolute -top-1 h-4 w-0.5 bg-moat-accent"
                              style={{ left: pos(mc.p50) }}
                            />
                            {/* current price marker */}
                            <div
                              className="absolute -top-1 h-4 w-0.5 bg-moat-warning"
                              style={{ left: pos(price) }}
                            />
                          </div>
                          <div className="flex justify-between text-xs font-mono text-moat-text-muted">
                            <span>${mc.p10.toFixed(0)}</span>
                            <span className="text-moat-text">med ${mc.p50.toFixed(0)}</span>
                            <span>${mc.p90.toFixed(0)}</span>
                          </div>
                          <span className="text-xs text-moat-text-muted">
                            <span className="text-moat-warning">▎</span> price ${price.toFixed(0)} · band = P10–P90 of 1,000 simulated DCF paths
                          </span>
                        </>
                      );
                    })()
                  ) : (
                    <span className="text-sm text-moat-text-muted">
                      DCF lens unreliable here (thin/volatile free cash flow) — the
                      consensus leans on the earnings and relative-value anchors instead.
                    </span>
                  )}
                </div>
              </div>
            </Card>
          )}

          {/* AI second opinion on the quantitative valuation */}
          <ValuationReview ticker={data.ticker} />

          {/* Legendary Investor Cards */}
          <InvestorCards ticker={data.ticker} />

          {/* Legendary Investor Holdings (13F) */}
          <InstitutionalHoldings ticker={data.ticker} />

          {/* Insider Trades (SEC Form 4) */}
          <InsiderTrades ticker={data.ticker} />

          {/* Price Chart */}
          <PriceChart ticker={data.ticker} />

          {/* Gauge + DCF details */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card>
              <div className="flex flex-col items-center">
                {hasFV ? (
                  <Gauge
                    value={data.margin_of_safety_pct!}
                    min={-50}
                    max={50}
                    label="Margin of Safety"
                  />
                ) : (
                  <div className="flex flex-col items-center gap-2 py-8">
                    <span className="text-2xl font-semibold font-mono text-moat-text-muted">N/A</span>
                    <span className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">Margin of Safety</span>
                    <span className="text-xs text-moat-warning text-center max-w-[200px]">{data.valuation_note}</span>
                  </div>
                )}
              </div>
            </Card>
            <Card>
              <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                DCF Details
              </h2>
              <div className="grid grid-cols-2 gap-3">
                <StatBlock label="WACC" value={`${(data.dcf_breakdown.wacc * 100).toFixed(1)}%`} />
                <StatBlock label="Terminal Growth" value={`${(data.dcf_breakdown.terminal_growth * 100).toFixed(1)}%`} />
                <StatBlock label="Growth Rate" value={`${(data.dcf_breakdown.growth_rate * 100).toFixed(1)}%`} />
                <StatBlock label="Growth Source" value={data.dcf_breakdown.growth_source.replace("forward_", "Fwd ").replace("historical_", "Hist ")} />
                <StatBlock label="Enterprise Value" value={formatB(data.dcf_breakdown.enterprise_value)} />
                <StatBlock label="Net Debt" value={formatB(data.dcf_breakdown.net_debt)} />
              </div>
            </Card>
          </div>

          {/* Financial Trends */}
          <FinancialTrends ticker={data.ticker} />

          {/* P/E Chart */}
          <PEChart ticker={data.ticker} currentPE={currentPE} />

          {/* Key Metrics */}
          {/* `fair_value` was never part of the analyze response, so this prop has
              always resolved to undefined (the fair-value gap renders as N/A).
              Pass null explicitly to preserve that exact behavior and unblock the
              production type-check. See docs/Development.md (future improvements). */}
          <KeyMetrics
            ticker={data.ticker}
            fairValue={null}
            currentPrice={data.current_price}
          />
        </>
      )}
    </div>
  );
}
