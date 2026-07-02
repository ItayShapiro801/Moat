"use client";

import { useState, useEffect, useRef } from "react";
import { API_BASE_URL } from "@/lib/api";
import { Card } from "./ui/Card";
import { Badge } from "./ui/Badge";

interface Review {
  applicable?: boolean;
  assessment?: "reasonable" | "too_high" | "too_low" | "unreliable";
  agrees_with_model?: boolean;
  ai_fair_value?: number | null;
  ai_value_low?: number | null;
  ai_value_high?: number | null;
  confidence?: "high" | "medium" | "low";
  rationale?: string;
  key_factors?: string[];
  model_intrinsic_value?: number | null;
  current_price?: number | null;
}

const ASSESSMENT_LABEL: Record<string, string> = {
  reasonable: "Model looks reasonable",
  too_high: "Model looks too high",
  too_low: "Model looks too low",
  unreliable: "Model looks unreliable",
};

function assessmentVariant(a?: string): "success" | "warning" | "danger" | "neutral" {
  if (a === "reasonable") return "success";
  if (a === "too_high" || a === "too_low") return "warning";
  if (a === "unreliable") return "danger";
  return "neutral";
}

const fmt = (v: number | null | undefined, ccy = "") =>
  v == null ? "—" : `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}${ccy}`;

export function ValuationReview({ ticker }: { ticker: string }) {
  const [data, setData] = useState<Review | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const fetchedTicker = useRef<string | null>(null);

  useEffect(() => {
    if (fetchedTicker.current === ticker) return;
    fetchedTicker.current = ticker;
    setLoading(true);
    setFailed(false);
    fetch(`${API_BASE_URL}/valuation-review/${ticker}`)
      .then((r) => r.json())
      .then(setData)
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
  }, [ticker]);

  // Only shown for operating companies; silently absent otherwise.
  if (!loading && (failed || !data || data.applicable === false)) return null;

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between gap-2 flex-wrap">
        <h2 className="text-xs font-medium uppercase tracking-widest text-moat-text-muted">
          AI Valuation Review
          <span className="ml-2 normal-case tracking-normal text-[10px] text-moat-text-muted/70">
            second opinion on the model
          </span>
        </h2>
        {!loading && data?.assessment && (
          <div className="flex items-center gap-2">
            <Badge variant={assessmentVariant(data.assessment)}>
              {ASSESSMENT_LABEL[data.assessment] || data.assessment}
            </Badge>
            {data.confidence && (
              <span className="text-[11px] text-moat-text-muted">
                {data.confidence} confidence
              </span>
            )}
          </div>
        )}
      </div>

      {loading ? (
        <div className="flex justify-center py-6 text-moat-text-muted animate-pulse">
          Reviewing the valuation…
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {/* When the AI disagrees and offers its own estimate, show it side-by-side. */}
          {data?.agrees_with_model === false && data?.ai_fair_value != null && (
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl border border-moat-border bg-moat-bg/40 px-4 py-3">
                <div className="text-[10px] uppercase tracking-wider text-moat-text-muted">
                  Model estimate
                </div>
                <div className="font-mono text-lg text-moat-text-muted">
                  {fmt(data.model_intrinsic_value)}
                </div>
              </div>
              <div className="rounded-xl border border-moat-accent/40 bg-moat-accent/10 px-4 py-3">
                <div className="text-[10px] uppercase tracking-wider text-moat-accent">
                  AI estimate
                </div>
                <div className="font-mono text-lg font-semibold text-moat-text">
                  {fmt(data.ai_fair_value)}
                </div>
                {data.ai_value_low != null && data.ai_value_high != null && (
                  <div className="text-[11px] text-moat-text-muted">
                    range {fmt(data.ai_value_low)} – {fmt(data.ai_value_high)}
                  </div>
                )}
              </div>
            </div>
          )}

          {data?.rationale && (
            <p className="text-sm text-moat-text-muted leading-relaxed">{data.rationale}</p>
          )}

          {Array.isArray(data?.key_factors) && data!.key_factors!.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {data!.key_factors!.map((f, i) => (
                <span
                  key={i}
                  className="rounded-md bg-moat-surface-hover px-2 py-1 text-[11px] text-moat-text-muted"
                >
                  {f}
                </span>
              ))}
            </div>
          )}

          <p className="text-[10px] text-moat-text-muted/70">
            AI-generated second opinion — informed judgement, not investment advice.
          </p>
        </div>
      )}
    </Card>
  );
}
