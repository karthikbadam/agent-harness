import { useEffect, useRef } from "react";
import { Badge, Box, Code, Stack, Text } from "@chakra-ui/react";

import type { JobOut, StreamEvent } from "../types";
import { ToolUseEventCard } from "./ToolUseEvent";

interface Props {
  events: StreamEvent[];
  job?: JobOut;
}

export function TurnTranscript({ events, job }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length]);

  return (
    <Stack gap={3}>
      {events.map((ev) => (
        <EventCard key={ev.seq ?? `${ev.turn}-${ev.ts}-${ev.type}`} event={ev} job={job} />
      ))}
      <div ref={bottomRef} />
    </Stack>
  );
}

function EventCard({ event }: { event: StreamEvent; job?: JobOut }) {
  switch (event.type) {
    case "tool_use":
      return <ToolUseEventCard event={event} />;
    case "tool_result":
      return (
        <Box borderWidth="1px" borderRadius="md" p={3} borderColor={event.ok ? "border" : "red.emphasized"}>
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
