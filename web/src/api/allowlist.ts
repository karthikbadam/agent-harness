import { api } from "./client";
import type { AllowlistRuleCreate, AllowlistRuleOut } from "../types";

export const allowlistApi = {
  list: (projectId?: string) =>
    api.get<AllowlistRuleOut[]>(
      projectId ? `/api/allowlist?project_id=${projectId}` : "/api/allowlist"
    ),
  create: (body: AllowlistRuleCreate) => api.post<AllowlistRuleOut>("/api/allowlist", body),
  remove: (id: string) => api.del(`/api/allowlist/${id}`),
};
