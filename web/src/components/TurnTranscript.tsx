import { useEffect, useRef } from "react";
import { Badge, Box, Code, Flex, Stack, Text } from "@chakra-ui/react";

import type { StreamEvent } from "../types";
import { ToolUseEventCard } from "./ToolUseEvent";

interface Props {
  events: StreamEvent[];
}

export function TurnTranscript({ events }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length]);

  return (
    <Stack gap={3}>
      {events.map((ev) => (
        <EventCard key={ev.seq ?? `${ev.turn}-${ev.ts}-${ev.type}`} event={ev} />
      ))}
      <div ref={bottomRef} />
    </Stack>
  );
}

function EventCard({ event }: { event: StreamEvent }) {
  switch (event.type) {
    case "tool_use":
      return <ToolUseEventCard event={event} />;
    case "tool_result":
      return (
        <Box borderWidth="1px" borderRadius="md" p={3} borderColor={event.ok ? "border" : "red.500"}>
          <Badge colorPalette={event.ok ? "green" : "red"} variant="subtle" mr={2}>
            {event.ok ? "ok" : "error"}
          </Badge>
          <Code
            as="pre"
            display="block"
            whiteSpace="pre-wrap"
            fontSize="xs"
            mt={2}
            p={2}
            borderRadius="sm"
          >
            {event.output_preview || "(no output)"}
          </Code>
        </Box>
      );
    case "assistant_text":
      return (
        <Box>
          <Text whiteSpace="pre-wrap">{event.text}</Text>
        </Box>
      );
    case "tool_blocked":
      return (
        <Box
          borderWidth="1px"
          borderRadius="md"
          p={3}
          borderColor="red.500"
          bg="red.subtle"
        >
          <Stack gap={2}>
            <Flex justify="space-between" align="center">
              <Text fontWeight="medium" color="red.fg">
                Blocked: {event.tool}
              </Text>
              {event.suggested_rule && (
                <Badge colorPalette="red" variant="subtle">
                  {event.suggested_rule}
                </Badge>
              )}
            </Flex>
            <Text fontSize="sm" color="fg.muted">
              {event.reason}
            </Text>
            <Text fontSize="xs" color="fg.muted">
              The retry button appears in step (j).
            </Text>
          </Stack>
        </Box>
      );
    case "turn_done":
      return (
        <Box pt={2} fontSize="xs" color="fg.muted">
          turn {event.turn} done · exit {event.exit_code}
          {event.cost_usd != null ? ` · $${event.cost_usd.toFixed(4)}` : ""}
          {event.duration_ms != null ? ` · ${event.duration_ms}ms` : ""}
        </Box>
      );
    case "job_status":
      return (
        <Box pt={1} fontSize="xs" color="fg.muted">
          status → {event.status}
        </Box>
      );
  }
}
