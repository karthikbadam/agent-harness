import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { jobsApi } from "../api/jobs";
import type { FollowupCreate, JobCreate } from "../types";

export const jobsKey = ["jobs"] as const;
export const jobKey = (id: string) => ["job", id] as const;

export function useJobs() {
  return useQuery({
    queryKey: jobsKey,
    queryFn: jobsApi.list,
    refetchInterval: 5_000,
  });
}

export function useJob(id: string | undefined) {
  return useQuery({
    queryKey: jobKey(id ?? ""),
    queryFn: () => jobsApi.get(id!),
    enabled: Boolean(id),
  });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JobCreate) => jobsApi.create(body),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: jobsKey });
      qc.setQueryData(jobKey(job.id), job);
    },
  });
}

export function useFollowup(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FollowupCreate) => jobsApi.followup(id, body),
    onSuccess: (job) => {
      qc.setQueryData(jobKey(id), job);
      qc.invalidateQueries({ queryKey: jobsKey });
    },
  });
}

export function useStopJob(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => jobsApi.stop(id),
    onSuccess: (job) => {
      qc.setQueryData(jobKey(id), job);
      qc.invalidateQueries({ queryKey: jobsKey });
    },
  });
}

export function useDeleteJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => jobsApi.remove(id),
    onSuccess: (_void, id) => {
      qc.removeQueries({ queryKey: jobKey(id) });
      qc.invalidateQueries({ queryKey: jobsKey });
    },
  });
}
