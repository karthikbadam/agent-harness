import { Box, Field, Input, Stack, Text } from "@chakra-ui/react";

const PRESETS: { label: string; cron: string }[] = [
  { label: "Every minute (test)", cron: "* * * * *" },
  { label: "Every hour, on the hour", cron: "0 * * * *" },
  { label: "Daily at 9am", cron: "0 9 * * *" },
  { label: "Daily at 9pm", cron: "0 21 * * *" },
  { label: "Weekdays at 9am", cron: "0 9 * * 1-5" },
  { label: "Mondays at 9am", cron: "0 9 * * 1" },
];

export interface CronPickerProps {
  value: string;
  onChange: (cron: string) => void;
}

export function CronPicker({ value, onChange }: CronPickerProps) {
  const matchedPreset = PRESETS.find((p) => p.cron === value)?.cron ?? "";
  return (
    <Stack gap={2}>
      <Field.Root>
        <Field.Label>Preset</Field.Label>
        <select
          value={matchedPreset}
          onChange={(e) => {
            if (e.target.value) onChange(e.target.value);
          }}
          style={{ padding: 8, borderRadius: 6, borderWidth: 1 }}
        >
          <option value="">-- choose preset or write cron below --</option>
          {PRESETS.map((p) => (
            <option key={p.cron} value={p.cron}>
              {p.label} ({p.cron})
            </option>
          ))}
        </select>
      </Field.Root>
      <Field.Root>
        <Field.Label>Cron (m h dom mon dow)</Field.Label>
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="0 9 * * *"
          fontFamily="mono"
        />
      </Field.Root>
      <Box fontSize="xs" color="fg.muted">
        <Text>Server timezone is UTC.</Text>
        <Text>
          Examples: "*/5 * * * *" every 5m, "0 9-17 * * 1-5" hourly weekdays
          9–5.
        </Text>
      </Box>
    </Stack>
  );
}
