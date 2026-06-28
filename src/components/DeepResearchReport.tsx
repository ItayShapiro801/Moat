"use client";

import { useState } from "react";
import { API_BASE_URL } from "@/lib/api";
import { motion } from "framer-motion";

interface MoatScores {
  network_effects?: number;
  brand?: number;
  switching_costs?: number;
  data_advantage?: number;
  technology_advantage?: number;
  regulatory_advantage?: number;
  overall?: number;
  summary?: string;
}
interface ScenarioEntry {
  value: number | null;
  commentary?: string | null;
}
interface InvestmentSummary {
  reasons_to_buy?: string[];
  reasons_not_to_buy?: string[];
  thesis_works_if?: string;
  thesis_breaks_if?: string;
  scores?: Record<string, number>;
}
interface Report {
  executive_summary?: string;
  business_model?: string;
  products_services?: string;
  competitive_moat?: MoatScores;
  industry_analysis?: string;
  competitors?: string;
  management?: string;
  financial_history?: string;
  unit_economics?: string;
  risks?: string;
  growth_drivers?: string;
  scenarios?: { bear?: ScenarioEntry; base?: ScenarioEntry; bull?: ScenarioEntry };
  red_flags?: string;
  open_questions?: string;
  investment_summary?: InvestmentSummary;
}
interface DeepData {
  company_name: string;
  fiscal_range: string;
  years_covered: number;
  report: Report;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-bold uppercase tracking-wider text-moat-accent border-b border-moat-border pb-1">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Para({ text }: { text?: string }) {
  if (!text) return null;
  return <p className="text-sm text-moat-text-muted leading-relaxed whitespace-pre-line">{text}</p>;
}

const MOAT_DIMS: [keyof MoatScores, string][] = [
  ["network_effects", "Network Effects"],
  ["brand", "Brand"],
  ["switching_costs", "Switching Costs"],
  ["data_advantage", "Data Advantage"],
  ["technology_advantage", "Technology"],
  ["regulatory_advantage", "Regulatory"],
];

const SCORE_LABELS: [string, string][] = [
  ["business_quality", "Business Quality"],
  ["management_quality", "Management Quality"],
  ["competitive_advantage", "Competitive Advantage"],
  ["growth_potential", "Growth Potential"],
  ["risk_level", "Risk Level"],
  ["overall_attractiveness", "Overall Attractiveness"],
];

function scoreColor(n: number | undefined): string {
  if (n == null) return "text-moat-text-muted";
  if (n >= 7) return "text-moat-accent";
  if (n >= 4) return "text-moat-warning";
  return "text-moat-danger";
}

