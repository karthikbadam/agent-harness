import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Center,
  Flex,
  Heading,
  HStack,
  Spinner,
  Stack,
  Text,
  Textarea,
} from "@chakra-ui/react";
import { LuFolderGit2 } from "react-icons/lu";

import { Shell } from "../components/Shell";
import { NewTaskComposer } from "../components/NewTaskComposer";
import { TaskCard } from "../components/TaskCard";
import { useProjects } from "../hooks/useProjects";
import { usePlan, useTasks } from "../hooks/useTasks";
import type { ProjectOut, TaskOut } from "../types";

export function ProjectsPage() {
  const [params, setParams] = useSearchParams();
  const { data: projects, isLoading } = useProjects();
  const selectedId = params.get("project") ?? projects?.[0]?.id ?? "";
  const selected = projects?.find((p) => p.id === selectedId) ?? projects?.[0];

  const setProject = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("project", id);
    setParams(next, { replace: true });
  };

  return (
    <Shell title="Projects">
      {isLoading && (
        <Center py={10}>
          <Spinner />
        </Center>
      )}
      {projects && projects.length === 0 && (
        <EmptyProjectsHint />
      )}
      {projects && projects.length > 0 && (
        <Flex
          direction={{ base: "column", md: "row" }}
          gap={{ base: 4, md: 6 }}
          align="flex-start"
        >
          {/* Mobile: horizontal scroll chips */}
          <HStack
            display={{ base: "flex", md: "none" }}
            gap={2}
            overflowX="auto"
            w="full"
            pb={1}
            css={{ scrollbarWidth: "none", "&::-webkit-scrollbar": { display: "none" } }}
          >
            {projects.map((p) => (
              <Button
                key={p.id}
                size="xs"
                variant={p.id === selected?.id ? "solid" : "subtle"}
                colorPalette={p.id === selected?.id ? "blue" : "gray"}
                onClick={() => setProject(p.id)}
                flexShrink={0}
              >
                {p.name}
              </Button>
            ))}
          </HStack>
          {/* Desktop: vertical list */}
          <Stack
            display={{ base: "none", md: "flex" }}
            gap={1}
            w="56"
            flexShrink={0}
            position="sticky"
            top="20"
          >
            <Text
              fontSize="2xs"
              fontWeight="medium"
              textTransform="uppercase"
              letterSpacing="wider"
              color="fg.muted"
              px={2}
              mb={1}
            >
              Projects
            </Text>
            {projects.map((p) => {
              const current = p.id === selected?.id;
              return (
                <Flex
                  key={p.id}
                  align="center"
                  gap={2}
                  px={3}
                  py={2}
                  rounded="md"
                  cursor="pointer"
                  bg={current ? "bg.subtle" : "transparent"}
                  color={current ? "fg" : "fg.muted"}
                  fontWeight={current ? "medium" : "normal"}
                  onClick={() => setProject(p.id)}
                  _hover={{ bg: "bg.subtle", color: "fg" }}
                  transition="background-color 0.15s"
                >
                  <Box lineHeight="0" fontSize="sm">
                    <LuFolderGit2 />
                  </Box>
                  <Text fontSize="sm" truncate>
                    {p.name}
                  </Text>
                </Flex>
              );
            })}
          </Stack>
          <Box flex="1" w="full" minW={0}>
            {selected && <ProjectPane project={selected} />}
          </Box>
        </Flex>
      )}
    </Shell>
  );
}

function ProjectPane({ project }: { project: ProjectOut }) {
  const { data: tasks, isLoading } = useTasks(project.id);
  const groups = useMemo(() => groupByPhase(tasks ?? []), [tasks]);

  return (
    <Stack gap={5}>
      <Box>
        <Heading size="lg" fontWeight="semibold" lineHeight="short">
          {project.name}
        </Heading>
        <Text fontSize="xs" color="fg.muted" fontFamily="mono" mt={1}>
          {project.path}
        </Text>
      </Box>
      <PlannerComposer projectId={project.id} />
      <NewTaskComposer projectId={project.id} />
      {isLoading && (
        <Center py={4}>
          <Spinner size="sm" />
        </Center>
      )}
      {tasks && tasks.length === 0 && (
        <Box bg="bg.subtle" rounded="lg" px={4} py={6} textAlign="center">
          <Text fontSize="sm" color="fg.muted">
            No tasks yet — create one above or describe an ask for the planner.
          </Text>
        </Box>
      )}
      <Stack gap={6}>
        {groups.map((g) => (
          <Stack key={g.label} gap={2.5}>
            <Flex align="baseline" gap={2}>
              <Heading
                size="xs"
                color="fg.muted"
                textTransform="uppercase"
                letterSpacing="wider"
                fontWeight="medium"
              >
                {g.label}
              </Heading>
              <Text fontSize="2xs" color="fg.subtle">
                {g.tasks.length}
              </Text>
            </Flex>
            <Stack gap={2}>
              {g.tasks.map((t) => (
                <TaskCard key={t.id} task={t} />
              ))}
            </Stack>
          </Stack>
        ))}
      </Stack>
    </Stack>
  );
}

function PlannerComposer({ projectId }: { projectId: string }) {
  const plan = usePlan(projectId);
  const [ask, setAsk] = useState("");
  return (
    <Box bg="bg.subtle" rounded="lg" p={4}>
      <Stack gap={2}>
        <Text fontSize="xs" fontWeight="medium" color="fg.muted" letterSpacing="wide">
          PLANNER
        </Text>
        <Textarea
          placeholder="Describe an ask. The planner will decompose it into draft tasks you can edit and confirm."
          value={ask}
          onChange={(e) => setAsk(e.target.value)}
          size="sm"
          rows={2}
          bg="bg"
        />
        <Flex justify="flex-end">
          <Button
            size="xs"
            colorPalette="blue"
            onClick={async () => {
              if (!ask.trim()) return;
              await plan.mutateAsync(ask.trim());
              setAsk("");
            }}
            loading={plan.isPending}
            disabled={!ask.trim()}
          >
            Plan
          </Button>
        </Flex>
      </Stack>
    </Box>
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

interface PhaseGroup {
  label: string;
  tasks: TaskOut[];
}

function groupByPhase(tasks: TaskOut[]): PhaseGroup[] {
  const order: { label: string; match: (t: TaskOut) => boolean }[] = [
    { label: "Awaiting ack", match: (t) => t.phase === "awaiting_ack" },
    { label: "Running", match: (t) => t.status === "running" },
    { label: "Ready", match: (t) => t.status === "ready" },
    { label: "Blocked", match: (t) => t.status === "pending" },
    { label: "Failed", match: (t) => t.status === "failed" },
    { label: "Done", match: (t) => t.status === "done" },
    { label: "Canceled", match: (t) => t.status === "canceled" },
  ];
  const seen = new Set<string>();
  const groups: PhaseGroup[] = [];
  for (const g of order) {
    const ts = tasks.filter((t) => !seen.has(t.id) && g.match(t));
    ts.forEach((t) => seen.add(t.id));
    if (ts.length > 0) groups.push({ label: g.label, tasks: ts });
  }
  return groups;
}
