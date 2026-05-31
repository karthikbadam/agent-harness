import { Box, Button, Flex, HStack, Stack, Text } from "@chakra-ui/react";
import { useNavigate } from "react-router-dom";
import { LuGitBranch, LuRepeat } from "react-icons/lu";

import type { TaskOut } from "../types";
import {
  useAckTask,
  useCancelTask,
  useConfirmPlan,
  useRetryTask,
  useRunTask,
} from "../hooks/useTasks";

interface Props {
  task: TaskOut;
}

interface StatusBadge {
  label: string;
  color: "blue" | "green" | "red" | "orange" | "gray" | "teal";
  pulse?: boolean;
}

function badgeForTask(t: TaskOut): StatusBadge {
  // A loop reads as a loop, not a generic "Executing…".
  if (t.mode === "loop") {
    if (t.status === "running") return { label: "Looping", color: "blue", pulse: true };
    if (t.status === "done") return { label: "Loop finished", color: "green" };
    if (t.status === "failed") return { label: "Loop failed", color: "red" };
    if (t.status === "canceled") return { label: "Loop stopped", color: "orange" };
    if (t.status === "ready") return { label: "Loop ready", color: "teal" };
  }
  if (t.status === "failed" || t.phase === "failed")
    return { label: "Failed", color: "red" };
  if (t.status === "canceled") return { label: "Canceled", color: "orange" };
  // A gated planner draft awaiting your review + confirm.
  if (t.mode === "plan" && t.phase === "awaiting_ack")
    return { label: "Plan · review", color: "orange", pulse: true };
  if (t.phase === "awaiting_ack") return { label: "Awaiting ack", color: "orange" };
  if (t.phase === "planning") {
    // mode='plan' is the top-level planner decomposing the ask; mode='plan_then_execute'
    // is a per-task planning turn before edits.
    const label = t.mode === "plan" ? "Decomposing…" : "Planning…";
    return { label, color: "blue", pulse: true };
  }
  if (t.phase === "executing") {
    const label = t.mode === "research" ? "Researching…" : "Executing…";
    return { label, color: "blue", pulse: true };
  }
  if (t.phase === "integrating") return { label: "Integrating…", color: "blue", pulse: true };
  if (t.status === "running") {
    const label = t.mode === "research" ? "Researching…" : "Running…";
    return { label, color: "blue", pulse: true };
  }
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

export function TaskCard({ task }: Props) {
  const navigate = useNavigate();
  const run = useRunTask(task.project_id);
  const ack = useAckTask(task.project_id);
  const retry = useRetryTask(task.project_id);
  const cancel = useCancelTask(task.project_id);
  const confirmPlan = useConfirmPlan(task.project_id);

  const isLoop = task.mode === "loop";
  const isPlanReview = task.mode === "plan" && task.phase === "awaiting_ack";
  const canRun = task.status === "ready";
  // plan_then_execute parks at awaiting_ack and is acked into execution; a
  // gated planner draft (mode='plan') is confirmed, not acked.
  const canAck = task.phase === "awaiting_ack" && !isPlanReview;
  const canRetry = task.status === "failed";
  const canStopLoop = isLoop && task.status === "running";

  const badge = badgeForTask(task);

  const stop = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <Box
      bg="bg"
      rounded="lg"
      px={4}
      py={3.5}
      cursor="pointer"
      onClick={() =>
        navigate(
          isLoop || isPlanReview
            ? `/tasks/${task.id}`
            : `/jobs?task_id=${task.id}`,
        )
      }
      _hover={{ bg: "bg.muted" }}
      transition="background-color 0.15s"
    >
      <Flex justify="space-between" align="flex-start" gap={3}>
        <Stack gap={1.5} flex="1" minW={0}>
          <Text fontWeight="medium" lineHeight="short" truncate>
            {task.title}
          </Text>
          <HStack gap={2} align="center" fontSize="xs" wrap="wrap">
            <HStack gap={1.5}>
              <Box
                boxSize="1.5"
                rounded="full"
                bg={DOT_BG[badge.color]}
                animation={badge.pulse ? "pulse 1.4s ease-in-out infinite" : undefined}
              />
              <Text color={DOT_FG[badge.color]} fontWeight="medium">
                {badge.label}
              </Text>
            </HStack>
            {isLoop ? (
              <HStack gap={1.5} color="fg.muted">
                <Text>·</Text>
                <Box lineHeight="0">
                  <LuRepeat />
                </Box>
                <Text fontFamily="mono" fontSize="2xs">
                  {loopMeta(task)}
                </Text>
              </HStack>
            ) : task.worktree_branch ? (
              <HStack gap={1.5} color="fg.muted">
                <Text>·</Text>
                <Box lineHeight="0">
                  <LuGitBranch />
                </Box>
                <Text truncate fontFamily="mono" fontSize="2xs">
                  {task.worktree_branch}
                </Text>
              </HStack>
            ) : (
              <HStack gap={1.5} color="fg.subtle">
                <Text>·</Text>
                <Text fontFamily="mono" fontSize="2xs">
                  {task.id}
                </Text>
              </HStack>
            )}
          </HStack>
        </Stack>
        <HStack gap={1.5} flexShrink={0} onClick={stop}>
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
          {isPlanReview && (
            <Button
              size="2xs"
              colorPalette="green"
              onClick={() => confirmPlan.mutate(task.id)}
              loading={confirmPlan.isPending}
            >
              Confirm & run
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
          {canStopLoop && (
            <Button
              size="2xs"
              variant="outline"
              colorPalette="red"
              onClick={() => cancel.mutate(task.id)}
              loading={cancel.isPending}
            >
              Stop loop
            </Button>
          )}
        </HStack>
      </Flex>
    </Box>
  );
}

/** Compact loop summary for the card meta row, e.g. "11/40 · best 0.958". */
function loopMeta(t: TaskOut): string {
  const ls = (t.loop_state ?? {}) as {
    iteration?: number;
    best_metric?: number | null;
  };
  const spec = (t.loop_spec ?? {}) as { max_iterations?: number };
  const iter = ls.iteration ?? 0;
  const count = spec.max_iterations ? `${iter}/${spec.max_iterations}` : `${iter}`;
  const best =
    typeof ls.best_metric === "number" ? ` · best ${ls.best_metric.toFixed(3)}` : "";
  return `${count}${best}`;
}
