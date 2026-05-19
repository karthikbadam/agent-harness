import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { projectsApi } from "../api/projects";
import type { ProjectCreate, ProjectUpdate } from "../types";

export const projectsKey = ["projects"] as const;
export const pathSuggestionsKey = ["path-suggestions"] as const;

export function useProjects() {
  return useQuery({ queryKey: projectsKey, queryFn: projectsApi.list });
}

export function usePathSuggestions(enabled: boolean) {
  return useQuery({
    queryKey: pathSuggestionsKey,
    queryFn: () => projectsApi.pathSuggestions(),
    enabled,
    staleTime: 60_000,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectCreate) => projectsApi.create(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: projectsKey }),
  });
}

export function useUpdateProject(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectUpdate) => projectsApi.update(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: projectsKey }),
  });
}
