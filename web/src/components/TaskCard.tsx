import { useState } from "react";
import {
  Box,
  Button,
  Collapsible,
  Flex,
  HStack,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useNavigate } from "react-router-dom";
import { LuChevronDown, LuChevronUp, LuGitBranch } from "react-icons/lu";

import type { OutcomeOut, TaskOut } from "../types";
import {
  useAckTask,
  useRetryTask,
  useRunTask,
  useTaskOutcomes,
} from "../hooks/useTasks";
import { MarkdownText } from "./MarkdownText";

interface Props {
  task: TaskOut;
}

interface StatusBadge {
  label: string;
  color: "blue" | "green" | "red" | "orange" | "gray" | "teal";
  pulse?: boolean;
}

function badgeForTask(t: TaskOut): StatusBadge {
  if (t.status === "failed" || t.phase === "failed")
    return { label: "Failed", color: "red" };
  if (t.status === "canceled") return { label: "Canceled", color: "orange" };
  if (t.phase === "awaiting_ack") return { label: "Awaiting ack", color: "orange" };
  if (t.phase === "planning") return { label: "Planning…", color: "blue", pulse: true };
  if (t.phase === "executing") return { label: "Executing…", color: "blue", pulse: true };
  if (t.phase === "integrating") return { label: "Integrating…", color: "blue", pulse: true };
  if (t.status === "running") return { label: "Running…", color: "blue", pulse: true };
  if (t.status === "done") return { label: "Done", color: "green" };
  if (t.status === "ready") return { label: "Ready", color: "teal" };
  if (t.status === "pending") return { label: "Blocked on deps", color: "gray" };
  return { label: t.status, color: "gray" };
}

const DOT_BG: Record<StatusBadge["color"], string> = {
  blue: "blue.solid",
  green: "green.solid",
  red: "red.solid",
  orange: "orange.solid",
  teal: "teal.solid",
  gray: "border",
};
const DOT_FG: Record<StatusBadge["color"], string> = {
  blue: "blue.fg",
  green: "green.fg",
  red: "red.fg",
  orange: "orange.fg",
  teal: "teal.fg",
  gray: "fg.muted",
};

function latestOf(
  outcomes: OutcomeOut[] | undefined,
  kind: "plan" | "execute" | "integrate",
): OutcomeOut | null {
  if (!outcomes) return null;
  for (const o of outcomes) if (o.kind === kind) return o;
  return null;
}

