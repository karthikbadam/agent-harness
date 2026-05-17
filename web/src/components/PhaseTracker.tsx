import { Box, Flex, Text } from "@chakra-ui/react";

const PHASES = ["planning", "awaiting_ack", "executing", "done"] as const;
type Phase = (typeof PHASES)[number];

const PHASE_LABEL: Record<Phase, string> = {
  planning: "Plan",
  awaiting_ack: "Ack",
  executing: "Execute",
  done: "Done",
};

function rank(phase: string | null | undefined): number {
  if (phase === null || phase === undefined) return -1;
  const idx = (PHASES as readonly string[]).indexOf(phase);
  return idx;
}

interface Props {
  phase: string | null | undefined;
  status: string;
}

export function PhaseTracker({ phase, status }: Props) {
  const cur = rank(phase);
  const failed = status === "failed" || phase === "failed";
  return (
    <Flex gap={1} align="center">
      {PHASES.map((p, idx) => {
        const reached = idx <= cur && !failed;
        const active = idx === cur && !failed;
        const isFailedHere = failed && idx === Math.max(cur, 0);
        return (
          <Flex key={p} flex="1" align="center" gap={1}>
            <Box
              flex="1"
              h="2"
              borderRadius="full"
              bg={
                isFailedHere
                  ? "red.solid"
                  : reached
                    ? active
                      ? "blue.solid"
                      : "green.solid"
                    : "bg.subtle"
              }
              borderWidth="1px"
              borderColor={active ? "blue.emphasized" : "border"}
              transition="background-color 0.2s"
            />
          </Flex>
        );
      })}
      <Text fontSize="xs" color="fg.muted" minW="16">
        {failed ? "failed" : phase ? PHASE_LABEL[phase as Phase] ?? phase : "pending"}
      </Text>
    </Flex>
  );
}
