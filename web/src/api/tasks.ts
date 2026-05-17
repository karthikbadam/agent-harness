import { api } from "./client";
import type { JobOut, TaskCreate, TaskOut, TaskUpdate, OutcomeOut } from "../types";

export const tasksApi = {
  listForProject: (projectId: string) =>
    api.get<TaskOut[]>(`/api/projects/${projectId}/tasks`),
  get: (id: string) => api.get<TaskOut>(`/api/tasks/${id}`),
  create: (projectId: string, body: TaskCreate, opts?: { run?: boolean }) =>
    api.post<TaskOut>(
      `/api/projects/${projectId}/tasks${opts?.run ? "?run=true" : ""}`,
      body,
    ),
  update: (id: string, body: TaskUpdate) =>
    api.patch<TaskOut>(`/api/tasks/${id}`, body),
  remove: (id: string) => api.del(`/api/tasks/${id}`),
  run: (id: string) => api.post<JobOut>(`/api/tasks/${id}/run`, {}),
  ack: (id: string, notes = "") => {
    const path = notes
      ? `/api/tasks/${id}/ack?notes=${encodeURIComponent(notes)}`
      : `/api/tasks/${id}/ack`;
    return api.post<JobOut>(path, {});
  },
  retry: (id: string) => api.post<JobOut>(`/api/tasks/${id}/retry`, {}),
  restart: (id: string) => api.post<TaskOut>(`/api/tasks/${id}/restart`, {}),
  cancel: (id: string) => api.post<TaskOut>(`/api/tasks/${id}/cancel`, {}),
  outcomes: (id: string) =>
    api.get<OutcomeOut[]>(`/api/tasks/${id}/outcomes`),
  plan: (projectId: string, ask: string) =>
    api.post<{ task_ids: string[]; raw?: string | null; error?: string | null }>(
      `/api/projects/${projectId}/plan`,
      { ask },
    ),
};
