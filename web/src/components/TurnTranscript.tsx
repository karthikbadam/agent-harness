import { useEffect, useRef, useState } from "react";
import { Badge, Box, Button, Code, Flex, HStack, Stack, Text } from "@chakra-ui/react";

import type { JobOut, StreamEvent, ToolResultEvent, TurnDoneEvent, TurnOut } from "../types";
import { MarkdownText } from "./MarkdownText";
import { StatusPill } from "./StatusPill";
import { ToolUseEventCard } from "./ToolUseEvent";

interface Props {
  events: StreamEvent[];
  job?: JobOut;
}

export function TurnTranscript({ events, job }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length, job?.turns?.length]);

  const turns = job?.turns ?? [];
  const eventsByTurn = new Map<number, StreamEvent[]>();
  for (const e of events) {
    const arr = eventsByTurn.get(e.turn) ?? [];
    arr.push(e);
    eventsByTurn.set(e.turn, arr);
  }

  return (
    <Stack gap={5}>
      {turns.map((t) => (
        <Stack key={t.idx} gap={3}>
          <UserPromptCard turn={t} />
          {(eventsByTurn.get(t.idx) ?? []).map((ev) => (
            <EventCard key={ev.seq ?? `${ev.turn}-${ev.ts}-${ev.type}`} event={ev} />
          ))}
          {(eventsByTurn.get(t.idx) ?? []).length === 0 && t.status === "queued" && (
            <Text fontSize="xs" color="fg.muted">…queued, waiting for runner</Text>
          )}
        </Stack>
      ))}
      <div ref={bottomRef} />
    </Stack>
  );
}

function UserPromptCard({ turn }: { turn: TurnOut }) {
  return (
    <Box
      alignSelf="flex-end"
      maxW="85%"
      bg="blue.subtle"
      color="fg"
      borderRadius="lg"
      px={3}
      py={2}
    >
      <Text fontSize="xs" color="fg.muted" mb={1}>
        you · turn {turn.idx}
      </Text>
      <Text whiteSpace="pre-wrap" fontSize="sm">
        {turn.prompt}
      </Text>
    </Box>
  );
}

function EventCard({ event }: { event: StreamEvent }) {
  switch (event.type) {
    case "tool_use":
      return <ToolUseEventCard event={event} />;
    case "tool_result":
      return <ToolResultCard event={event} />;
    case "assistant_text":
      return (
        <Box>
          <MarkdownText source={event.text} />
        </Box>
      );
    case "turn_done":
      return <TurnDoneCard event={event} />;
    case "job_status":
      return (
        <HStack pt={1}>
          <Text fontSize="xs" color="fg.muted">
            status
          </Text>
          <StatusPill status={event.status} />
        </HStack>
      );
  }
}

function ToolResultCard({ event }: { event: ToolResultEvent }) {
  const text = event.output_preview || "(no output)";
  const long = text.length > 600;
  const [open, setOpen] = useState(!long);
  const display = open ? text : text.slice(0, 600) + "\n…";
  return (
    <Box
      borderWidth="1px"
      borderRadius="md"
      p={3}
      borderColor={event.ok ? "border" : "red.emphasized"}
    >
      <Flex justify="space-between" align="center" mb={2}>
        <Badge colorPalette={event.ok ? "green" : "red"} variant="subtle">
          {event.ok ? "ok" : "error"}
        </Badge>
        {long && (
          <Button size="xs" variant="ghost" onClick={() => setOpen((o) => !o)}>
            {open ? "Show less" : `Show more (${text.length} chars)`}
          </Button>
        )}
      </Flex>
      <Code
        as="pre"
        display="block"
        whiteSpace="pre-wrap"
        fontSize="xs"
        p={2}
        borderRadius="sm"
        overflowX="auto"
      >
        {display}
      </Code>
    </Box>
  );
}

function TurnDoneCard({ event }: { event: TurnDoneEvent }) {
  return (
    <HStack gap={2} pt={2} fontSize="xs" color="fg.muted" wrap="wrap">
      <Badge variant="subtle" colorPalette={event.exit_code === 0 ? "green" : "red"}>
        exit {event.exit_code}
      </Badge>
      {event.cost_usd != null && (
        <Badge variant="outline">${event.cost_usd.toFixed(4)}</Badge>
      )}
      {event.duration_ms != null && (
        <Badge variant="outline">{(event.duration_ms / 1000).toFixed(1)}s</Badge>
      )}
    </HStack>
  );
}
