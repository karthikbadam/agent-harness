import { Box, Button, Flex, HStack, Stack, Text } from "@chakra-ui/react";
import { useNavigate } from "react-router-dom";
import { LuGitBranch } from "react-icons/lu";

import type { TaskOut } from "../types";
import { useAckTask, useRetryTask, useRunTask } from "../hooks/useTasks";
import { PhaseTracker } from "./PhaseTracker";
import { StatusPill } from "./StatusPill";

interface Props {
  task: TaskOut;
}

export function TaskCard({ task }: Props) {
  const navigate = useNavigate();
  const run = useRunTask(task.project_id);
  const ack = useAckTask(task.project_id);
  const retry = useRetryTask(task.project_id);

  const canRun = task.status === "ready";
  const canAck = task.phase === "awaiting_ack";
  const canRetry = task.status === "failed";
  const showPhase = !task.synthetic && task.mode === "plan_then_execute" && (
    task.status === "running" ||
    task.status === "done" ||
    task.status === "failed"
  );

  return (
    <Box
      bg="bg.subtle"
      borderRadius="lg"
      px={4}
      py={3.5}
      _hover={{ bg: "bg.muted" }}
      transition="background-color 0.15s"
    >
      <Stack gap={3}>
        <Flex justify="space-between" align="flex-start" gap={3}>
          <Stack gap={1} flex="1" minW={0}>
            <Text fontWeight="medium" lineHeight="short" truncate>
              {task.title}
            </Text>
            <HStack gap={2} fontSize="2xs" color="fg.muted" wrap="wrap">
              <StatusPill status={task.status} />
              {task.synthetic && (
                <Text textTransform="uppercase" letterSpacing="wider">
                  · integrate
                </Text>
              )}
              {task.mode === "one_shot" && !task.synthetic && (
                <Text textTransform="uppercase" letterSpacing="wider">
                  · quick
                </Text>
              )}
              <Text>· {task.source}</Text>
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
        {showPhase && <PhaseTracker phase={task.phase} status={task.status} />}
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
