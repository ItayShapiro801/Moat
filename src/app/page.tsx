"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { TickerSearch } from "@/components/ui/TickerSearch";

function AnimatedCounter({ target }: { target: number }) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let frame: number;
    const duration = 2000;
    const start = performance.now();
    function tick(now: number) {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setCount(Math.floor(eased * target));
      if (progress < 1) frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target]);
  return <>{count.toLocaleString()}</>;
}

interface Particle {
  width: number;
  height: number;
  left: number;
  top: number;
  yDrift: number;
  xDrift: number;
  opacity: number;
  duration: number;
  delay: number;
}

function GlowParticles() {
  // Generate particle values only on the client to avoid SSR hydration mismatch
  const [particles, setParticles] = useState<Particle[]>([]);

  useEffect(() => {
    setParticles(
      Array.from({ length: 18 }).map(() => ({
        width: 3 + Math.random() * 4,
        height: 3 + Math.random() * 4,
        left: 10 + Math.random() * 80,
        top: 10 + Math.random() * 70,
        yDrift: -30 - Math.random() * 40,
        xDrift: (Math.random() - 0.5) * 30,
        opacity: 0.4 + Math.random() * 0.3,
        duration: 4 + Math.random() * 4,
        delay: Math.random() * 5,
      }))
    );
  }, []);

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {particles.map((p, i) => (
        <motion.div
          key={i}
          className="absolute rounded-full bg-moat-accent"
          style={{
            width: p.width,
            height: p.height,
            left: `${p.left}%`,
            top: `${p.top}%`,
            filter: "blur(1px)",
          }}
          animate={{
            y: [0, p.yDrift, 0],
            x: [0, p.xDrift, 0],
            opacity: [0, p.opacity, 0],
          }}
          transition={{
            duration: p.duration,
            repeat: Infinity,
            delay: p.delay,
            ease: "easeInOut",
          }}
        />
      ))}
    </div>
  );
}

function ChartLine() {
  return (
    <svg
      viewBox="0 0 800 200"
      className="absolute inset-0 w-full h-full"
      preserveAspectRatio="none"
      style={{ opacity: 0.15 }}
    >
      <defs>
        <filter id="glow">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <motion.path
        d="M0,160 C50,155 80,140 120,130 C160,120 180,125 220,100 C260,75 300,90 340,70 C380,50 420,65 460,45 C500,25 540,40 580,30 C620,20 660,35 700,25 C740,15 770,20 800,10"
        fill="none"
        stroke="#34d399"
        strokeWidth="2.5"
        filter="url(#glow)"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 2.5, ease: "easeInOut" }}
      />
    </svg>
  );
}

const FEATURES_LIVE = [
  {
    title: "Intrinsic Value Engine",
    desc: "Blended DCF + multi-factor relative valuation with sector-aware adjustments and confidence scoring.",
    icon: "📊",
  },
  {
    title: "Legendary Investor Analysis",
    desc: "Buffett, Munger, Lynch, Burry, Ackman & Graham each weigh in with a score, verdict and bull/bear case via AI.",
    icon: "🧠",
  },
  {
    title: "Financial Trends & Charts",
    desc: "Revenue, EPS, FCF, margins, price history and historical P/E — interactive charts with auto-calculated CAGR.",
    icon: "📈",
  },
  {
    title: "Insider Trades",
    desc: "Real-time SEC Form 4 filings — see exactly what executives and directors are buying and selling.",
    icon: "🕵️",
  },
  {
    title: "Institutional Holdings",
    desc: "13F filings reveal whether Berkshire, Pershing Square, Bridgewater and other legends hold the stock.",
    icon: "🏛️",
  },
  {
    title: "Key Metrics & Analyst Ratings",
    desc: "P/E, EV/EBITDA, margins, debt ratios plus Wall Street consensus rating and price targets.",
    icon: "🔑",
  },
  {
    title: "Compare Stocks",
    desc: "Put 2–3 tickers side by side — valuation, margin of safety, F-score and investor verdicts at a glance.",
    icon: "⚖️",
  },
  {
    title: "Portfolio Tracking",
    desc: "Save stocks to your portfolio and track intrinsic value, margin of safety and F-score in one place.",
    icon: "💼",
  },
  {
    title: "PDF Report Export",
    desc: "Generate a polished research report with thesis, charts and investor takes — download it as a PDF.",
    icon: "📄",
  },
  {
    title: "Stock Screener",
    desc: "Filter the S&P 500 by margin of safety and F-score to surface undervalued, high-quality ideas.",
    icon: "🔎",
  },
];

