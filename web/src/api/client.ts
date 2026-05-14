/**
 * Minimal fetch wrapper. Bearer-auth from the Zustand store, JSON in/out,
 * typed error on non-2xx.
 */
import { useUI } from "../stores/ui";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

function authHeader(): Record<string, string> {
  const t = useUI.getState().token;
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function handle<T>(resp: Response): Promise<T> {
  if (resp.status === 204) return undefined as T;
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body?.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

export const api = {
  get: async <T,>(path: string): Promise<T> =>
    handle<T>(await fetch(path, { headers: { ...authHeader() } })),

  post: async <T,>(path: string, body?: unknown): Promise<T> =>
    handle<T>(
      await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeader() },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
    ),

  patch: async <T,>(path: string, body?: unknown): Promise<T> =>
    handle<T>(
      await fetch(path, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...authHeader() },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
    ),

  del: async (path: string): Promise<void> =>
    handle<void>(await fetch(path, { method: "DELETE", headers: { ...authHeader() } })),
};

export function sseUrl(path: string): string {
  const t = useUI.getState().token ?? "";
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(t)}`;
}
