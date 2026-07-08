"use client";

import { useState } from "react";
import { API_BASE_URL } from "@/lib/api";
import { jsPDF } from "jspdf";
// html2canvas-pro supports modern CSS color functions (lab/oklch) that the
// original html2canvas chokes on with Tailwind v4.
import html2canvas from "html2canvas-pro";
import { useAuth } from "@/lib/auth-context";

// Email delivery requires a verified sending domain (Resend), which the public
// free deployment doesn't have. Hidden unless explicitly enabled; "Download PDF"
// works for everyone regardless. Set NEXT_PUBLIC_ENABLE_EMAIL=true to re-enable.
const EMAIL_ENABLED = process.env.NEXT_PUBLIC_ENABLE_EMAIL === "true";

interface Scenario {
  value: number | null;
}
interface ReportData {
  company_name: string;
  current_price: number;
  intrinsic_value: { consensus: number | null; base?: Scenario };
  margin_of_safety_pct: number | null;
  confidence: string;
  f_score: number;
  valuation_breakdown: {
    internal_dcf: number | null;
    earnings_multiple?: number | null;
    external_dcf: number | null;
    relative_value: number | null;
    dcf_excluded?: boolean;
  };
}

interface Investor {
  name: string;
  score: number | null;
  verdict: string | null;
  bull_case: string | null;
  bear_case: string | null;
}

// Light theme palette for the printable PDF
const INK = [31, 41, 55] as const; // slate-800
const MUTED = [107, 114, 128] as const; // gray-500
const GREEN = [22, 163, 74] as const;
const RED = [220, 38, 38] as const;
const LINE = [220, 224, 230] as const;

