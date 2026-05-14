import { api } from "./client";
import type { ScheduleCreate, ScheduleOut, ScheduleUpdate } from "../types";

export const schedulesApi = {
  list: () => api.get<ScheduleOut[]>("/api/schedules"),
  create: (body: ScheduleCreate) => api.post<ScheduleOut>("/api/schedules", body),
  update: (id: string, body: ScheduleUpdate) =>
    api.patch<ScheduleOut>(`/api/schedules/${id}`, body),
  remove: (id: string) => api.del(`/api/schedules/${id}`),
};