const FEATURES_SOON: { title: string; desc: string }[] = [];

export default function Home() {
  return (
    <div className="flex flex-col min-h-full">
      {/* Hero */}
      <section className="relative flex flex-col items-center justify-center px-6 py-28 overflow-hidden">
        <ChartLine />
        <GlowParticles />

        <div className="relative z-10 flex flex-col items-center gap-6 max-w-2xl text-center">
          <motion.h1
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="text-6xl font-bold tracking-tight text-moat-text"
          >
            Find undervalued stocks
            <br />
            <span className="text-moat-accent">before the market does</span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.15 }}
            className="text-lg text-moat-text-muted max-w-lg"
          >
            Professional-grade intrinsic value analysis — blended DCF, multi-factor
            relative valuation, and sector-aware adjustments. Free and open.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.3 }}
            className="w-full max-w-md"
          >
            <Card className="w-full">
              <TickerSearch placeholder="Search any stock (e.g. AAPL, Microsoft, Tesla)" />
            </Card>
          </motion.div>

          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.6 }}
            className="text-sm text-moat-text-muted font-mono"
          >
            Analyzing <span className="text-moat-accent font-semibold"><AnimatedCounter target={5000} />+</span> stocks in real-time
          </motion.p>
        </div>
      </section>

      {/* What is Moat — narrative / value-prop block */}
      <section className="px-6 py-12">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mx-auto max-w-5xl rounded-3xl border border-moat-border bg-moat-surface/60 px-8 py-10"
        >
          <h2 className="text-2xl sm:text-3xl font-bold text-moat-text text-center">
            Hedge-fund-grade research, without the Bloomberg terminal
          </h2>
          <p className="mt-4 max-w-3xl mx-auto text-center text-moat-text-muted leading-relaxed">
            Most retail investors can&apos;t access the depth of analysis that
            professionals pay tens of thousands a year for. Moat closes that gap: a
            blended intrinsic-value engine, AI-powered analysis from six legendary
            investors, and full 15-section diligence reports — all built from real SEC
            filings and live market data. It&apos;s not one DCF number you have to trust
            blindly: multiple independent valuation methods are cross-checked, six
            distinct investor personas reason from the actual numbers (not generic
            chatbot output), and real insider &amp; institutional ownership signals are
            pulled straight from the source.
          </p>

          <div className="mt-10 grid grid-cols-2 md:grid-cols-4 gap-6">
            {[
              ["6", "AI Investor Personas"],
              ["15", "Section Deep Research"],
              ["3", "Independent Valuation Models"],
              ["Live", "SEC Filings (Form 4 & 13F)"],
            ].map(([num, label]) => (
              <div key={label} className="flex flex-col items-center text-center gap-1">
                <span className="text-4xl font-bold font-mono text-moat-accent">
                  {num}
                </span>
                <span className="text-xs uppercase tracking-widest text-moat-text-muted">
                  {label}
                </span>
              </div>
            ))}
          </div>
        </motion.div>
      </section>

      {/* Features */}
      <section id="features" className="px-6 py-16 mx-auto max-w-6xl w-full">
        <motion.h2
          initial={{ opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-2xl font-bold text-moat-text text-center mb-10"
        >
          Everything you need to value a stock
        </motion.h2>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES_LIVE.map((f, i) => (
            <motion.div
              key={f.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.08 }}
            >
              <Card hover className="h-full">
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{f.icon}</span>
                    <h3 className="text-sm font-semibold text-moat-text">{f.title}</h3>
                  </div>
                  <p className="text-xs text-moat-text-muted leading-relaxed">{f.desc}</p>
                </div>
              </Card>
            </motion.div>
          ))}
          {FEATURES_SOON.map((f, i) => (
            <motion.div
              key={f.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: (FEATURES_LIVE.length + i) * 0.08 }}
            >
              <Card className="h-full opacity-50">
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <Badge variant="warning">Coming Soon</Badge>
                    <h3 className="text-sm font-semibold text-moat-text">{f.title}</h3>
                  </div>
                  <p className="text-xs text-moat-text-muted leading-relaxed">{f.desc}</p>
                </div>
              </Card>
            </motion.div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="mt-auto border-t border-moat-border/50 py-6 px-6">
        <div className="mx-auto max-w-6xl flex items-center justify-center">
          <span className="text-xs text-moat-text-muted">
            Moat — Intrinsic value analysis. Not financial advice.
          </span>
        </div>
      </footer>
    </div>
  );
}
