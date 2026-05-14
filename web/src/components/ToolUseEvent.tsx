import { useState } from "react";
import { Badge, Box, Button, Code, Stack, Text } from "@chakra-ui/react";

import type { ToolUseEvent as TU } from "../types";

export function ToolUseEventCard({ event }: { event: TU }) {
  const [open, setOpen] = useState(false);
  const input = event.input ?? {};
  const inputStr = JSON.stringify(input, null, 2);
  const preview = previewFor(event.tool, input);
  return (
    <Box borderWidth="1px" borderRadius="md" p={3} bg="bg.subtle">
      <Stack gap={1}>
        <Box>
          <Badge colorPalette="purple" variant="subtle" mr={2}>
            {event.tool}
          </Badge>
          {preview && (
            <Text as="span" fontSize="sm" color="fg.muted" wordBreak="break-all">
              {preview}
            </Text>
          )}
        </Box>
        {inputStr.length > 0 && inputStr !== "{}" && (
          <>
            <Button
              variant="ghost"
              size="xs"
              alignSelf="flex-start"
              onClick={() => setOpen((o) => !o)}
            >
              {open ? "Hide input" : "Show input"}
            </Button>
            {open && (
              <Code
                as="pre"
                display="block"
                whiteSpace="pre-wrap"
                fontSize="xs"
                p={2}
                borderRadius="sm"
              >
                {inputStr}
              </Code>
            )}
          </>
        )}
      </Stack>
    </Box>
  );
}

function previewFor(tool: string, input: Record<string, unknown>): string {
  if (tool === "Bash" && typeof input.command === "string") return input.command;
  if (typeof input.file_path === "string") return String(input.file_path);
  return "";
}
