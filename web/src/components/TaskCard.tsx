import { Box, Button, Flex, HStack, Stack, Text } from "@chakra-ui/react";
import { useNavigate } from "react-router-dom";
import { LuGitBranch } from "react-icons/lu";

import type { TaskOut } from "../types";
import { useAckTask, useRetryTask, useRunTask } from "../hooks/useTasks";

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

export function TaskCard({ task }: Props) {
  const navigate = useNavigate();
  const run = useRunTask(task.project_id);
  const ack = useAckTask(task.project_id);
  const retry = useRetryTask(task.project_id);

  const canRun = task.status === "ready";
  const canAck = task.phase === "awaiting_ack";
  const canRetry = task.status === "failed";

  const badge = badgeForTask(task);

  return (
    <Box
      bg="bg.subtle"
      rounded="lg"
      px={4}
      py={3.5}
      _hover={{ bg: "bg.muted" }}
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
                  <Text fontSize="2xs" color="fg.subtle">
                    ·
                  </Text>
                  <Text fontSize="2xs" color="fg.muted">
                    planner
                  </Text>
                </>
              )}
              {task.synthetic && (
                <>
                  <Text fontSize="2xs" color="fg.subtle">
                    ·
                  </Text>
                  <Text fontSize="2xs" color="fg.muted">
                    integration
                  </Text>
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
                colorPalette="blue"
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
