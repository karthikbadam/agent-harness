import { api } from "./client";
import type { components } from "../types/api";

type DriverState = components["schemas"]["DriverStateOut"];
type DriverNote = components["schemas"]["DriverNoteOut"];

export const driverApi = {
  get: (projectId: string) =>
    api.get<DriverState>(`/api/projects/${projectId}/driver`),
  setMode: (projectId: string, mode: "off" | "on") =>
    api.patch<DriverState>(`/api/projects/${projectId}/driver`, { mode }),
  notes: (projectId: string, opts?: { unack?: boolean }) => {
    const params = opts?.unack ? "?acknowledged=false" : "";
    return api.get<DriverNote[]>(
      `/api/projects/${projectId}/driver/notes${params}`,
    );
  },
};

export type { DriverState, DriverNote };
