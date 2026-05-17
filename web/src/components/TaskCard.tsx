import { Box, Button, Flex, HStack, Stack, Text } from "@chakra-ui/react";
import { useNavigate } from "react-router-dom";

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

  return (
    <Box borderWidth="1px" borderRadius="md" px={4} py={3}>
      <Stack gap={3}>
        <Flex justify="space-between" align="flex-start" gap={2}>
          <Stack gap={1} flex="1" minW={0}>
            <Text fontWeight="medium" truncate>
              {task.title}
            </Text>
            <HStack gap={2} fontSize="xs" color="fg.muted">
              <StatusPill status={task.status} />
              {task.synthetic && <Text>· synthetic</Text>}
              <Text>· {task.source}</Text>
              <Text>· {task.mode}</Text>
            </HStack>
          </Stack>
          <HStack gap={1}>
            {canRun && (
              <Button
                size="xs"
                onClick={() => run.mutate(task.id)}
                loading={run.isPending}
              >
                Run
              </Button>
            )}
            {canAck && (
              <Button
                size="xs"
                colorPalette="blue"
                onClick={() => ack.mutate({ id: task.id })}
                loading={ack.isPending}
              >
                Ack plan
              </Button>
            )}
            {canRetry && (
              <Button
                size="xs"
                variant="outline"
                onClick={() => retry.mutate(task.id)}
                loading={retry.isPending}
              >
                Retry
              </Button>
            )}
          </HStack>
        </Flex>
        {!task.synthetic && task.mode === "plan_then_execute" && (
          <PhaseTracker phase={task.phase} status={task.status} />
        )}
        <Flex justify="space-between" align="center" gap={2}>
          <Text fontSize="xs" color="fg.muted" truncate>
            {task.worktree_branch ? `branch ${task.worktree_branch}` : task.id}
          </Text>
          <Button
            size="xs"
            variant="ghost"
            onClick={() => navigate(`/jobs?task_id=${task.id}`)}
          >
            Jobs
          </Button>
        </Flex>
      </Stack>
    </Box>
  );
}
