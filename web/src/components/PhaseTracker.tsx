import { Flex, HStack, Text } from "@chakra-ui/react";

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
  return (PHASES as readonly string[]).indexOf(phase);
}

interface Props {
  phase: string | null | undefined;
  status: string;
}

export function PhaseTracker({ phase, status }: Props) {
  const cur = rank(phase);
  const failed = status === "failed" || phase === "failed";

  return (
    <HStack gap={1.5} align="center">
      {PHASES.map((p, idx) => {
        const reached = idx <= cur && !failed;
        const active = idx === cur && !failed;
        const isFailedHere = failed && idx === Math.max(cur, 0);

        let dotBg = "border.subtle";
        let dotBorder = "border";
        let textColor = "fg.muted";
        let textWeight: "normal" | "medium" = "normal";

        if (isFailedHere) {
          dotBg = "red.solid";
          dotBorder = "red.solid";
          textColor = "red.fg";
          textWeight = "medium";
        } else if (active) {
          dotBg = "blue.solid";
          dotBorder = "blue.solid";
          textColor = "fg";
          textWeight = "medium";
        } else if (reached) {
          dotBg = "green.solid";
          dotBorder = "green.solid";
          textColor = "fg.muted";
        }

        return (
          <Flex key={p} align="center" gap={1.5}>
            <Flex
              w="1.5"
              h="1.5"
              rounded="full"
              bg={dotBg}
              borderWidth="1px"
              borderColor={dotBorder}
            />
            <Text
              fontSize="2xs"
              color={textColor}
              fontWeight={textWeight}
              letterSpacing="wide"
              textTransform="uppercase"
            >
              {PHASE_LABEL[p]}
            </Text>
            {idx < PHASES.length - 1 && (
              <Text fontSize="2xs" color="border" mx={0.5}>
                ›
              </Text>
            )}
          </Flex>
        );
      })}
    </HStack>
  );
}
