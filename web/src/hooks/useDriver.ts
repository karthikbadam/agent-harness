import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { driverApi } from "../api/driver";

export const driverKey = (projectId: string) => ["driver", projectId] as const;
export const driverNotesKey = (projectId: string) =>
  ["driver-notes", projectId] as const;

export function useDriver(projectId: string | undefined) {
  return useQuery({
    queryKey: driverKey(projectId ?? ""),
    queryFn: () => driverApi.get(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 5_000,
  });
}

export function useDriverNotes(projectId: string | undefined) {
  return useQuery({
    queryKey: driverNotesKey(projectId ?? ""),
    queryFn: () => driverApi.notes(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 5_000,
  });
}

export function useSetDriverMode(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mode: "off" | "on") => driverApi.setMode(projectId, mode),
    onSuccess: (data) => {
      qc.setQueryData(driverKey(projectId), data);
      qc.invalidateQueries({ queryKey: driverNotesKey(projectId) });
    },
  });
}
