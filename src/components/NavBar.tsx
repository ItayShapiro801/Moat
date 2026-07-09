"use client";

import { useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { getSupabaseClient } from "@/lib/supabase/client";
import { useAuth } from "@/lib/auth-context";

const NAV_LINKS = [
  { label: "Home", href: "/" },
  { label: "Screener", href: "/screener" },
  { label: "Compare", href: "/compare" },
];

type Mode = "login" | "signup" | "forgot";

interface Msg {
  type: "success" | "error";
  text: string;
}

function AuthModal({
  initialMode,
  onClose,
}: {
  initialMode: Mode;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);

  const supabase = getSupabaseClient();
  const notConfigured = !supabase;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (!supabase) {
      setMsg({ type: "error", text: "Authentication is not configured yet." });
      return;
    }
    setBusy(true);
    try {
      if (mode === "signup") {
        const { error } = await supabase.auth.signUp({
          email,
          password,
          options: { data: { username } },
        });
        if (error) throw error;
        setMsg({
          type: "success",
          text: "Check your email to verify your account.",
        });
      } else if (mode === "login") {
        const { error } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (error) throw error;
        setMsg({ type: "success", text: "Logged in successfully." });
        setTimeout(onClose, 600);
      } else if (mode === "forgot") {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${window.location.origin}/reset-password`,
        });
        if (error) throw error;
        setMsg({ type: "success", text: "Password reset link sent — check your email." });
      }
    } catch (err: unknown) {
      const text =
        mode === "login"
          ? "Invalid email or password."
          : err instanceof Error
            ? err.message
            : "Something went wrong.";
      setMsg({ type: "error", text });
    } finally {
      setBusy(false);
    }
  }

  async function handleGoogle() {
    setMsg(null);
    if (!supabase) {
      setMsg({ type: "error", text: "Authentication is not configured yet." });
      return;
    }
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/` },
    });
    if (error) {
      setMsg({
        type: "error",
        text:
          "Google sign-in isn't enabled yet. Enable the Google provider in the Supabase dashboard.",
      });
    }
  }

  const title =
    mode === "login"
      ? "Welcome back"
      : mode === "signup"
        ? "Create your account"
        : "Reset your password";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="relative w-full max-w-sm rounded-2xl border border-moat-border bg-moat-surface p-8"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-moat-text-muted hover:text-moat-text text-lg"
          aria-label="Close"
        >
          ×
        </button>
        <div className="flex flex-col items-center gap-4">
          <span className="text-2xl font-bold text-moat-accent">Moat</span>
          <h2 className="text-lg font-semibold text-moat-text">{title}</h2>

          {notConfigured && (
            <p className="text-xs text-moat-warning text-center">
              Supabase isn&apos;t configured. Add your project URL and anon key to
              .env.local to enable sign-in.
            </p>
          )}

          {msg && (
            <p
              className={`text-sm text-center ${
                msg.type === "success" ? "text-moat-accent" : "text-moat-danger"
              }`}
            >
              {msg.text}
            </p>
          )}

          <form onSubmit={handleSubmit} className="w-full flex flex-col gap-3">
            {mode === "signup" && (
              <input
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Username"
                minLength={3}
                className="rounded-lg border border-moat-border bg-moat-bg px-4 py-2.5 text-moat-text placeholder:text-moat-text-muted text-sm focus:outline-none focus:ring-2 focus:ring-moat-accent"
              />
            )}
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Email address"
              className="rounded-lg border border-moat-border bg-moat-bg px-4 py-2.5 text-moat-text placeholder:text-moat-text-muted text-sm focus:outline-none focus:ring-2 focus:ring-moat-accent"
            />
            {mode !== "forgot" && (
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                minLength={6}
                className="rounded-lg border border-moat-border bg-moat-bg px-4 py-2.5 text-moat-text placeholder:text-moat-text-muted text-sm focus:outline-none focus:ring-2 focus:ring-moat-accent"
              />
            )}
            <button
              type="submit"
              disabled={busy}
              className="w-full py-2.5 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors disabled:opacity-50"
            >
              {busy
                ? "Please wait…"
                : mode === "login"
                  ? "Log in"
                  : mode === "signup"
                    ? "Sign up"
                    : "Send reset link"}
            </button>
          </form>

          {mode !== "forgot" && (
            <>
              <div className="flex items-center gap-3 w-full">
                <div className="h-px flex-1 bg-moat-border" />
                <span className="text-xs text-moat-text-muted">or</span>
                <div className="h-px flex-1 bg-moat-border" />
              </div>
              <button
                onClick={handleGoogle}
                className="w-full py-2.5 rounded-lg border border-moat-border bg-moat-bg text-moat-text text-sm font-medium hover:bg-moat-surface-hover transition-colors"
              >
                Continue with Google
              </button>
            </>
          )}

          <div className="flex flex-col items-center gap-1 text-xs">
            {mode === "login" && (
              <>
                <button
                  onClick={() => {
                    setMode("forgot");
                    setMsg(null);
                  }}
                  className="text-moat-text-muted hover:text-moat-accent transition-colors"
                >
                  Forgot password?
                </button>
                <button
                  onClick={() => {
                    setMode("signup");
                    setMsg(null);
                  }}
                  className="text-moat-text-muted hover:text-moat-accent transition-colors"
                >
                  Don&apos;t have an account? Sign up
                </button>
              </>
            )}
            {mode === "signup" && (
              <button
                onClick={() => {
                  setMode("login");
                  setMsg(null);
                }}
                className="text-moat-text-muted hover:text-moat-accent transition-colors"
              >
                Already have an account? Log in
              </button>
            )}
            {mode === "forgot" && (
              <button
                onClick={() => {
                  setMode("login");
                  setMsg(null);
                }}
                className="text-moat-text-muted hover:text-moat-accent transition-colors"
              >
                Back to login
              </button>
            )}
          </div>
        </div>
      </motion.div>
    </div>
  );
}