export function DeepResearchButton({
  ticker,
  companyName,
}: {
  ticker: string;
  companyName: string;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<DeepData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  function generate(refresh = false) {
    setOpen(true);
    setLoading(true);
    setError(false);
    fetch(`${API_BASE_URL}/deep-research/${ticker}${refresh ? "?refresh=true" : ""}`)
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.json();
      })
      .then((d) => setData(d))
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }

  const r = data?.report;

  return (
    <>
      <button
        onClick={() => (data ? setOpen(true) : generate())}
        className="px-3 py-1 rounded-lg text-sm font-medium border border-moat-accent text-moat-accent hover:bg-moat-accent/10 transition-colors"
      >
        Deep Research Report
      </button>

      {open && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/70 backdrop-blur-sm overflow-y-auto py-10">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="relative w-full max-w-3xl mx-4 rounded-2xl border border-moat-border bg-moat-surface p-8"
          >
            <button
              onClick={() => setOpen(false)}
              className="absolute top-4 right-4 text-moat-text-muted hover:text-moat-text text-xl"
              aria-label="Close"
            >
              ×
            </button>

            <h2 className="text-2xl font-bold text-moat-text mb-1">
              Deep Research — {companyName}
            </h2>
            <p className="text-xs text-moat-text-muted mb-6">
              Full diligence report ({ticker})
            </p>

            {loading && (
              <div className="flex flex-col items-center gap-3 py-16">
                <div className="h-8 w-8 rounded-full border-2 border-moat-accent border-t-transparent animate-spin" />
                <p className="text-moat-text-muted text-sm">
                  Generating full diligence report… this takes about 30 seconds.
                </p>
              </div>
            )}

            {!loading && error && (
              <div className="flex flex-col items-center gap-3 py-16">
                <p className="text-moat-text-muted text-sm">Couldn&apos;t load right now.</p>
                <button
                  onClick={() => generate(true)}
                  className="px-4 py-2 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
                >
                  Try Again
                </button>
              </div>
            )}

            {!loading && !error && r && (
              <div className="flex flex-col gap-6">
                <Section title="1 · Executive Summary"><Para text={r.executive_summary} /></Section>
                <Section title="2 · Business Model"><Para text={r.business_model} /></Section>
                <Section title="3 · Products & Services"><Para text={r.products_services} /></Section>

                <Section title="4 · Competitive Moat">
                  {r.competitive_moat && (
                    <>
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                        {MOAT_DIMS.map(([k, label]) => (
                          <div key={k} className="flex items-center justify-between rounded-lg bg-moat-bg/40 px-3 py-1.5">
                            <span className="text-xs text-moat-text-muted">{label}</span>
                            <span className={`font-mono font-semibold text-sm ${scoreColor(r.competitive_moat![k] as number)}`}>
                              {r.competitive_moat![k] ?? "—"}/10
                            </span>
                          </div>
                        ))}
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs font-semibold uppercase tracking-wider text-moat-text-muted">Overall Moat</span>
                        <span className={`font-mono font-bold ${scoreColor(r.competitive_moat.overall)}`}>
                          {r.competitive_moat.overall ?? "—"}/10
                        </span>
                      </div>
                      <Para text={r.competitive_moat.summary} />
                    </>
                  )}
                </Section>

                <Section title="5 · Industry Analysis"><Para text={r.industry_analysis} /></Section>
                <Section title="6 · Competitors"><Para text={r.competitors} /></Section>
                <Section title="7 · Management"><Para text={r.management} /></Section>
                <Section title={`8 · Financial History (${data!.years_covered}y, ${data!.fiscal_range})`}>
                  <Para text={r.financial_history} />
                </Section>
                <Section title="9 · Unit Economics"><Para text={r.unit_economics} /></Section>
                <Section title="10 · Risks"><Para text={r.risks} /></Section>
                <Section title="11 · Growth Drivers"><Para text={r.growth_drivers} /></Section>

                <Section title="12 · Scenarios (DCF-derived values)">
                  {r.scenarios && (
                    <div className="flex flex-col gap-3">
                      {(["bear", "base", "bull"] as const).map((k) => {
                        const s = r.scenarios![k];
                        if (!s) return null;
                        return (
                          <div key={k} className="rounded-lg bg-moat-bg/40 px-4 py-3">
                            <div className="flex items-baseline gap-2 mb-1">
                              <span className="text-xs font-semibold uppercase tracking-wider text-moat-text-muted">{k}</span>
                              <span className="font-mono font-bold text-moat-text">
                                {s.value != null ? `$${s.value.toFixed(2)}` : "N/A"}
                              </span>
                            </div>
                            <Para text={s.commentary ?? undefined} />
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Section>

                <Section title="13 · Red Flags"><Para text={r.red_flags} /></Section>
                <Section title="14 · Open Questions"><Para text={r.open_questions} /></Section>

                <Section title="15 · Investment Summary">
                  {r.investment_summary && (
                    <div className="flex flex-col gap-4">
                      {r.investment_summary.scores && (
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                          {SCORE_LABELS.map(([k, label]) => (
                            <div key={k} className="flex items-center justify-between rounded-lg bg-moat-bg/40 px-3 py-1.5">
                              <span className="text-xs text-moat-text-muted">{label}</span>
                              <span className={`font-mono font-semibold text-sm ${scoreColor(r.investment_summary!.scores![k])}`}>
                                {r.investment_summary!.scores![k] ?? "—"}/10
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        {r.investment_summary.reasons_to_buy && (
                          <div>
                            <h4 className="text-xs font-semibold uppercase tracking-wider text-moat-accent mb-1">Reasons to Buy</h4>
                            <ul className="list-disc pl-4 text-sm text-moat-text-muted space-y-1">
                              {r.investment_summary.reasons_to_buy.map((x, i) => <li key={i}>{x}</li>)}
                            </ul>
                          </div>
                        )}
                        {r.investment_summary.reasons_not_to_buy && (
                          <div>
                            <h4 className="text-xs font-semibold uppercase tracking-wider text-moat-danger mb-1">Reasons Not to Buy</h4>
                            <ul className="list-disc pl-4 text-sm text-moat-text-muted space-y-1">
                              {r.investment_summary.reasons_not_to_buy.map((x, i) => <li key={i}>{x}</li>)}
                            </ul>
                          </div>
                        )}
                      </div>
                      {r.investment_summary.thesis_works_if && (
                        <p className="text-sm text-moat-text"><span className="text-moat-accent font-semibold">Thesis works if: </span>{r.investment_summary.thesis_works_if}</p>
                      )}
                      {r.investment_summary.thesis_breaks_if && (
                        <p className="text-sm text-moat-text"><span className="text-moat-danger font-semibold">Thesis breaks if: </span>{r.investment_summary.thesis_breaks_if}</p>
                      )}
                    </div>
                  )}
                </Section>

                <p className="text-[10px] text-moat-text-muted text-center pt-2">
                  Generated by Moat — not financial advice.
                </p>
              </div>
            )}
          </motion.div>
        </div>
      )}
    </>
  );
}
