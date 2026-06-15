import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Box,
  Button,
  Center,
  Drawer,
  Flex,
  Heading,
  HStack,
  Portal,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { LuFileText } from "react-icons/lu";

import { Shell } from "../components/Shell";
import { AutopilotToggle } from "../components/AutopilotToggle";
import { Composer } from "../components/Composer";
import { MarkdownText } from "../components/MarkdownText";
import {
  ProviderPicker,
  type ProviderValue,
} from "../components/ProviderPicker";
import { StickyComposer } from "../components/StickyComposer";
import { TaskCard } from "../components/TaskCard";
import { parseServerDate, relativeTime } from "../api/dates";
import { useProjects } from "../hooks/useProjects";
import { useLastPlan, usePlan, useTasks } from "../hooks/useTasks";
import type { TaskOut } from "../types";

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const { data: projects, isLoading: projectsLoading } = useProjects();
  const project = projects?.find((p) => p.id === projectId);
  const { data: tasks, isLoading: tasksLoading } = useTasks(projectId);
  const plan = usePlan(projectId);
  const { data: lastPlan } = useLastPlan(projectId);
  const [planOpen, setPlanOpen] = useState(false);
  // Provider for prompts on this project. Defaults to the project's configured
  // provider; a per-prompt override takes precedence until the user changes it.
  const [providerOverride, setProviderOverride] =
    useState<ProviderValue | null>(null);
  const provider: ProviderValue =
    providerOverride ?? (project?.agent_provider as ProviderValue) ?? "claude";
  const groups = useMemo(() => groupByPhase(tasks ?? []), [tasks]);

  // Planning shows up as a regular task with mode='plan' in the list (the
  // user clicks into it to see the live planner stream). We only disable the
  // composer while the POST is in flight — the spawned plan task takes over
  // any visible "planning…" indication via its TaskCard.
  return (
    <Shell
      title={project?.name ?? "Project"}
      subtitle={project?.path}
      back="/"
      composerHeight={project ? 110 : 0}
      right={
        project ? (
          <>
            {lastPlan && (
              <Button
                size="xs"
                variant="outline"
                onClick={() => setPlanOpen(true)}
                gap={1.5}
                aria-label="View plan"
                px={{ base: 2, md: 3 }}
              >
                <LuFileText />
                <Text hideBelow="md">View plan</Text>
              </Button>
            )}
            <AutopilotToggle projectId={project.id} />
          </>
        ) : undefined
      }
    >
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
          {tasks && tasks.length === 0 && <EmptyTasksHint />}
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
      )}
      {project && (
        <StickyComposer>
          <Flex px={4} pt={2} justify="flex-end">
            <ProviderPicker value={provider} onChange={setProviderOverride} />
          </Flex>
          <Composer
            placeholder="Plan a new task — describe what should happen"
            disabled={plan.isPending}
            projectId={projectId}
            onSend={async (prompt, attachmentIds) => {
              await plan.mutateAsync({
                ask: prompt,
                attachmentIds,
                agentProvider: provider,
              });
            }}
          />
        </StickyComposer>
      )}
      <PlanDrawer open={planOpen} onClose={() => setPlanOpen(false)} />
    </Shell>
  );
}

function PlanDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { projectId = "" } = useParams();
  const { data: plan } = useLastPlan(open ? projectId : undefined);
  return (
    <Drawer.Root
      open={open}
      onOpenChange={(e) => (e.open ? null : onClose())}
      placement={{ base: "bottom", md: "end" }}
      size={{ base: "full", md: "lg" }}
    >
      <Portal>
        <Drawer.Backdrop />
        <Drawer.Positioner>
          <Drawer.Content pb="env(safe-area-inset-bottom)">
            <Drawer.Header>
              <Stack gap={1}>
                <Drawer.Title>Project plan</Drawer.Title>
                {plan &&
                  (() => {
                    const n = plan.task_ids?.length ?? 0;
                    return (
                      <Text fontSize="xs" color="fg.muted">
                        Planned {relativeTime(parseServerDate(plan.created_at))}{" "}
                        · {n} task{n === 1 ? "" : "s"}
                      </Text>
                    );
                  })()}
              </Stack>
            </Drawer.Header>
            <Drawer.Body>
              {!plan ? (
                <Center py={10}>
                  <Spinner size="sm" />
                </Center>
              ) : (
                <Stack gap={5}>
                  <Box>
                    <Text
                      fontSize="2xs"
                      color="fg.muted"
                      textTransform="uppercase"
                      letterSpacing="wider"
                      mb={1.5}
                    >
                      Ask
                    </Text>
                    <Box bg="bg" rounded="md" px={3.5} py={3}>
                      <Text
                        fontSize="sm"
                        lineHeight="1.55"
                        whiteSpace="pre-wrap"
                      >
                        {plan.ask}
                      </Text>
                    </Box>
                  </Box>
                  <Box>
                    <Text
                      fontSize="2xs"
                      color="fg.muted"
                      textTransform="uppercase"
                      letterSpacing="wider"
                      mb={1.5}
                    >
                      Planner findings & task list
                    </Text>
                    <Box bg="bg" rounded="md" px={3.5} py={3} fontSize="sm">
                      <MarkdownText source={plan.raw || "(empty)"} />
                    </Box>
                  </Box>
                </Stack>
              )}
            </Drawer.Body>
            <Drawer.Footer borderTopWidth="1px" borderColor="border.subtle">
              <HStack justify="flex-end" w="full">
                <Button variant="outline" size="sm" onClick={onClose}>
                  Close
                </Button>
              </HStack>
            </Drawer.Footer>
          </Drawer.Content>
        </Drawer.Positioner>
      </Portal>
    </Drawer.Root>
  );
}

function EmptyTasksHint() {
  return (
    <Box bg="bg" rounded="lg" px={4} py={6} textAlign="center">
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
    const ts = tasks
      .filter((t) => !seen.has(t.id) && g.match(t))
      .sort(
        (a, b) =>
          parseServerDate(b.created_at).getTime() -
          parseServerDate(a.created_at).getTime(),
      );
    ts.forEach((t) => seen.add(t.id));
    if (ts.length > 0) groups.push({ label: g.label, tasks: ts });
  }
  return groups;
}
