import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { allowlistApi } from "../api/allowlist";
import type { AllowlistRuleCreate } from "../types";

export const allowlistKey = (projectId?: string) =>
  ["allowlist", projectId ?? null] as const;

export function useAllowlist(projectId?: string) {
  return useQuery({
    queryKey: allowlistKey(projectId),
    queryFn: () => allowlistApi.list(projectId),
  });
}

export function useCreateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AllowlistRuleCreate) => allowlistApi.create(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["allowlist"] });
    },
  });
}

export function useDeleteRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => allowlistApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["allowlist"] });
    },
  });
}