export function NavBar() {
  const { user, signOut, authMode, openAuth, closeAuth } = useAuth();

  return (
    <>
      <nav className="sticky top-0 z-50 border-b border-moat-border/50 bg-moat-bg/80 backdrop-blur-md">
        <div className="mx-auto max-w-6xl flex items-center justify-between px-6 py-3">
          <Link href="/" className="text-xl font-bold text-moat-accent tracking-tight">
            Moat
          </Link>
          <div className="flex items-center gap-6">
            {NAV_LINKS.map((l) => (
              <Link
                key={l.label}
                href={l.href}
                className="text-sm text-moat-text-muted hover:text-moat-text transition-colors"
              >
                {l.label}
              </Link>
            ))}
            <Link
              href="/portfolio"
              className="text-sm text-moat-text-muted hover:text-moat-text transition-colors"
            >
              Portfolio
            </Link>
            {user ? (
              <>
                <span className="text-sm text-moat-text-muted hidden sm:inline">
                  Logged in as{" "}
                  <span className="text-moat-text">
                    {(user.user_metadata?.username as string) || user.email}
                  </span>
                </span>
                <button
                  onClick={signOut}
                  className="px-4 py-1.5 rounded-lg border border-moat-border text-moat-text text-sm font-medium hover:bg-moat-surface-hover transition-colors"
                >
                  Log out
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => openAuth("login")}
                  className="text-sm text-moat-text-muted hover:text-moat-text transition-colors"
                >
                  Login
                </button>
                <button
                  onClick={() => openAuth("signup")}
                  className="px-4 py-1.5 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors"
                >
                  Sign Up
                </button>
              </>
            )}
          </div>
        </div>
      </nav>

      {authMode && (
        <AuthModal initialMode={authMode} onClose={closeAuth} />
      )}
    </>
  );
}
