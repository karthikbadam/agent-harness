import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AuthInfo } from "../types";
import { useUI } from "../stores/ui";

/**
 * Pings /api/me to verify the stored token. Returns:
 *   isAuthed === true  → token is valid
 *   isAuthed === false → no token or token rejected
 *   isLoading          → still checking
 */
export function useAuth() {
  const token = useUI((s) => s.token);
  const q = useQuery<AuthInfo>({
    queryKey: ["auth", token],
    queryFn: () => api.get<AuthInfo>("/api/me"),
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
