import { useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Center,
  Flex,
  Heading,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { Composer } from "../components/Composer";
import { JobCard } from "../components/JobCard";
import { parseServerDate } from "../api/dates";
import { useCreateJob, useJobs } from "../hooks/useJobs";
import { useProjects } from "../hooks/useProjects";
import type { JobOut, ProjectOut } from "../types";

export function JobsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const taskFilter = params.get("task_id") ?? "";
  const projectFilter = params.get("project_id") ?? "";
  const { data: allJobs, isLoading, error } = useJobs();
  const { data: projects } = useProjects();
  const createJob = useCreateJob();

  const jobs = useMemo(() => {
    if (!allJobs) return undefined;
    let out = allJobs;
    if (taskFilter) out = out.filter((j) => j.task_id === taskFilter);
    if (projectFilter) out = out.filter((j) => j.project_id === projectFilter);
    return out;
  }, [allJobs, taskFilter, projectFilter]);

  const groups = useMemo(() => groupByProject(jobs ?? [], projects ?? []), [
    jobs,
    projects,
  ]);

  const hasFilter = taskFilter || projectFilter;
  const clearFilters = () => {
    const next = new URLSearchParams(params);
    next.delete("task_id");
    next.delete("project_id");
    setParams(next);
  };

  return (
    <Shell
      title={
        taskFilter
          ? `Jobs · task ${taskFilter.slice(0, 6)}`
          : projectFilter
            ? `Jobs · ${projects?.find((p) => p.id === projectFilter)?.name ?? "project"}`
            : "Jobs"
      }
      right={
        hasFilter ? (
          <Button size="xs" variant="ghost" onClick={clearFilters}>
            Clear filter
          </Button>
        ) : undefined
      }
    >
      <Box pb="calc(160px + env(safe-area-inset-bottom))">
        {isLoading && (
          <Center py={8}>
            <Spinner />
          </Center>
        )}
        {error && <Text color="red.fg">Failed to load jobs.</Text>}
        {jobs && jobs.length === 0 && (
          <Center py={12}>
            <Stack gap={2} align="center" maxW="md" textAlign="center">
              <Heading size="sm" color="fg.muted">
                {hasFilter ? "No matching jobs" : "No jobs yet"}
              </Heading>
              <Text fontSize="sm" color="fg.subtle">
                {hasFilter
                  ? "Try clearing the filter, or start a new job below."
                  : "Type a prompt below to start an ad-hoc job."}
              </Text>
            </Stack>
          </Center>
        )}
        <Stack gap={6} maxW="container.md">
          {groups.map((g) => (
            <Stack key={g.projectId} gap={2.5}>
              <Flex align="baseline" gap={2}>
                <Heading
                  size="xs"
                  color="fg.muted"
                  textTransform="uppercase"
                  letterSpacing="wider"
                  fontWeight="medium"
                  cursor="pointer"
                  _hover={{ color: "fg" }}
                  onClick={() => navigate(`/projects/${g.projectId}`)}
                >
                  {g.label}
                </Heading>
                <Text fontSize="2xs" color="fg.subtle">
                  {g.jobs.length}
                </Text>
              </Flex>
              <Stack gap={2}>
                {g.jobs.map((j) => (
                  <JobCard key={j.id} job={j} />
                ))}
              </Stack>
            </Stack>
          ))}
        </Stack>
      </Box>
      <Box
        position="fixed"
        left={{ base: 0, md: "224px" }}
        right={0}
        bottom={0}
        bg="bg"
        borderTopWidth="1px"
        borderColor="border.subtle"
        zIndex={5}
      >
        <Box maxW="container.md" mx={{ base: 0, md: "auto" }}>
          <Composer
            placeholder="Start an ad-hoc job…"
            onSend={async (prompt) => {
              const job = await createJob.mutateAsync({
                prompt,
                project_id: projectFilter || undefined,
              });
              navigate(`/jobs/${job.id}`);
            }}
          />
        </Box>
      </Box>
    </Shell>
  );
}

interface ProjectGroup {
  projectId: string;
  label: string;
  jobs: JobOut[];
}

function groupByProject(jobs: JobOut[], projects: ProjectOut[]): ProjectGroup[] {
  const nameById = new Map(projects.map((p) => [p.id, p.name]));
  const byProj = new Map<string, JobOut[]>();
  for (const j of jobs) {
    const arr = byProj.get(j.project_id) ?? [];
    arr.push(j);
    byProj.set(j.project_id, arr);
  }
  // Sort each project's jobs by created_at desc (newest first).
  for (const arr of byProj.values()) {
    arr.sort(
      (a, b) =>
        parseServerDate(b.created_at).getTime() -
        parseServerDate(a.created_at).getTime(),
    );
  }
  // Sort projects by their newest job (most-recently-active project first).
  const groups: ProjectGroup[] = Array.from(byProj.entries()).map(([pid, js]) => ({
    projectId: pid,
    label: nameById.get(pid) ?? pid.slice(0, 8),
    jobs: js,
  }));
  groups.sort(
    (a, b) =>
      parseServerDate(b.jobs[0]!.created_at).getTime() -
      parseServerDate(a.jobs[0]!.created_at).getTime(),
  );
  return groups;
}
