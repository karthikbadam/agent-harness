import { useUI } from "../stores/ui";
import { ApiError } from "./client";

export interface AttachmentOut {
  id: string;
  project_id: string | null;
  job_id: string | null;
  filename: string;
  mime_type: string;
  url: string;
  created_at: string;
}

async function handleJson<T>(resp: Response): Promise<T> {
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

function authHeader(): Record<string, string> {
  const t = useUI.getState().token;
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export const attachmentsApi = {
  upload: async (file: File, projectId?: string): Promise<AttachmentOut> => {
    const form = new FormData();
    form.append("file", file);
    if (projectId) form.append("project_id", projectId);
    const resp = await fetch("/api/attachments", {
      method: "POST",
      headers: authHeader(),
      body: form,
    });
    return handleJson<AttachmentOut>(resp);
  },

  remove: async (id: string): Promise<void> => {
    await fetch(`/api/attachments/${id}`, {
      method: "DELETE",
      headers: authHeader(),
    });
  },
};
