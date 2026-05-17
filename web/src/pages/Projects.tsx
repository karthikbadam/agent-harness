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
import { useProjects } from "../hooks/useProjects";
import { useJobs } from "../hooks/useJobs";
import type { JobOut, ProjectOut } from "../types";

export function ProjectsPage() {
  const navigate = useNavigate();
  const { data: projects, isLoading } = useProjects();
  const { data: jobs } = useJobs();
  const sorted = useMemo(() => sortProjects(projects ?? []), [projects]);
  const stats = useMemo(() => indexStats(jobs ?? []), [jobs]);

  return (
    <Shell title="Projects">
      {isLoading && (
        <Center py={10}>
          <Spinner />
        </Center>
      )}
      {projects && projects.length === 0 && <EmptyProjectsHint />}
      {sorted.length > 0 && (
        <Stack gap={6} maxW="container.md">
          <ProjectGroup
            label="Your projects"
            projects={sorted.user}
            stats={stats}
            onOpen={(p) => navigate(`/projects/${p.id}`)}
          />
          {sorted.system.length > 0 && (
            <ProjectGroup
              label="System"
              projects={sorted.system}
              stats={stats}
              onOpen={(p) => navigate(`/projects/${p.id}`)}
            />
          )}
        </Stack>
      )}
    </Shell>
  );
}

interface JobStats {
  total: number;
  running: number;
}

function indexStats(jobs: JobOut[]): Record<string, JobStats> {
  const m: Record<string, JobStats> = {};
  for (const j of jobs) {
    const s = m[j.project_id] ?? { total: 0, running: 0 };
    s.total += 1;
    if (j.status === "running" || j.status === "queued") s.running += 1;
    m[j.project_id] = s;
  }
  return m;
}

interface SortedProjects {
  user: ProjectOut[];
  system: ProjectOut[];
}

function sortProjects(projects: ProjectOut[]): SortedProjects & ProjectOut[] {
  // Default project to the bottom in a separate "System" group.
  const user = projects.filter((p) => !p.is_default);
  const system = projects.filter((p) => p.is_default);
  user.sort((a, b) => a.name.localeCompare(b.name));
  // Cheap shape: array-like for length checks, with .user/.system grouped views.
  const all = [...user, ...system] as SortedProjects & ProjectOut[];
  all.user = user;
  all.system = system;
  return all;
}

function ProjectGroup({
  label,
  projects,
  stats,
  onOpen,
}: {
  label: string;
  projects: ProjectOut[];
  stats: Record<string, JobStats>;
  onOpen: (p: ProjectOut) => void;
}) {
  if (projects.length === 0) return null;
  return (
    <Stack gap={2}>
      <Heading
        size="xs"
        color="fg.muted"
        textTransform="uppercase"
        letterSpacing="wider"
        fontWeight="medium"
      >
        {label}
      </Heading>
      <Stack gap={2}>
        {projects.map((p) => (
          <ProjectRow
            key={p.id}
            project={p}
            stats={stats[p.id] ?? { total: 0, running: 0 }}
            onClick={() => onOpen(p)}
          />
        ))}
      </Stack>
    </Stack>
  );
}

function ProjectRow({
  project,
  stats,
  onClick,
}: {
  project: ProjectOut;
  stats: JobStats;
  onClick: () => void;
}) {
  return (
    <Flex
      bg="bg.subtle"
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
        bg="bg"
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
        {stats.running > 0 && (
          <HStack gap={1.5}>
            <Box boxSize="2" rounded="full" bg="blue.solid" />
            <Text>{stats.running} running</Text>
          </HStack>
        )}
        {stats.total > 0 && stats.running === 0 && (
          <Text>{stats.total} jobs</Text>
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
          Create one with{" "}
          <Box as="code" px={1} bg="bg.subtle" rounded="sm" fontSize="xs">
            POST /api/projects
          </Box>{" "}
          — point it at a repo path on this machine.
        </Text>
      </Stack>
    </Center>
  );
}
