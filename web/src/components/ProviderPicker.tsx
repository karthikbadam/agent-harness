import { HStack, SegmentGroup, Text } from "@chakra-ui/react";

export type ProviderValue = "claude" | "codex" | "auto";
// "inherit" means: don't send an override; the job inherits the project default.
export type ProviderChoice = ProviderValue | "inherit";

const LABELS: Record<ProviderValue, string> = {
  auto: "Auto",
  claude: "Claude",
  codex: "Codex",
};

interface ProviderPickerProps {
  value: ProviderChoice;
  onChange: (v: ProviderChoice) => void;
  /** Show a leading "Default" segment that maps to project inheritance. */
  includeInherit?: boolean;
  label?: string;
  size?: "xs" | "sm";
}

/**
 * Segmented Auto/Claude/Codex picker. `auto` runs Claude for planning and
 * Codex for execution. When `includeInherit` is set, a leading "Default"
 * segment lets a one-off (e.g. an ad-hoc job) fall back to the project default.
 */
export function ProviderPicker({
  value,
  onChange,
  includeInherit = false,
  label = "AGENT",
  size = "xs",
}: ProviderPickerProps) {
  const items = [
    ...(includeInherit ? [{ value: "inherit", label: "Default" }] : []),
    { value: "auto", label: LABELS.auto },
    { value: "claude", label: LABELS.claude },
    { value: "codex", label: LABELS.codex },
  ];
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
        onValueChange={(d) =>
          onChange((d.value as ProviderChoice) ?? "inherit")
        }
        size={size}
      >
        <SegmentGroup.Indicator />
        <SegmentGroup.Items items={items} />
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
