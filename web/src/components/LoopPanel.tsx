import { Box, Flex, HStack, Image, Link, Stack, Text } from "@chakra-ui/react";

import { tasksApi } from "../api/tasks";
import { useArtifacts, useIterations } from "../hooks/useTasks";
import type { TaskOut } from "../types";

interface Props {
  task: TaskOut; // a mode='loop' parent
}

interface LoopState {
  iteration?: number;
  best_metric?: number | null;
  best_commit?: string | null;
  consecutive_failures?: number;
  spent_usd?: number;
}
interface LoopSpec {
  metric_name?: string;
  direction?: string;
  max_iterations?: number;
  target_metric?: number | null;
}

function fmt(n: number | null | undefined, digits = 4): string {
  return typeof n === "number" ? n.toFixed(digits) : "—";
}

/**
 * The series view for an autoresearch loop: a compact state summary, the
 * latest progress graph (registered as a `graph` artifact on the parent), and
 * the per-iteration list (metric + keep/discard). Polls while the loop runs so
 * it refreshes on the phone as iterations land.
 */
export function LoopPanel({ task }: Props) {
  const live = task.status === "running";
  const { data: iterations } = useIterations(task.id, live);
  const { data: artifacts } = useArtifacts(task.id, live);

  const state = (task.loop_state ?? {}) as LoopState;
  const spec = (task.loop_spec ?? {}) as LoopSpec;
  const metricName = spec.metric_name ?? "metric";
  const graph = artifacts?.find((a) => a.kind === "graph");
  const downloads = artifacts?.filter((a) => a.kind !== "graph") ?? [];
  const rows = [...(iterations ?? [])].sort((a, b) => b.iteration - a.iteration);

  // Prevent taps on the panel from bubbling to the card's navigate handler.
  const stop = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <Box mt={3} pt={3} borderTopWidth="1px" borderColor="border" onClick={stop}>
      <HStack gap={4} fontSize="xs" wrap="wrap" mb={graph ? 3 : 2}>
        <Stat
          label={`best ${metricName}`}
          value={fmt(state.best_metric)}
          accent="green.fg"
        />
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
        {typeof state.spent_usd === "number" && state.spent_usd > 0 && (
          <Stat label="spend" value={`$${state.spent_usd.toFixed(2)}`} />
        )}
      </HStack>

      {graph && (
        <Image
          src={tasksApi.artifactUrl(graph.id)}
          alt="progress"
          w="100%"
          rounded="md"
          borderWidth="1px"
          borderColor="border"
          mb={3}
        />
      )}

      {rows.length > 0 && (
        <Stack gap={0.5} maxH="14rem" overflowY="auto">
          {rows.map((it) => (
            <Flex
              key={it.task_id}
              align="center"
              gap={2}
              fontSize="xs"
              px={2}
              py={1}
              rounded="sm"
              _hover={{ bg: "bg.muted" }}
            >
              <Text color="fg.subtle" fontFamily="mono" w="2.5rem" flexShrink={0}>
                #{it.iteration}
              </Text>
              <Text fontFamily="mono" w="4.5rem" flexShrink={0}>
                {fmt(it.metric)}
              </Text>
              <KeptBadge kept={it.kept} status={it.status} />
              <Text color="fg.muted" truncate flex="1" minW={0}>
                {it.description ?? ""}
              </Text>
            </Flex>
          ))}
        </Stack>
      )}

      {downloads.length > 0 && (
        <HStack gap={2} mt={2} wrap="wrap">
          {downloads.map((a) => (
            <Link
              key={a.id}
              href={tasksApi.artifactUrl(a.id)}
              fontSize="2xs"
              color="blue.fg"
              borderWidth="1px"
              borderColor="border"
              rounded="sm"
              px={2}
              py={0.5}
            >
              {a.name}
            </Link>
          ))}
        </HStack>
      )}
    </Box>
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

function KeptBadge({
  kept,
  status,
}: {
  kept: boolean | null | undefined;
  status: string;
}) {
  if (status === "running")
    return (
      <Text color="blue.fg" w="4rem" flexShrink={0}>
        running
      </Text>
    );
  if (kept === true)
    return (
      <Text color="green.fg" w="4rem" flexShrink={0}>
        ✓ kept
      </Text>
    );
  if (kept === false)
    return (
      <Text color="fg.subtle" w="4rem" flexShrink={0}>
        ✗ discard
      </Text>
    );
  return (
    <Text color="fg.subtle" w="4rem" flexShrink={0}>
      —
    </Text>
  );
}
