import { useState } from "react";
import { Box, Button, Flex, Textarea } from "@chakra-ui/react";

interface Props {
  placeholder?: string;
  disabled?: boolean;
  onSend: (prompt: string) => Promise<void> | void;
}

export function Composer({ placeholder = "Tell claude...", disabled, onSend }: Props) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const text = value.trim();
    if (!text || busy || disabled) return;
    setBusy(true);
    try {
      await onSend(text);
      setValue("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box
      borderTopWidth="1px"
      px={3}
      py={2}
      bg="bg"
      position="sticky"
      bottom={0}
      pb="env(safe-area-inset-bottom)"
    >
      <Flex gap={2} align="flex-end">
        <Textarea
          rows={1}
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          disabled={disabled || busy}
          resize="none"
          minH="40px"
          maxH="160px"
        />
        <Button
          onClick={submit}
          loading={busy}
          disabled={!value.trim() || disabled}
          colorPalette="blue"
        >
          Send
        </Button>
      </Flex>
    </Box>
  );
}
