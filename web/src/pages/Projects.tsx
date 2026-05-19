import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Center,
  Flex,
  Heading,
  HStack,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { LuChevronRight, LuFolderGit2 } from "react-icons/lu";

import { Shell } from "../components/Shell";
import { NewProjectComposer } from "../components/NewProjectComposer";
import { StickyComposer } from "../components/StickyComposer";
import { SwipeableRow } from "../components/SwipeableRow";
import { parseServerDate } from "../api/dates";
import { projectsApi } from "../api/projects";
import { useQueryClient } from "@tanstack/react-query";
import {
  projectsKey,
  useProjects,
} from "../hooks/useProjects";
import { useJobs } from "../hooks/useJobs";
import { useAllTasks } from "../hooks/useTasks";
import type { JobOut, ProjectOut, TaskOut } from "../types";

export function ProjectsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: projects, isLoading } = useProjects();
  const { data: jobs } = useJobs();
  const { data: tasks } = useAllTasks();
  const sorted = useMemo(() => sortProjects(projects ?? []), [projects]);
  const stats = useMemo(
    () => indexStats(jobs ?? [], tasks ?? []),
    [jobs, tasks],
  );

  const handleDelete = async (p: ProjectOut) => {
    try {
      await projectsApi.remove(p.id);
      qc.invalidateQueries({ queryKey: projectsKey });
    } catch (err) {
      console.error("delete project failed:", err);
      alert(
        `Could not delete project "${p.name}". The server may have rejected the request (open tasks?).`,
      );
    }
  };

  return (
    <Shell title="Projects" composerHeight={110}>
      {isLoading && (
        <Center py={10}>
          <Spinner />
        </Center>
      )}
      {projects && projects.length === 0 && <EmptyProjectsHint />}
      {sorted.length > 0 && (
        <Stack gap={2} maxW="container.md">
          {sorted.map((p) => (
            <SwipeableRow
              key={p.id}
              disabled={p.is_default}
              confirmMessage={`Delete project "${p.name}"? Tasks and jobs are removed too.`}
              onDelete={() => handleDelete(p)}
            >
              <ProjectRow
                project={p}
                stats={stats[p.id] ?? emptyStats()}
                onClick={() => navigate(`/projects/${p.id}`)}
              />
            </SwipeableRow>
          ))}
        </Stack>
      )}
      <StickyComposer>
        <NewProjectComposer />
      </StickyComposer>
    </Shell>
  );
}

interface ProjectStats {
  jobs: number;
  jobsRunning: number;
  tasksDone: number;
  tasksTotal: number;
}

const emptyStats = (): ProjectStats => ({
  jobs: 0,
  jobsRunning: 0,
  tasksDone: 0,
  tasksTotal: 0,
});

function indexStats(
  jobs: JobOut[],
  tasks: TaskOut[],
): Record<string, ProjectStats> {
  const m: Record<string, ProjectStats> = {};
  for (const j of jobs) {
    const s = m[j.project_id] ?? emptyStats();
    s.jobs += 1;
    if (j.status === "running" || j.status === "queued") s.jobsRunning += 1;
    m[j.project_id] = s;
  }
  for (const t of tasks) {
    const s = m[t.project_id] ?? emptyStats();
    s.tasksTotal += 1;
    if (t.status === "done") s.tasksDone += 1;
    m[t.project_id] = s;
  }
  return m;
}

function sortProjects(projects: ProjectOut[]): ProjectOut[] {
  // Default project pinned to the bottom; everything else newest-first by
  // creation timestamp — matches the reverse-chronological feed convention
  // used on Jobs (recent activity surfaces at the top).
  const user = projects
    .filter((p) => !p.is_default)
    .sort(
      (a, b) =>
        parseServerDate(b.created_at).getTime() -
        parseServerDate(a.created_at).getTime(),
    );
  const system = projects.filter((p) => p.is_default);
  return [...user, ...system];
}

function ProjectRow({
  project,
  stats,
  onClick,
}: {
  project: ProjectOut;
  stats: ProjectStats;
  onClick: () => void;
}) {
  return (
    <Flex
      bg="bg"
      rounded="lg"
      px={4}
      py={3.5}
      cursor="pointer"
      align="center"
      gap={3}
      onClick={onClick}
      _hover={{ bg: "bg.muted" }}
      transition="background-color 0.15s"
    >
      <Box
        boxSize="9"
        rounded="md"
        bg="bg.subtle"
        borderWidth="1px"
        borderColor="border.subtle"
        display="flex"
        alignItems="center"
        justifyContent="center"
        fontSize="md"
        color="fg.muted"
        flexShrink={0}
      >
        <LuFolderGit2 />
      </Box>
      <Stack gap={0.5} flex="1" minW={0}>
        <Flex align="center" gap={2}>
          <Text fontWeight="medium" lineHeight="short" truncate>
            {project.name}
          </Text>
          {project.is_default && (
            <Text
              fontSize="2xs"
              color="fg.muted"
              textTransform="uppercase"
              letterSpacing="wider"
            >
              default
            </Text>
          )}
        </Flex>
        <Text fontSize="xs" color="fg.subtle" fontFamily="mono" truncate>
          {project.path}
        </Text>
      </Stack>
      <HStack gap={3} fontSize="xs" color="fg.muted" flexShrink={0}>
        {stats.jobsRunning > 0 && (
          <HStack gap={1.5}>
            <Box boxSize="2" rounded="full" bg="blue.solid" />
            <Text>{stats.jobsRunning} running</Text>
          </HStack>
        )}
        {stats.tasksTotal > 0 && (
          <Text>
            {stats.tasksDone === stats.tasksTotal
              ? `${stats.tasksTotal} tasks`
              : `${stats.tasksDone}/${stats.tasksTotal} tasks`}
          </Text>
        )}
        {stats.jobs > 0 && stats.jobsRunning === 0 && (
          <Text>{stats.jobs} jobs</Text>
        )}
        <Box color="fg.subtle" lineHeight="0">
          <LuChevronRight />
        </Box>
      </HStack>
    </Flex>
  );
}

function EmptyProjectsHint() {
  return (
    <Center py={12}>
      <Stack gap={2} align="center" maxW="md" textAlign="center">
        <Heading size="sm" color="fg.muted">
          No projects
        </Heading>
        <Text fontSize="sm" color="fg.subtle">
          Pick a path from the composer below and add a project.
        </Text>
      </Stack>
    </Center>
  );
}
