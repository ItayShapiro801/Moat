"use client";

import { createBrowserClient } from "@supabase/ssr";

// Supabase's "Project URL" is the base origin. The dashboard also shows a REST
// URL ending in /rest/v1/ — normalize to the base so the client builds correct
// paths whichever was pasted.
const rawUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const supabaseUrl = rawUrl
  ? rawUrl.replace(/\/rest\/v1\/?$/, "").replace(/\/+$/, "")
  : rawUrl;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

/**
 * True only when real Supabase env vars are configured (not the placeholders).
 * Lets the UI degrade gracefully before the project is set up.
 */
export const isSupabaseConfigured = Boolean(
  supabaseUrl &&
    supabaseAnonKey &&
    !supabaseUrl.includes("YOUR-PROJECT") &&
    !supabaseAnonKey.includes("YOUR-ANON")
);

let _client: ReturnType<typeof createBrowserClient> | null = null;

/** Singleton browser Supabase client. Returns null if not configured. */
export function getSupabaseClient() {
  if (!isSupabaseConfigured) return null;
  if (!_client) {
    _client = createBrowserClient(supabaseUrl!, supabaseAnonKey!);
  }
  return _client;
}
