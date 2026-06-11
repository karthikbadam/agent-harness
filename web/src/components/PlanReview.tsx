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
  const titleById = new Map((allTasks ?? []).map((t) => [t.id, t.title]));
  const depNames = (d: TaskOut) =>
    (d.depends_on ?? [])
      .map((id) => titleById.get(id) ?? id.slice(0, 6))
      .join(", ");
  const planJob = (jobs ?? [])
    .filter((j) => j.task_id === task.id)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
  const followup = useFollowup(planJob?.id ?? "");
  const replanning =
    planJob?.status === "running" || planJob?.status === "queued";
  const awaiting = task.phase === "awaiting_ack";

  return (
    <Stack gap={4}>
      <Box>
        <Text
          fontSize="2xs"
          color="fg.subtle"
          textTransform="uppercase"
          mb={1.5}
        >
          Proposed plan ({drafts.length})
        </Text>
        <Stack gap={1.5}>
          {drafts.map((d) => (
            <Stack
              key={d.id}
              gap={1}
              fontSize="sm"
              px={2.5}
              py={2}
              rounded="md"
              bg="bg.muted"
            >
              <Flex align="center" gap={2}>
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
                      ? "⤚ integrate"
                      : d.mode}
                </Text>
                <Text flex="1" minW={0} truncate>
                  {d.title}
                </Text>
              </Flex>
              {(d.depends_on ?? []).length > 0 ? (
                <Text fontSize="2xs" color="fg.subtle" pl="6rem" truncate>
                  ↳ after {depNames(d)}
                </Text>
              ) : (
                // A loop or integrate that builds on a foundation but has no
                // deps would start immediately, before the foundation exists —
                // the failure mode that's otherwise invisible at review time.
                (d.mode === "loop" || d.synthetic) &&
                drafts.length > 1 && (
                  <Text
                    fontSize="2xs"
                    color="orange.fg"
                    pl="6rem"
                    fontWeight="medium"
                  >
                    ⚠ no dependencies — starts immediately, before any
                    foundation is built
                  </Text>
                )
              )}
            </Stack>
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
