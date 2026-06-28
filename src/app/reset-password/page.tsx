"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { getSupabaseClient } from "@/lib/supabase/client";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(
    null
  );
  const [ready, setReady] = useState(false);

  // Supabase puts the recovery session in place when the user arrives via the
  // emailed link; confirm we have a session before allowing a password change.
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!supabase) {
      setMsg({ type: "error", text: "Authentication is not configured." });
      return;
    }
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) setReady(true);
      else
        setMsg({
          type: "error",
          text: "Open this page from the password reset link in your email.",
        });
    });
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (password !== confirm) {
      setMsg({ type: "error", text: "Passwords do not match." });
      return;
    }
    const supabase = getSupabaseClient();
    if (!supabase) return;
    setBusy(true);
    try {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) throw error;
      setMsg({ type: "success", text: "Password updated. Redirecting…" });
      setTimeout(() => router.push("/"), 1200);
    } catch (err: unknown) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "Could not update password.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 py-20">
      <Card className="w-full max-w-sm">
        <div className="flex flex-col gap-4">
          <h1 className="text-lg font-semibold text-moat-text text-center">
            Set a new password
          </h1>

          {msg && (
            <p
              className={`text-sm text-center ${
                msg.type === "success" ? "text-moat-accent" : "text-moat-danger"
              }`}
            >
              {msg.text}
            </p>
          )}

          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <input
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="New password"
              disabled={!ready}
              className="rounded-lg border border-moat-border bg-moat-bg px-4 py-2.5 text-moat-text placeholder:text-moat-text-muted text-sm focus:outline-none focus:ring-2 focus:ring-moat-accent disabled:opacity-50"
            />
            <input
              type="password"
              required
              minLength={6}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="Confirm new password"
              disabled={!ready}
              className="rounded-lg border border-moat-border bg-moat-bg px-4 py-2.5 text-moat-text placeholder:text-moat-text-muted text-sm focus:outline-none focus:ring-2 focus:ring-moat-accent disabled:opacity-50"
            />
            <Button type="submit" disabled={busy || !ready}>
              {busy ? "Updating…" : "Update password"}
            </Button>
          </form>
        </div>
      </Card>
    </div>
  );
}
