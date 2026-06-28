"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { getSupabaseClient } from "@/lib/supabase/client";

/**
 * Non-dismissable modal shown when a logged-in user has no username
 * (e.g. Google OAuth users, or accounts created before usernames existed).
 */
export function UsernamePrompt({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (username.trim().length < 3) {
      setError("Username must be at least 3 characters.");
      return;
    }
    const supabase = getSupabaseClient();
    if (!supabase) return;
    setBusy(true);
    try {
      const { error: err } = await supabase.auth.updateUser({
        data: { username: username.trim() },
      });
      if (err) throw err;
      onDone();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not save username.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="w-full max-w-sm rounded-2xl border border-moat-border bg-moat-surface p-8"
      >
        <div className="flex flex-col items-center gap-4">
          <span className="text-2xl font-bold text-moat-accent">Moat</span>
          <h2 className="text-lg font-semibold text-moat-text">
            Choose a username
          </h2>
          <p className="text-sm text-moat-text-muted text-center">
            Pick a username to finish setting up your account.
          </p>

          {error && (
            <p className="text-sm text-moat-danger text-center">{error}</p>
          )}

          <form onSubmit={handleSubmit} className="w-full flex flex-col gap-3">
            <input
              type="text"
              required
              minLength={3}
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Username"
              className="rounded-lg border border-moat-border bg-moat-bg px-4 py-2.5 text-moat-text placeholder:text-moat-text-muted text-sm focus:outline-none focus:ring-2 focus:ring-moat-accent"
            />
            <button
              type="submit"
              disabled={busy}
              className="w-full py-2.5 rounded-lg bg-moat-accent text-moat-bg text-sm font-medium hover:bg-moat-accent/90 transition-colors disabled:opacity-50"
            >
              {busy ? "Saving…" : "Continue"}
            </button>
          </form>
        </div>
      </motion.div>
    </div>
  );
}
