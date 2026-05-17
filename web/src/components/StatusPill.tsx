import { Badge } from "@chakra-ui/react";

const COLORS: Record<string, string> = {
  // Job statuses
  queued: "gray",
  running: "blue",
  done: "green",
  failed: "red",
  stopped: "orange",
  // Task statuses
  pending: "gray",
  ready: "teal",
  canceled: "orange",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <Badge
      colorPalette={COLORS[status] ?? "gray"}
      variant="subtle"
      fontSize="2xs"
      px={1.5}
      textTransform="lowercase"
    >
      {status}
    </Badge>
  );
}
