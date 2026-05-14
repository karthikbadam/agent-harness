import { api } from "./client";
import type { ProjectCreate, ProjectOut, ProjectUpdate } from "../types";

export const projectsApi = {
  list: () => api.get<ProjectOut[]>("/api/projects"),
  create: (body: ProjectCreate) => api.post<ProjectOut>("/api/projects", body),
  update: (id: string, body: ProjectUpdate) =>
    api.patch<ProjectOut>(`/api/projects/${id}`, body),
  remove: (id: string) => api.del(`/api/projects/${id}`),
};
