import { useState } from "react";
import {
  Badge,
  Box,
  Button,
  Code,
  Flex,
  HStack,
  Stack,
  Text,
} from "@chakra-ui/react";

import type { ToolUseEvent as TU } from "../types";

const COLLAPSE_AT = 240;

export function ToolUseEventCard({ event }: { event: TU }) {
  const input = (event.input ?? {}) as Record<string, unknown>;
  const layout = layoutFor(event.tool, input);

  return (
    <Box borderWidth="1px" borderRadius="md" p={3} bg="bg">
      <Stack gap={2}>
        <HStack gap={2} align="center" wrap="wrap">
          <Badge colorPalette="purple" variant="subtle">
            {event.tool}
          </Badge>
          {layout.header && (
            <Text
              fontSize="sm"
              color="fg"
              wordBreak="break-all"
              fontFamily={layout.headerMono ? "mono" : undefined}
            >
              {layout.header}
            </Text>
          )}
        </HStack>
        {layout.body && <CollapsibleCode text={layout.body} />}
      </Stack>
    </Box>
  );
}

interface Layout {
  header: string;
  headerMono?: boolean;
  body?: string;
}

function layoutFor(tool: string, input: Record<string, unknown>): Layout {
  if (tool === "Bash") {
    const cmd = str(input.command);
    const desc = str(input.description);
    const firstLine = cmd.split("\n")[0] ?? "";
    const isMulti = cmd.includes("\n") || cmd.length > 80;
    return {
      header: desc || (isMulti ? `${firstLine.slice(0, 70)}…` : firstLine),
      body: isMulti ? cmd : undefined,
      headerMono: !desc,
    };
  }

  if (tool === "Write") {
    const path = basename(str(input.file_path));
    return {
      header: path || "(no path)",
      headerMono: true,
      body: str(input.content),
    };
  }

  if (tool === "Edit" || tool === "MultiEdit") {
    const path = basename(str(input.file_path));
    const oldS = str(input.old_string);
    const newS = str(input.new_string);
    const body =
      oldS || newS
        ? `- ${oldS.split("\n").join("\n- ")}\n+ ${newS.split("\n").join("\n+ ")}`
        : undefined;
    return { header: path || "(no path)", headerMono: true, body };
  }

  if (tool === "Read") {
    const path = str(input.file_path);
    const offset = input.offset != null ? ` :${input.offset}` : "";
    const limit = input.limit != null ? ` (${input.limit} lines)` : "";
    return { header: `${basename(path)}${offset}${limit}`, headerMono: true };
  }

  if (tool === "Glob") {
    return { header: str(input.pattern), headerMono: true };
  }

  if (tool === "Grep") {
    const pattern = str(input.pattern);
    const path = str(input.path);
    return {
      header: path ? `${pattern}  in  ${basename(path)}` : pattern,
      headerMono: true,
    };
  }

  if (tool === "WebFetch") {
    return {
      header: str(input.url),
      headerMono: true,
      body: str(input.prompt) || undefined,
    };
  }

  // Generic fallback: render JSON.
  const json = JSON.stringify(input, null, 2);
  return { header: "", body: json === "{}" ? undefined : json };
}

function CollapsibleCode({ text }: { text: string }) {
  const long = text.length > COLLAPSE_AT;
  const [open, setOpen] = useState(!long);
  const display = open ? text : text.slice(0, COLLAPSE_AT) + "\n…";
  return (
    <Box>
      {long && (
        <Flex justify="flex-end" mb={1}>
          <Button size="xs" variant="ghost" onClick={() => setOpen((o) => !o)}>
            {open ? "Show less" : `Show more (${text.length} chars)`}
          </Button>
        </Flex>
      )}
      <Code
        as="pre"
        display="block"
        whiteSpace="pre"
        fontSize="xs"
        p={2}
        borderRadius="sm"
        overflowX="auto"
        maxW="100%"
      >
        {display}
      </Code>
    </Box>
  );
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function basename(p: string): string {
  if (!p) return "";
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(i + 1) : p;
}
