import { HStack, SegmentGroup, Text } from "@chakra-ui/react";

export type ProviderValue = "claude" | "codex" | "auto";

const LABELS: Record<ProviderValue, string> = {
  auto: "Auto",
  claude: "Claude",
  codex: "Codex",
};

const ITEMS = [
  { value: "auto", label: LABELS.auto },
  { value: "claude", label: LABELS.claude },
  { value: "codex", label: LABELS.codex },
];

interface ProviderPickerProps {
  value: ProviderValue;
  onChange: (v: ProviderValue) => void;
  label?: string;
  size?: "xs" | "sm";
}

/**
 * Segmented Auto/Claude/Codex picker. Claude is the default. `auto` runs
 * Claude for planning and Codex for execution.
 */
export function ProviderPicker({
  value,
  onChange,
  label = "AGENT",
  size = "xs",
}: ProviderPickerProps) {
  return (
    <HStack gap={2}>
      {label && (
        <Text
          fontSize="2xs"
          color="fg.muted"
          letterSpacing="wide"
          flexShrink={0}
        >
          {label}
        </Text>
      )}
      <SegmentGroup.Root
        value={value}
        onValueChange={(d) => onChange((d.value as ProviderValue) ?? "claude")}
        size={size}
      >
        <SegmentGroup.Indicator />
        <SegmentGroup.Items items={ITEMS} />
      </SegmentGroup.Root>
    </HStack>
  );
}

/** Compact read-only label for the resolved provider that ran a job. */
export function ProviderBadge({ provider }: { provider: string }) {
  const label = LABELS[provider as ProviderValue] ?? provider;
  return (
    <Text textTransform="uppercase" letterSpacing="wider">
      · {label}
    </Text>
  );
}
