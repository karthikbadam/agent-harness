import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AuthInfo } from "../types";
import { useUI } from "../stores/ui";

/**
 * Verifies the stored token AND establishes the session cookie by POSTing the
 * bearer token to /api/session. The cookie is what authenticates SSE (and any
 * same-origin request), so we mint a fresh one whenever auth is (re)checked —
 * this runs on app load before any EventSource opens. Returns:
 *   isAuthed === true  → token is valid (cookie set)
 *   isAuthed === false → no token or token rejected
 *   isLoading          → still checking
 */
export function useAuth() {
  const token = useUI((s) => s.token);
  const q = useQuery<AuthInfo>({
    queryKey: ["auth", token],
    queryFn: () => api.post<AuthInfo>("/api/session"),
    enabled: Boolean(token),
    retry: false,
    staleTime: 60_000,
  });
  return {
    token,
    isLoading: Boolean(token) && q.isLoading,
    isAuthed: Boolean(token) && q.isSuccess,
    error: q.error,
  };
}
