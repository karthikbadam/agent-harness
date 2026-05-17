import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { tasksApi } from "../api/tasks";
import { jobsKey, jobKey } from "./useJobs";
import type { TaskCreate, TaskUpdate } from "../types";

export const tasksKey = (projectId: string) => ["tasks", projectId] as const;
export const taskKey = (id: string) => ["task", id] as const;
export const taskOutcomesKey = (id: string) => ["task-outcomes", id] as const;

export function useTasks(projectId: string | undefined) {
  return useQuery({
    queryKey: tasksKey(projectId ?? ""),
    queryFn: () => tasksApi.listForProject(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 5_000,
  });
}

export function useTask(id: string | undefined) {
  return useQuery({
    queryKey: taskKey(id ?? ""),
    queryFn: () => tasksApi.get(id!),
    enabled: Boolean(id),
    refetchInterval: 5_000,
  });
}

export function useTaskOutcomes(id: string | undefined) {
  return useQuery({
    queryKey: taskOutcomesKey(id ?? ""),
    queryFn: () => tasksApi.outcomes(id!),
    enabled: Boolean(id),
  });
}

function invalidateTasksFor(qc: ReturnType<typeof useQueryClient>, projectId: string) {
  qc.invalidateQueries({ queryKey: tasksKey(projectId) });
}

export function useCreateTask(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskCreate) => tasksApi.create(projectId, body),
    onSuccess: () => invalidateTasksFor(qc, projectId),
  });
}

export function useUpdateTask(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TaskUpdate }) =>
      tasksApi.update(id, body),
    onSuccess: (task) => {
      qc.setQueryData(taskKey(task.id), task);
      invalidateTasksFor(qc, projectId);
    },
  });
}

export function useDeleteTask(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => tasksApi.remove(id),
    onSuccess: () => invalidateTasksFor(qc, projectId),
  });
}

export function useRunTask(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => tasksApi.run(id),
    onSuccess: (job) => {
      invalidateTasksFor(qc, projectId);
      qc.invalidateQueries({ queryKey: jobsKey });
      qc.setQueryData(jobKey(job.id), job);
    },
  });
}

export function useAckTask(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, notes }: { id: string; notes?: string }) =>
      tasksApi.ack(id, notes ?? ""),
    onSuccess: (job) => {
      invalidateTasksFor(qc, projectId);
      qc.invalidateQueries({ queryKey: jobsKey });
      qc.setQueryData(jobKey(job.id), job);
    },
  });
}

export function useRetryTask(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => tasksApi.retry(id),
    onSuccess: () => {
      invalidateTasksFor(qc, projectId);
      qc.invalidateQueries({ queryKey: jobsKey });
    },
  });
}

export function useRestartTask(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => tasksApi.restart(id),
    onSuccess: () => invalidateTasksFor(qc, projectId),
  });
}

export function useCancelTask(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => tasksApi.cancel(id),
    onSuccess: () => invalidateTasksFor(qc, projectId),
  });
}

export function usePlan(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ask: string) => tasksApi.plan(projectId, ask),
    onSuccess: () => invalidateTasksFor(qc, projectId),
  });
}