export function TaskCard({ task }: Props) {
  const navigate = useNavigate();
  const run = useRunTask(task.project_id);
  const ack = useAckTask(task.project_id);
  const retry = useRetryTask(task.project_id);
  const [expanded, setExpanded] = useState(false);

  const canRun = task.status === "ready";
  const canAck = task.phase === "awaiting_ack";
  const canRetry = task.status === "failed";

  // Fetch outcomes when awaiting ack (always — plan is the actionable context)
  // or when the user opens the disclosure on a non-pending task.
  const wantOutcomes =
    canAck || (expanded && task.status !== "pending" && task.status !== "ready");
  const { data: outcomes } = useTaskOutcomes(wantOutcomes ? task.id : undefined);
  const planOutcome = latestOf(outcomes, "plan");
  const execOutcome = latestOf(outcomes, "execute");
  const hasHistory =
    task.status === "done" || task.status === "failed" || task.phase === "done";

  const badge = badgeForTask(task);

  return (
    <Box
      bg="bg.subtle"
      rounded="lg"
      px={4}
      py={3.5}
      _hover={{ bg: canAck ? "bg.subtle" : "bg.muted" }}
      transition="background-color 0.15s"
    >
      <Stack gap={2.5}>
        <Flex justify="space-between" align="flex-start" gap={3}>
          <Stack gap={1.5} flex="1" minW={0}>
            <Text fontWeight="medium" lineHeight="short" truncate>
              {task.title}
            </Text>
            <HStack gap={1.5} align="center">
              <Box
                boxSize="1.5"
                rounded="full"
                bg={DOT_BG[badge.color]}
                animation={badge.pulse ? "pulse 1.4s ease-in-out infinite" : undefined}
              />
              <Text
                fontSize="xs"
                color={DOT_FG[badge.color]}
                fontWeight="medium"
              >
                {badge.label}
              </Text>
              {task.source === "planner" && (
                <>
                  <Text fontSize="2xs" color="fg.subtle">·</Text>
                  <Text fontSize="2xs" color="fg.muted">planner</Text>
                </>
              )}
              {task.synthetic && (
                <>
                  <Text fontSize="2xs" color="fg.subtle">·</Text>
                  <Text fontSize="2xs" color="fg.muted">integration</Text>
                </>
              )}
            </HStack>
          </Stack>
          <HStack gap={1.5} flexShrink={0}>
            {canRun && (
              <Button
                size="2xs"
                colorPalette="blue"
                onClick={() => run.mutate(task.id)}
                loading={run.isPending}
              >
                Run
              </Button>
            )}
            {canAck && (
              <Button
                size="2xs"
                colorPalette="orange"
                onClick={() => ack.mutate({ id: task.id })}
                loading={ack.isPending}
              >
                Ack plan
              </Button>
            )}
            {canRetry && (
              <Button
                size="2xs"
                variant="outline"
                colorPalette="orange"
                onClick={() => retry.mutate(task.id)}
                loading={retry.isPending}
              >
                Retry
              </Button>
            )}
          </HStack>
        </Flex>

        {canAck && (
          <PlanCallout outcome={planOutcome} />
        )}

        {hasHistory && (
          <Box>
            <Button
              size="2xs"
              variant="ghost"
              color="fg.muted"
              onClick={() => setExpanded((x) => !x)}
              gap={1}
              px={1}
              h={6}
              fontWeight="normal"
            >
              {expanded ? <LuChevronUp /> : <LuChevronDown />}
              {expanded ? "Hide details" : "Show plan & summary"}
            </Button>
            <Collapsible.Root open={expanded}>
              <Collapsible.Content>
                <Stack gap={2} mt={2}>
                  {planOutcome?.summary && (
                    <OutcomeBlock label="Plan" summary={planOutcome.summary} />
                  )}
                  {execOutcome?.summary && (
                    <OutcomeBlock
                      label={execOutcome.status === "success" ? "Execute" : "Execute (failed)"}
                      summary={execOutcome.summary}
                      tone={execOutcome.status === "success" ? "neutral" : "danger"}
                    />
                  )}
                </Stack>
              </Collapsible.Content>
            </Collapsible.Root>
          </Box>
        )}

        <Flex justify="space-between" align="center" gap={2} fontSize="2xs" color="fg.muted">
          {task.worktree_branch ? (
            <HStack gap={1.5}>
              <Box lineHeight="0">
                <LuGitBranch />
              </Box>
              <Text truncate fontFamily="mono">
                {task.worktree_branch}
              </Text>
            </HStack>
          ) : (
            <Text fontFamily="mono" color="fg.subtle">
              {task.id}
            </Text>
          )}
          <Button
            size="2xs"
            variant="ghost"
            color="fg.muted"
            onClick={() => navigate(`/jobs?task_id=${task.id}`)}
          >
            Jobs ›
          </Button>
        </Flex>
      </Stack>
    </Box>
  );
}

function PlanCallout({ outcome }: { outcome: OutcomeOut | null }) {
  return (
    <Box
      bg="bg"
      borderLeftWidth="2px"
      borderColor="orange.solid"
      rounded="md"
      px={3.5}
      py={3}
    >
      <Stack gap={1.5}>
        <Text
          fontSize="2xs"
          color="orange.fg"
          fontWeight="medium"
          textTransform="uppercase"
          letterSpacing="wider"
        >
          Plan
        </Text>
        {!outcome ? (
          <HStack gap={2}>
            <Spinner size="xs" />
            <Text fontSize="xs" color="fg.muted">
              Loading plan…
            </Text>
          </HStack>
        ) : (
          <Box fontSize="sm" color="fg" maxH="60" overflowY="auto">
            <MarkdownText source={outcome.summary || "(no plan recorded)"} />
          </Box>
        )}
      </Stack>
    </Box>
  );
}

function OutcomeBlock({
  label,
  summary,
  tone = "neutral",
}: {
  label: string;
  summary: string;
  tone?: "neutral" | "danger";
}) {
  const borderColor = tone === "danger" ? "red.solid" : "border.subtle";
  const labelColor = tone === "danger" ? "red.fg" : "fg.muted";
  return (
    <Box
      bg="bg"
      borderLeftWidth="2px"
      borderColor={borderColor}
      rounded="md"
      px={3.5}
      py={3}
    >
      <Stack gap={1.5}>
        <Text
          fontSize="2xs"
          color={labelColor}
          fontWeight="medium"
          textTransform="uppercase"
          letterSpacing="wider"
        >
          {label}
        </Text>
        <Box fontSize="sm" color="fg" maxH="80" overflowY="auto">
          <MarkdownText source={summary} />
        </Box>
      </Stack>
    </Box>
  );
}