export function ExportReport({
  data,
  ticker,
}: {
  data: ReportData;
  ticker: string;
}) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [emailMode, setEmailMode] = useState(false);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  async function buildPdf(): Promise<jsPDF> {
    // Fetch supplementary data for the report
    const [metricsRes, investorsRes, thesisRes, deepRes] = await Promise.allSettled([
      fetch(`${API_BASE_URL}/metrics/${ticker}`).then((r) => r.json()),
      fetch(`${API_BASE_URL}/investors/${ticker}`).then((r) => r.json()),
      fetch(`${API_BASE_URL}/thesis/${ticker}`).then((r) => r.json()),
      // Deep research is cached server-side; included if available (instant when
      // the user already generated it, otherwise generated on demand).
      fetch(`${API_BASE_URL}/deep-research/${ticker}`).then((r) => r.json()),
    ]);
    const metrics = metricsRes.status === "fulfilled" ? metricsRes.value : null;
    const investors: Investor[] =
      investorsRes.status === "fulfilled" ? investorsRes.value.investors || [] : [];
    const thesis = thesisRes.status === "fulfilled" ? thesisRes.value : null;
    const deep =
      deepRes.status === "fulfilled" ? deepRes.value?.report : null;

    const doc = new jsPDF({ unit: "pt", format: "a4" });
    const W = doc.internal.pageSize.getWidth();
    const H = doc.internal.pageSize.getHeight();
    const M = 48; // margin
    let y = M;
    // splitTextToSize wraps on spaces only — a single unbroken run (e.g. a
    // hyphen-joined LLM phrase) that measures right at the column width has
    // nowhere to break and gets drawn past the printable area, clipped by the
    // page edge. A few points of slack means that edge case wraps one word
    // earlier instead of overflowing.
    const WRAP_SLACK = 12;

    const ensure = (need: number) => {
      if (y + need > H - 56) {
        doc.addPage();
        y = M;
      }
    };
    const setColor = (c: readonly number[]) => doc.setTextColor(c[0], c[1], c[2]);
    const fmt$ = (v: number | null | undefined) =>
      v == null ? "N/A" : `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

    const section = (title: string) => {
      ensure(40);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(12);
      setColor(INK);
      doc.text(title, M, y);
      y += 8;
      doc.setDrawColor(LINE[0], LINE[1], LINE[2]);
      doc.line(M, y, W - M, y);
      y += 18;
      doc.setFont("helvetica", "normal");
    };
    const row = (label: string, value: string) => {
      ensure(18);
      doc.setFontSize(10);
      setColor(MUTED);
      doc.text(label, M, y);
      setColor(INK);
      doc.text(value, W - M, y, { align: "right" });
      y += 16;
    };
    const paragraph = (label: string, text: string, labelColor: readonly number[]) => {
      if (!text) return;
      ensure(28);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(9);
      setColor(labelColor);
      doc.text(label.toUpperCase(), M, y);
      y += 13;
      doc.setFont("helvetica", "normal");
      doc.setFontSize(10);
      setColor(INK);
      const lines = doc.splitTextToSize(text, W - 2 * M - WRAP_SLACK);
      lines.forEach((ln: string) => {
        ensure(14);
        doc.text(ln, M, y);
        y += 13;
      });
      y += 8;
    };
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

    // Capture each of the 7 Financial Trends metrics by switching tabs in the
    // live component, reading its CAGR badge, and embedding the chart + caption.
    const captureFinancialTrends = async () => {
      const el = document.getElementById("export-financial-trends");
      if (!el) return;
      const tabLabels = [
        "Revenue", "EPS", "Free Cash Flow", "Gross", "Op Income", "Net Income", "Shares",
      ];
      for (const label of tabLabels) {
        const btn = Array.from(el.querySelectorAll("button")).find(
          (b) => b.textContent?.trim() === label
        ) as HTMLButtonElement | undefined;
        if (!btn) continue;
        btn.click();
        await sleep(500); // let Recharts re-render
        // CAGR badge text (e.g. "CAGR +19.9%")
        const cagrEl = Array.from(el.querySelectorAll("span, div")).find((n) =>
          n.textContent?.trim().startsWith("CAGR")
        );
        const cagr = cagrEl?.textContent?.trim() || "";
        try {
          const canvas = await html2canvas(el, { backgroundColor: "#11161f", scale: 1.5 });
          const imgW = W - 2 * M;
          const imgH = (canvas.height / canvas.width) * imgW;
          ensure(imgH + 26);
          doc.setFont("helvetica", "bold");
          doc.setFontSize(10);
          setColor(INK);
          doc.text(`${label}${cagr ? " — " + cagr : ""}`, M, y);
          y += 12;
          // JPEG keeps the multi-chart PDF small enough to email.
          doc.addImage(canvas.toDataURL("image/jpeg", 0.85), "JPEG", M, y, imgW, imgH);
          y += imgH + 14;
        } catch {
          /* best-effort */
        }
      }
    };

    // Header
    doc.setFont("helvetica", "bold");
    doc.setFontSize(22);
    setColor(GREEN);
    doc.text("Moat", M, y);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    setColor(MUTED);
    doc.text(
      `Generated ${new Date().toLocaleDateString()} — not financial advice`,
      W - M,
      y,
      { align: "right" }
    );
    y += 18;
    doc.setDrawColor(LINE[0], LINE[1], LINE[2]);
    doc.line(M, y, W - M, y);
    y += 26;

    // Company title
    doc.setFont("helvetica", "bold");
    doc.setFontSize(18);
    setColor(INK);
    doc.text(`${data.company_name} (${ticker})`, M, y);
    y += 18;
    doc.setFont("helvetica", "normal");
    doc.setFontSize(11);
    setColor(MUTED);
    doc.text(`Current Price: ${fmt$(data.current_price)}`, M, y);
    y += 24;

    // Company Thesis (research note) — right after header, before summary boxes
    if (thesis && (thesis.business_overview || thesis.investment_thesis || thesis.key_risks)) {
      section("Company Thesis");
      paragraph("Business Overview", thesis.business_overview || "", MUTED);
      paragraph("Investment Thesis", thesis.investment_thesis || "", GREEN);
      paragraph("Key Risks", thesis.key_risks || "", RED);
      y += 4;
    }

    // Valuation summary boxes
    const iv = data.intrinsic_value.consensus;
    const mos = data.margin_of_safety_pct;
    const summary: [string, string, readonly number[]][] = [
      ["Intrinsic Value", fmt$(iv), INK],
      [
        "Margin of Safety",
        mos == null ? "N/A" : `${mos > 0 ? "+" : ""}${mos.toFixed(1)}%`,
        mos == null ? MUTED : mos > 0 ? GREEN : RED,
      ],
      ["Confidence", data.confidence, INK],
      ["F-Score", `${data.f_score} / 9`, INK],
    ];
    const boxW = (W - 2 * M - 3 * 10) / 4;
    summary.forEach(([label, val, color], i) => {
      const x = M + i * (boxW + 10);
      doc.setDrawColor(LINE[0], LINE[1], LINE[2]);
      doc.roundedRect(x, y, boxW, 50, 4, 4);
      doc.setFontSize(7.5);
      setColor(MUTED);
      doc.text(label.toUpperCase(), x + 8, y + 16);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(13);
      setColor(color);
      doc.text(val, x + 8, y + 36);
      doc.setFont("helvetica", "normal");
    });
    y += 72;

    // Valuation Breakdown
    section("Valuation Breakdown");
    const vb = data.valuation_breakdown;
    row("Internal Model (DCF / excess-return)", vb.dcf_excluded ? "Not applicable" : fmt$(vb.internal_dcf));
    row("External DCF (FMP)", fmt$(vb.external_dcf));
    row("Relative Value (multiples)", fmt$(vb.relative_value));
    if (vb.earnings_multiple != null) row("Earnings Multiple", fmt$(vb.earnings_multiple));
    row("Blended Intrinsic Value", fmt$(iv));
    y += 16;

    // Financial Trends — all 7 metrics captured with CAGR captions.
    section("Financial Trends");
    await captureFinancialTrends();

    // Key Metrics
    if (metrics) {
      section("Key Metrics");
      const v = metrics.valuation || {};
      const q = metrics.quality || {};
      const fh = metrics.financial_health || {};
      const pairs: [string, string][] = [
        ["P/E Ratio", v.pe_ratio != null ? `${v.pe_ratio}x` : "N/A"],
        ["Forward P/E", v.forward_pe != null ? `${v.forward_pe}x` : "N/A"],
        ["P/B Ratio", v.pb_ratio != null ? `${v.pb_ratio}x` : "N/A"],
        ["EV/EBITDA", v.ev_ebitda != null ? `${v.ev_ebitda}x` : "N/A"],
        ["Profit Margin", q.profit_margin != null ? `${q.profit_margin}%` : "N/A"],
        ["EPS (TTM)", fh.eps_ttm != null ? `$${fh.eps_ttm}` : "N/A"],
      ];
      pairs.forEach(([l, val]) => row(l, val));
      y += 12;
    }

    // Investor Takes
    if (investors.length) {
      section("Legendary Investor Takes");
      investors.forEach((inv) => {
        ensure(70);
        doc.setFont("helvetica", "bold");
        doc.setFontSize(11);
        setColor(INK);
        const scoreTxt =
          inv.score != null ? `  ${inv.score}/10` : "";
        doc.text(`${inv.name}${scoreTxt} — ${inv.verdict || "—"}`, M, y);
        y += 14;
        doc.setFont("helvetica", "normal");
        doc.setFontSize(9);
        if (inv.bull_case) {
          setColor(GREEN);
          doc.text("Bull:", M, y);
          setColor(INK);
          const lines = doc.splitTextToSize(inv.bull_case, W - 2 * M - 30 - WRAP_SLACK);
          doc.text(lines, M + 30, y);
          y += lines.length * 11 + 4;
        }
        ensure(30);
        if (inv.bear_case) {
          setColor(RED);
          doc.text("Bear:", M, y);
          setColor(INK);
          const lines = doc.splitTextToSize(inv.bear_case, W - 2 * M - 30 - WRAP_SLACK);
          doc.text(lines, M + 30, y);
          y += lines.length * 11 + 10;
        }
      });
    }

    // Deep Research Report (the centerpiece, when available)
    if (deep) {
      doc.addPage();
      y = M;
      doc.setFont("helvetica", "bold");
      doc.setFontSize(16);
      setColor(INK);
      doc.text("Deep Research Report", M, y);
      y += 22;

      paragraph("Executive Summary", deep.executive_summary || "", INK);
      paragraph("Business Model", deep.business_model || "", INK);
      if (deep.competitive_moat) {
        const m = deep.competitive_moat;
        paragraph(
          `Competitive Moat (Overall ${m.overall ?? "—"}/10)`,
          m.summary || "",
          INK
        );
      }
      paragraph("Industry Analysis", deep.industry_analysis || "", INK);
      paragraph("Competitors", deep.competitors || "", INK);
      paragraph("Management", deep.management || "", INK);
      paragraph("Financial History", deep.financial_history || "", INK);
      paragraph("Unit Economics", deep.unit_economics || "", INK);
      paragraph("Risks", deep.risks || "", RED);
      paragraph("Growth Drivers", deep.growth_drivers || "", GREEN);
      if (deep.scenarios) {
        const s = deep.scenarios;
        const fmtSc = (e: { value: number | null; commentary?: string } | undefined) =>
          e ? `${e.value != null ? "$" + e.value.toFixed(2) : "N/A"} — ${e.commentary || ""}` : "";
        paragraph("Bear Scenario", fmtSc(s.bear), RED);
        paragraph("Base Scenario", fmtSc(s.base), INK);
        paragraph("Bull Scenario", fmtSc(s.bull), GREEN);
      }
      paragraph("Red Flags", deep.red_flags || "", RED);
      paragraph("Open Questions", deep.open_questions || "", MUTED);
      if (deep.investment_summary) {
        const inv = deep.investment_summary;
        if (Array.isArray(inv.reasons_to_buy))
          paragraph("Reasons to Buy", inv.reasons_to_buy.map((x: string, i: number) => `${i + 1}. ${x}`).join("\n"), GREEN);
        if (Array.isArray(inv.reasons_not_to_buy))
          paragraph("Reasons Not to Buy", inv.reasons_not_to_buy.map((x: string, i: number) => `${i + 1}. ${x}`).join("\n"), RED);
        if (inv.thesis_works_if) paragraph("Thesis Works If", inv.thesis_works_if, INK);
        if (inv.thesis_breaks_if) paragraph("Thesis Breaks If", inv.thesis_breaks_if, INK);
      }
    }

    // Footer on every page
    const pages = doc.getNumberOfPages();
    for (let p = 1; p <= pages; p++) {
      doc.setPage(p);
      doc.setFontSize(8);
      setColor(MUTED);
      doc.text("Generated by Moat — not financial advice", W / 2, H - 28, {
        align: "center",
      });
    }

    return doc;
  }

  async function handleDownload() {
    setBusy(true);
    setMsg(null);
    try {
      const doc = await buildPdf();
      doc.save(`Moat-${ticker}-Report.pdf`);
      setOpen(false);
    } catch {
      setMsg({ type: "error", text: "Could not generate PDF." });
    } finally {
      setBusy(false);
    }
  }

  async function handleEmail() {
    const to = (email || user?.email || "").trim();
    if (!to) {
      setMsg({ type: "error", text: "Enter an email address." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const doc = await buildPdf();
      const dataUri = doc.output("datauristring");
      const res = await fetch(`${API_BASE_URL}/email-report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to_email: to, ticker, pdf_base64: dataUri }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || "Email failed");
      }
      setMsg({ type: "success", text: `Report sent to ${to}` });
    } catch (err: unknown) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "Email failed.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => {
          setOpen((o) => !o);
          setEmail(user?.email || "");
        }}
        className="px-3 py-1 rounded-lg text-sm font-medium border border-moat-border text-moat-text hover:bg-moat-surface-hover transition-colors"
      >
        Export Report
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-72 z-50 rounded-lg border border-moat-border bg-moat-surface p-3 shadow-lg flex flex-col gap-2">
          {busy ? (
            <div className="flex flex-col items-center gap-3 py-4 px-1 text-center">
              <div className="h-6 w-6 rounded-full border-2 border-moat-accent border-t-transparent animate-spin" />
              <p className="text-xs text-moat-text">
                Generating your report… this can take up to 30 seconds.
              </p>
              <p className="text-[11px] text-moat-text-muted">
                Please don&apos;t close this window.
              </p>
            </div>
          ) : (
            <>
              <button
                onClick={handleDownload}
                className="w-full py-2 rounded-md bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
              >
                Download PDF
              </button>

              {EMAIL_ENABLED &&
                (!emailMode ? (
                  <button
                    onClick={() => setEmailMode(true)}
                    className="w-full py-2 rounded-md border border-moat-border text-moat-text text-sm font-medium hover:bg-moat-surface-hover transition-colors"
                  >
                    Email PDF
                  </button>
                ) : (
                  <div className="flex flex-col gap-2">
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="Email address"
                      className="rounded-md border border-moat-border bg-moat-bg px-3 py-1.5 text-moat-text placeholder:text-moat-text-muted text-sm focus:outline-none focus:ring-2 focus:ring-moat-accent"
                    />
                    <button
                      onClick={handleEmail}
                      className="w-full py-2 rounded-md bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
                    >
                      Send
                    </button>
                  </div>
                ))}

              {msg && (
                <p
                  className={`text-xs text-center ${
                    msg.type === "success" ? "text-moat-accent" : "text-moat-danger"
                  }`}
                >
                  {msg.text}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
