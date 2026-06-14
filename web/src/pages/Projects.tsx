import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Center,
  Flex,
  Heading,
  HStack,
  IconButton,
  Image,
  Input,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import {
  LuPin,
  LuClock,
  LuSearch,
  LuX,
} from "react-icons/lu";

import { Shell } from "../components/Shell";
import { StickyComposer } from "../components/StickyComposer";
import { NewProjectComposer } from "../components/NewProjectComposer";
import { SwipeableRow } from "../components/SwipeableRow";
import { relativeCardDate, parseServerDate } from "../api/dates";
import { projectsApi } from "../api/projects";
import { useQueryClient } from "@tanstack/react-query";
import { projectsKey, useProjects } from "../hooks/useProjects";
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

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const displayed = useMemo(() => {
    if (!searchQuery.trim()) return sorted;
    const q = searchQuery.toLowerCase();
    return sorted.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.path.toLowerCase().includes(q) ||
        (p.instructions ?? "").toLowerCase().includes(q),
    );
  }, [sorted, searchQuery]);

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
    <Shell
      title="Projects"
      composerHeight={110}
      right={
        <IconButton
          aria-label="Search"
          variant="ghost"
          size="sm"
          onClick={() => {
            setSearchOpen((v) => !v);
            setSearchQuery("");
          }}
        >
          {searchOpen ? <LuX /> : <LuSearch />}
        </IconButton>
      }
    >
      {/* Search bar */}
      {searchOpen && (
        <Box mb={4}>
          <Input
            autoFocus
            placeholder="Search projects…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            size="sm"
            rounded="lg"
          />
        </Box>
      )}

      {isLoading && (
        <Center py={10}>
          <Spinner />
        </Center>
      )}

      {projects && projects.length === 0 && <EmptyProjectsHint />}

      {/* Masonry 2-column grid using CSS columns */}
      {displayed.length > 0 && (
        <Box
          css={{
            columnCount: { base: 1, sm: 2, xl: 3 },
            columnGap: "12px",
          }}
        >
          {displayed.map((p) => (
            <Box
              key={p.id}
              css={{ breakInside: "avoid" }}
              mb={3}
              display="inline-block"
              w="100%"
            >
              <SwipeableRow
                disabled={p.is_default}
                confirmMessage={`Delete project "${p.name}"? Tasks and jobs are removed too.`}
                onDelete={() => handleDelete(p)}
              >
                <ProjectCard
                  project={p}
                  stats={stats[p.id] ?? emptyStats()}
                  onClick={() => navigate(`/projects/${p.id}`)}
                />
              </SwipeableRow>
            </Box>
          ))}
        </Box>
      )}

      <StickyComposer>
        <NewProjectComposer />
      </StickyComposer>
    </Shell>
  );
}

// ─── ProjectCard ─────────────────────────────────────────────────────────────

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

function ProjectCard({
  project,
  stats,
  onClick,
}: {
  project: ProjectOut;
  stats: ProjectStats;
  onClick: () => void;
}) {
  const hasCover = Boolean(project.cover_url);
  const description = project.instructions?.trim() || project.path;

  return (
    <Flex
      direction="column"
      bg="bg"
      rounded="2xl"
      overflow="hidden"
      borderWidth="1px"
      borderColor="border"
      cursor="pointer"
      onClick={onClick}
      _hover={{ shadow: "md", transform: "translateY(-1px)" }}
      transition="box-shadow 0.15s, transform 0.15s"
    >
      {/* Text block */}
      <Box px={4} pt={3} pb={hasCover ? 2 : 3}>
        {/* Timestamp row */}
        <HStack gap={1.5} mb={1.5} color="fg.muted">
          {project.is_default ? (
            <Box lineHeight="0" fontSize="xs">
              <LuPin />
            </Box>
          ) : (
            <Box lineHeight="0" fontSize="xs">
              <LuClock />
            </Box>
          )}
          <Text fontSize="xs" color="fg.muted">
            {project.is_default
              ? "default"
              : relativeCardDate(project.created_at)}
          </Text>
        </HStack>

        {/* Title */}
        <Text fontWeight="bold" fontSize="md" lineHeight="short" mb={1}>
          {project.name}
        </Text>

        {/* Description — only when no cover; keep card compact with cover */}
        {!hasCover && description && (
          <Text
            fontSize="sm"
            color="fg.muted"
            lineHeight="short"
            css={{
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {description}
          </Text>
        )}

        {/* Stats row */}
        {(stats.jobsRunning > 0 || stats.tasksTotal > 0 || stats.jobs > 0) && (
          <HStack gap={3} mt={2} fontSize="xs" color="fg.subtle">
            {stats.jobsRunning > 0 && (
              <HStack gap={1}>
                <Box boxSize="1.5" rounded="full" bg="blue.solid" />
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
          </HStack>
        )}
      </Box>

      {/* Cover image */}
      {hasCover && (
        <Image
          src={project.cover_url!}
          w="full"
          h="40"
          objectFit="cover"
          flexShrink={0}
        />
      )}
    </Flex>
  );
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

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

function EmptyProjectsHint() {
  return (
    <Center py={12}>
      <Stack gap={2} align="center" maxW="md" textAlign="center">
        <Heading size="sm" color="fg.muted">
          No projects
        </Heading>
        <Text fontSize="sm" color="fg.subtle">
          Tap the pencil button below to add your first project.
        </Text>
      </Stack>
    </Center>
  );
}
