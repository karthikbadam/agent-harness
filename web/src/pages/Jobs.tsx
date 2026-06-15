import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
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
import {
  ProviderPicker,
  type ProviderChoice,
} from "../components/ProviderPicker";
import { StickyComposer } from "../components/StickyComposer";
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
  const [provider, setProvider] = useState<ProviderChoice>("inherit");

  const jobs = useMemo(() => {
    if (!allJobs) return undefined;
    let out = allJobs;
    if (taskFilter) out = out.filter((j) => j.task_id === taskFilter);
    if (projectFilter) out = out.filter((j) => j.project_id === projectFilter);
    return out;
  }, [allJobs, taskFilter, projectFilter]);

  const groups = useMemo(
    () => groupByProject(jobs ?? [], projects ?? []),
    [jobs, projects],
  );

  const hasFilter = Boolean(taskFilter || projectFilter);
  const clearFilters = () => {
    const next = new URLSearchParams(params);
    next.delete("task_id");
    next.delete("project_id");
    setParams(next);
  };

  const title = taskFilter
    ? "Task jobs"
    : projectFilter
      ? (projects?.find((p) => p.id === projectFilter)?.name ?? "Project jobs")
      : "Jobs";
  const subtitle = taskFilter
    ? `Filtered to task ${taskFilter.slice(0, 8)}`
    : projectFilter
      ? "All jobs in this project"
      : undefined;

  return (
    <Shell
      title={title}
      subtitle={subtitle}
      composerHeight={110}
      right={
        hasFilter ? (
          <Button size="xs" variant="ghost" onClick={clearFilters}>
            Clear filter
          </Button>
        ) : undefined
      }
    >
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
      <StickyComposer>
        <Flex px={4} pt={2} justify="flex-end">
          <ProviderPicker
            value={provider}
            onChange={setProvider}
            includeInherit
          />
        </Flex>
        <Composer
          placeholder="Start an ad-hoc job…"
          projectId={projectFilter || undefined}
          onSend={async (prompt, attachmentIds) => {
            const job = await createJob.mutateAsync({
              prompt,
              project_id: projectFilter || undefined,
              attachment_ids: attachmentIds,
              agent_provider: provider === "inherit" ? undefined : provider,
            });
            navigate(`/jobs/${job.id}`);
          }}
        />
      </StickyComposer>
    </Shell>
  );
}

interface ProjectGroup {
  projectId: string;
  label: string;
  jobs: JobOut[];
}

function groupByProject(
  jobs: JobOut[],
  projects: ProjectOut[],
): ProjectGroup[] {
  const nameById = new Map(projects.map((p) => [p.id, p.name]));
  const byProj = new Map<string, JobOut[]>();
  for (const j of jobs) {
    const arr = byProj.get(j.project_id) ?? [];
    arr.push(j);
    byProj.set(j.project_id, arr);
  }
  for (const arr of byProj.values()) {
    arr.sort(
      (a, b) =>
        parseServerDate(b.created_at).getTime() -
        parseServerDate(a.created_at).getTime(),
    );
  }
  const groups: ProjectGroup[] = Array.from(byProj.entries()).map(
    ([pid, js]) => ({
      projectId: pid,
      label: nameById.get(pid) ?? pid.slice(0, 8),
      jobs: js,
    }),
  );
  groups.sort(
    (a, b) =>
      parseServerDate(b.jobs[0]!.created_at).getTime() -
      parseServerDate(a.jobs[0]!.created_at).getTime(),
  );
  return groups;
}
