import { api } from "./client";
import type { JobCreate, JobOut, FollowupCreate } from "../types";

export const jobsApi = {
  list: () => api.get<JobOut[]>("/api/jobs"),
  get: (id: string) => api.get<JobOut>(`/api/jobs/${id}`),
  create: (body: JobCreate) => api.post<JobOut>("/api/jobs", body),
  followup: (id: string, body: FollowupCreate) =>
    api.post<JobOut>(`/api/jobs/${id}/followup`, body),
  stop: (id: string) => api.post<JobOut>(`/api/jobs/${id}/stop`),
};
