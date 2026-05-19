import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { Box, Flex, IconButton, Text, Textarea } from "@chakra-ui/react";
import { LuArrowUp } from "react-icons/lu";

interface Props {
  placeholder?: string;
  disabled?: boolean;
  onSend: (prompt: string) => Promise<void> | void;
}

const MIN_HEIGHT = 44;
const MAX_HEIGHT = 240;
const HINT_AT = 200;

export function Composer({ placeholder = "Tell claude...", disabled, onSend }: Props) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.max(MIN_HEIGHT, Math.min(el.scrollHeight, MAX_HEIGHT));
    el.style.height = `${next}px`;
  }, [value]);

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

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter inserts a newline; only ⌘/Ctrl+↵ sends. (Shift gets auto-applied
    // by iOS keyboard after sentence-ending punctuation, so we exclude it.)
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void submit();
    }
  };

  const canSend = !!value.trim() && !disabled && !busy;
  const showHint = focused && value.length === 0;
  const showCount = value.length >= HINT_AT;

  return (
    <Box px={3} pt={3} pb="max(env(safe-area-inset-bottom), 10px)" bg="bg.subtle">
      <Flex
        align="flex-end"
        gap={2}
        bg="bg"
        borderRadius="lg"
        px={3.5}
        py={2.5}
        outline={focused ? "1px solid" : undefined}
        outlineColor={focused ? "blue.solid" : undefined}
        outlineOffset="0"
        transition="outline-color 0.15s ease"
      >
        <Textarea
          ref={ref}
          rows={1}
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          disabled={disabled || busy}
          resize="none"
          variant="outline"
          border="none"
          outline="none"
          bg="transparent"
          px={0}
          py={1}
          minH={`${MIN_HEIGHT}px`}
          maxH={`${MAX_HEIGHT}px`}
          fontSize="16px"
          lineHeight="1.4"
          _focus={{ boxShadow: "none", outline: "none" }}
          _focusVisible={{ boxShadow: "none", outline: "none" }}
        />
        <IconButton
          aria-label="Send"
          onClick={submit}
          loading={busy}
          disabled={!canSend}
          size="sm"
          rounded="full"
          colorPalette="blue"
          variant={canSend ? "solid" : "subtle"}
          alignSelf="flex-end"
          mb={1}
        >
          <LuArrowUp />
        </IconButton>
      </Flex>
      {(showHint || showCount) && (
        <Flex justify="space-between" px={2} pt={1} fontSize="xs" color="fg.muted">
          <Text>{showHint ? "↵ for newline · ⌘↵ to send" : ""}</Text>
          {showCount && <Text>{value.length} chars</Text>}
        </Flex>
      )}
    </Box>
  );
}
