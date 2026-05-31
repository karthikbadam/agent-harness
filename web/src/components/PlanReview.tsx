import { Box, Button, Flex, Stack, Text } from "@chakra-ui/react";

import { Composer } from "./Composer";
import { useConfirmPlan, useTasks } from "../hooks/useTasks";
import { useFollowup, useJobs } from "../hooks/useJobs";
import type { TaskOut } from "../types";

/**
 * Review surface for a gated planner draft (mode='plan' parked at awaiting_ack):
 * the proposed task graph, a Confirm & run button, and a composer that sends a
 * steering followup to the plan's job — which re-plans and replaces the drafts.
 */
export function PlanReview({ task }: { task: TaskOut }) {
  const { data: allTasks } = useTasks(task.project_id);
  const { data: jobs } = useJobs();
  const confirmPlan = useConfirmPlan(task.project_id);

  const drafts = (allTasks ?? [])
    .filter((t) => t.parent_task_id === task.id)
    .sort((a, b) => a.order_idx - b.order_idx);
  const planJob = (jobs ?? [])
    .filter((j) => j.task_id === task.id)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
  const followup = useFollowup(planJob?.id ?? "");
  const replanning = planJob?.status === "running" || planJob?.status === "queued";
  const awaiting = task.phase === "awaiting_ack";

  return (
    <Stack gap={4}>
      <Box>
        <Text fontSize="2xs" color="fg.subtle" textTransform="uppercase" mb={1.5}>
          Proposed plan ({drafts.length})
        </Text>
        <Stack gap={1.5}>
          {drafts.map((d) => (
            <Flex
              key={d.id}
              align="center"
              gap={2}
              fontSize="sm"
              px={2.5}
              py={2}
              rounded="md"
              bg="bg.muted"
            >
              <Text
                fontFamily="mono"
                fontSize="2xs"
                color={d.mode === "loop" ? "blue.fg" : "fg.subtle"}
                w="5.5rem"
                flexShrink={0}
              >
                {d.mode === "loop"
                  ? "⟳ loop"
                  : d.synthetic
                    ? "integrate"
                    : d.mode}
              </Text>
              <Text flex="1" minW={0} truncate>
                {d.title}
              </Text>
              {(d.depends_on ?? []).length > 0 && (
                <Text fontSize="2xs" color="fg.subtle" flexShrink={0}>
                  ↳ {(d.depends_on ?? []).length} dep
                </Text>
              )}
            </Flex>
          ))}
          {drafts.length === 0 && (
            <Text fontSize="sm" color="fg.muted">
              No draft tasks.
            </Text>
          )}
        </Stack>
      </Box>

      {awaiting && (
        <>
          <Button
            colorPalette="green"
            onClick={() => confirmPlan.mutate(task.id)}
            loading={confirmPlan.isPending}
            disabled={replanning || drafts.length === 0}
          >
            Confirm &amp; run
          </Button>
          <Box>
            <Text
              fontSize="2xs"
              color="fg.subtle"
              textTransform="uppercase"
              mb={1.5}
            >
              Steer the plan
            </Text>
            <Composer
              placeholder={
                replanning
                  ? "Re-planning…"
                  : "Nudge it — 'make it one loop', 'add a test task'…"
              }
              disabled={replanning || !planJob}
              onSend={async (prompt) => {
                await followup.mutateAsync({ prompt });
              }}
            />
          </Box>
        </>
      )}
    </Stack>
  );
}
