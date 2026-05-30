import { useNavigate, useParams } from "react-router-dom";
import { Box, Button, Center, Flex, HStack, Spinner, Stack, Text } from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { ArtifactView } from "../components/ArtifactView";
import { StatusPill } from "../components/StatusPill";
import {
  useArtifacts,
  useCancelTask,
  useIterations,
  useTask,
} from "../hooks/useTasks";

interface LoopState {
  iteration?: number;
  best_metric?: number | null;
  best_commit?: string | null;
  spent_usd?: number;
}
interface LoopSpec {
  metric_name?: string;
  max_iterations?: number;
  target_metric?: number | null;
}

function fmt(n: number | null | undefined, d = 4): string {
  return typeof n === "number" ? n.toFixed(d) : "—";
}

/**
 * Dig-in view for a task. Renders whatever the task produced — artifacts
 * dispatched by kind (graph/table/report/log/file) — so it's flexible across
 * task types. For a loop task it also shows the live stats and the iteration
 * series, each row drilling into that iteration's transcript.
 */
export function TaskDetailPage() {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const task = useTask(taskId);
  const isLoop = task.data?.mode === "loop";
  const live = task.data?.status === "running";
  const { data: artifacts } = useArtifacts(taskId, live);
  const { data: iterations } = useIterations(isLoop ? taskId : undefined, live);
  const cancel = useCancelTask(task.data?.project_id ?? "");

  const state = (task.data?.loop_state ?? {}) as LoopState;
  const spec = (task.data?.loop_spec ?? {}) as LoopSpec;
  const metricName = spec.metric_name ?? "metric";
  const rows = [...(iterations ?? [])].sort((a, b) => b.iteration - a.iteration);

  // Agents sometimes name an artifact per-iteration ("Progress graph (iter 4)"),
  // which creates a fresh row each time instead of updating one. Collapse a
  // series to its latest: strip a trailing "(iter N)"-style suffix and keep the
  // newest per (kind, normalized name). The API returns newest-first.
  const seenArtifacts = new Set<string>();
  const artifactList = (artifacts ?? []).filter((a) => {
    const base = a.name
      .replace(/\s*\((?:iter(?:ation)?|step|round|v(?:ersion)?)?\s*\d+\)\s*$/i, "")
      .trim()
      .toLowerCase();
    const key = `${a.kind}::${base || a.name.toLowerCase()}`;
    if (seenArtifacts.has(key)) return false;
    seenArtifacts.add(key);
    return true;
  });

  return (
    <Shell
      title={task.data?.title ?? "Task"}
      subtitle={
        task.data
          ? [isLoop ? "loop" : task.data.mode, task.data.status]
              .filter(Boolean)
              .join(" · ")
          : undefined
      }
      back={task.data ? `/projects/${task.data.project_id}` : "/"}
      right={
        isLoop && task.data?.status === "running" ? (
          <Button
            size="xs"
            variant="outline"
            colorPalette="red"
            onClick={() => cancel.mutate(task.data!.id)}
            loading={cancel.isPending}
          >
            Stop loop
          </Button>
        ) : undefined
      }
    >
      {task.isLoading && (
        <Center py={8}>
          <Spinner />
        </Center>
      )}

      {isLoop && (
        <HStack gap={5} fontSize="sm" wrap="wrap" mb={4}>
          <Stat label={`best ${metricName}`} value={fmt(state.best_metric)} accent="green.fg" />
          <Stat
            label="iterations"
            value={
              spec.max_iterations
                ? `${state.iteration ?? 0} / ${spec.max_iterations}`
                : String(state.iteration ?? 0)
            }
          />
          {typeof spec.target_metric === "number" && (
            <Stat label="target" value={fmt(spec.target_metric)} />
          )}
          {typeof state.spent_usd === "number" && (
            <Stat label="spend" value={`$${state.spent_usd.toFixed(2)}`} />
          )}
        </HStack>
      )}

      {/* Flexible: render whatever the task produced (latest of each series). */}
      <Stack gap={5}>
        {artifactList.map((a) => (
          <ArtifactView key={a.id} artifact={a} live={live} />
        ))}
      </Stack>

      {isLoop && rows.length > 0 && (
        <Box mt={5}>
          <Text fontSize="2xs" color="fg.subtle" textTransform="uppercase" mb={1.5}>
            Iterations ({rows.length})
          </Text>
          <Stack gap={0.5}>
            {rows.map((it) => (
              <Flex
                key={it.task_id}
                align="center"
                gap={3}
                fontSize="xs"
                px={2.5}
                py={2}
                rounded="md"
                cursor="pointer"
                _hover={{ bg: "bg.muted" }}
                onClick={() => navigate(`/jobs?task_id=${it.task_id}`)}
              >
                <Text color="fg.subtle" fontFamily="mono" w="3rem" flexShrink={0}>
                  #{it.iteration}
                </Text>
                <Text fontFamily="mono" w="4.5rem" flexShrink={0}>
                  {fmt(it.metric)}
                </Text>
                <Box w="4.5rem" flexShrink={0}>
                  {it.status === "running" ? (
                    <StatusPill status="running" />
                  ) : it.kept === true ? (
                    <Text color="green.fg">✓ kept</Text>
                  ) : it.kept === false ? (
                    <Text color="fg.subtle">✗ discard</Text>
                  ) : (
                    <Text color="fg.subtle">—</Text>
                  )}
                </Box>
                <Text color="fg.muted" truncate flex="1" minW={0}>
                  {it.description ?? ""}
                </Text>
              </Flex>
            ))}
          </Stack>
        </Box>
      )}

      {!task.isLoading &&
        artifactList.length === 0 &&
        !(isLoop && rows.length > 0) && (
          <Text color="fg.muted" fontSize="sm">
            No artifacts yet.
          </Text>
        )}
    </Shell>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <Stack gap={0}>
      <Text color="fg.subtle" textTransform="uppercase" fontSize="2xs">
        {label}
      </Text>
      <Text fontWeight="semibold" color={accent} fontFamily="mono">
        {value}
      </Text>
    </Stack>
  );
}
