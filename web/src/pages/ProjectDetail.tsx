import { useMemo } from "react";
import { useParams } from "react-router-dom";
import {
  Box,
  Center,
  Flex,
  Heading,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { Composer } from "../components/Composer";
import { TaskCard } from "../components/TaskCard";
import { useProjects } from "../hooks/useProjects";
import { usePlan, useTasks } from "../hooks/useTasks";
import type { ProjectOut, TaskOut } from "../types";

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const { data: projects, isLoading: projectsLoading } = useProjects();
  const project = projects?.find((p) => p.id === projectId);
  const { data: tasks, isLoading: tasksLoading } = useTasks(projectId);
  const plan = usePlan(projectId);
  const groups = useMemo(() => groupByPhase(tasks ?? []), [tasks]);
  const planning = plan.isPending;

  return (
    <Shell title={project?.name ?? "Project"} back="/">
      <Box pb="calc(160px + env(safe-area-inset-bottom))">
        {(projectsLoading || tasksLoading) && (
          <Center py={8}>
            <Spinner />
          </Center>
        )}
        {!projectsLoading && !project && (
          <Center py={12}>
            <Text color="fg.muted">Project not found.</Text>
          </Center>
        )}
        {project && (
          <Stack gap={5} maxW="container.md">
            <Stack gap={1}>
              <Heading size="lg" fontWeight="semibold" lineHeight="short">
                {project.name}
              </Heading>
              <Text fontSize="xs" color="fg.subtle" fontFamily="mono">
                {project.path}
              </Text>
            </Stack>
            <Summary project={project} tasks={tasks ?? []} />
            {tasks && tasks.length === 0 && !planning && <EmptyTasksHint />}
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
            {planning && (
              <Box bg="bg.subtle" rounded="lg" px={4} py={3}>
                <Flex gap={3} align="center">
                  <Spinner size="sm" />
                  <Text fontSize="sm" color="fg.muted">
                    Planning… the planner is decomposing your ask into tasks.
                  </Text>
                </Flex>
              </Box>
            )}
          </Stack>
        )}
      </Box>
      {project && (
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
              placeholder="Plan a new task — describe what should happen"
              disabled={planning}
              onSend={async (prompt) => {
                await plan.mutateAsync(prompt);
              }}
            />
          </Box>
        </Box>
      )}
    </Shell>
  );
}

function Summary({ tasks }: { project: ProjectOut; tasks: TaskOut[] }) {
  const counts = useMemo(() => {
    let active = 0;
    let awaiting = 0;
    let done = 0;
    let failed = 0;
    for (const t of tasks) {
      if (t.status === "running") active += 1;
      if (t.phase === "awaiting_ack") awaiting += 1;
      if (t.status === "done") done += 1;
      if (t.status === "failed") failed += 1;
    }
    return { active, awaiting, done, failed };
  }, [tasks]);
  if (tasks.length === 0) return null;
  return (
    <Flex gap={4} flexWrap="wrap" fontSize="xs">
      <Stat label="Active" value={counts.active} accent="blue" />
      <Stat label="Awaiting ack" value={counts.awaiting} accent="blue" />
      <Stat label="Done" value={counts.done} accent="green" />
      {counts.failed > 0 && (
        <Stat label="Failed" value={counts.failed} accent="red" />
      )}
      <Stat label="Total" value={tasks.length} accent="gray" />
    </Flex>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent: "blue" | "green" | "red" | "gray" | "purple";
}) {
  const colorMap = {
    blue: { dot: "blue.solid", text: "fg" },
    green: { dot: "green.solid", text: "fg" },
    red: { dot: "red.solid", text: "fg" },
    gray: { dot: "border", text: "fg.muted" },
    purple: { dot: "purple.solid", text: "fg" },
  } as const;
  const c = colorMap[accent];
  return (
    <Flex align="center" gap={1.5}>
      <Box boxSize="1.5" rounded="full" bg={c.dot} />
      <Text color={c.text} fontWeight="medium">
        {value}
      </Text>
      <Text color="fg.muted">{label}</Text>
    </Flex>
  );
}

function EmptyTasksHint() {
  return (
    <Box bg="bg.subtle" rounded="lg" px={4} py={6} textAlign="center">
      <Text fontSize="sm" color="fg.muted">
        No tasks yet — describe an ask below and the planner will draft them.
      </Text>
    </Box>
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
