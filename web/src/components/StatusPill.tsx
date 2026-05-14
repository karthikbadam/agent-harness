import { Badge } from "@chakra-ui/react";

const COLORS: Record<string, string> = {
  queued: "gray",
  running: "blue",
  done: "green",
  failed: "red",
  stopped: "orange",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <Badge colorPalette={COLORS[status] ?? "gray"} variant="subtle">
      {status}
    </Badge>
  );
}
