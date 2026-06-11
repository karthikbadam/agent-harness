import { api, sseUrl } from "./client";
import type {
  ArtifactOut,
  IterationOut,
  JobOut,
  LastPlanOut,
  LoopCreate,
  TaskCreate,
  TaskOut,
  TaskUpdate,
  OutcomeOut,
} from "../types";

export const tasksApi = {
  listAll: () => api.get<TaskOut[]>(`/api/tasks`),
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
  confirm: (id: string) => api.post<TaskOut[]>(`/api/tasks/${id}/confirm`, {}),
  outcomes: (id: string) => api.get<OutcomeOut[]>(`/api/tasks/${id}/outcomes`),
  plan: (projectId: string, ask: string) =>
    api.post<{
      task_ids: string[];
      raw?: string | null;
      error?: string | null;
    }>(`/api/projects/${projectId}/plan`, { ask }),
  lastPlan: (projectId: string) =>
    api.get<LastPlanOut | null>(`/api/projects/${projectId}/plan`),

  // Loop (autoresearch) task surface.
  createLoop: (projectId: string, body: LoopCreate, opts?: { run?: boolean }) =>
    api.post<TaskOut>(
      `/api/projects/${projectId}/loops${opts?.run === false ? "?run=false" : ""}`,
      body,
    ),
  listIterations: (id: string) =>
    api.get<IterationOut[]>(`/api/tasks/${id}/iterations`),

  // Artifacts (graphs/tables/reports a task produced).
  listArtifacts: (id: string) =>
    api.get<ArtifactOut[]>(`/api/tasks/${id}/artifacts`),
  // Browser <img>/<a> can't set the auth header, so carry the token in the
  // query string (the download route accepts ?token=, same as SSE).
  artifactUrl: (artifactId: string) =>
    sseUrl(`/api/artifacts/${artifactId}/download`),
};
