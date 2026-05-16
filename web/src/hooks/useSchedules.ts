import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { schedulesApi } from "../api/schedules";
import type { ScheduleCreate, ScheduleUpdate } from "../types";

export const schedulesKey = ["schedules"] as const;

export function useSchedules() {
  return useQuery({ queryKey: schedulesKey, queryFn: schedulesApi.list });
}

export function useCreateSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScheduleCreate) => schedulesApi.create(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: schedulesKey }),
  });
}

export function useUpdateSchedule(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScheduleUpdate) => schedulesApi.update(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: schedulesKey }),
  });
}

export function useDeleteSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => schedulesApi.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: schedulesKey }),
  });
}
