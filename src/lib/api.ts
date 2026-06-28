/**
 * Central configuration for the backend (FastAPI) API.
 *
 * All frontend network calls resolve their base URL from here so the target
 * can be changed per environment via `NEXT_PUBLIC_API_BASE_URL` without editing
 * call sites. Falls back to the local dev server when the variable is unset,
 * preserving the previous hardcoded behavior.
 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Build an absolute backend URL from a root-relative path (e.g. `/analyze/AAPL`). */
export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}
