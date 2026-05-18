import { useState } from "react";
import {
  Box,
  Button,
  Drawer,
  Flex,
  HStack,
  Portal,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { LuPlane, LuTriangleAlert } from "react-icons/lu";

import {
  useDriver,
  useDriverNotes,
  useSetDriverMode,
} from "../hooks/useDriver";
import { parseServerDate, relativeTime } from "../api/dates";

interface Props {
  projectId: string;
}

export function AutopilotToggle({ projectId }: Props) {
  const { data: state, isLoading } = useDriver(projectId);
  const { data: notes } = useDriverNotes(projectId);
  const setMode = useSetDriverMode(projectId);
  const [drawerOpen, setDrawerOpen] = useState(false);

  if (isLoading || !state) {
    return null;
  }

  const isOn = state.mode === "on";
  const recentEscalations = (notes ?? []).filter(
    (n) => n.severity === "escalate" || n.severity === "warn",
  ).length;
  const noteCount = notes?.length ?? 0;

  // Click behavior:
  //  - off → toggle on
  //  - on  → if there are notes, open the drawer (so a single tap surfaces
  //    what autopilot is doing); user can long-press / right-click later
  //    if we want explicit "turn off". For now, turn off from inside the drawer.
  //  - on with no notes yet → toggle off
  const onClick = () => {
    if (!isOn) {
      setMode.mutate("on");
      return;
    }
    if (noteCount > 0) {
      setDrawerOpen(true);
      return;
    }
    setMode.mutate("off");
  };

  return (
    <>
      <Button
        size="xs"
        variant={isOn ? "solid" : "outline"}
        colorPalette={recentEscalations > 0 ? "red" : "purple"}
        onClick={onClick}
        loading={setMode.isPending}
        gap={1.5}
        px={{ base: 2, md: 3 }}
        position="relative"
        aria-label={isOn ? "Autopilot on" : "Autopilot off"}
      >
        <Box lineHeight="0">
          {recentEscalations > 0 ? <LuTriangleAlert /> : <LuPlane />}
        </Box>
        <Text hideBelow="md">Autopilot</Text>
        {isOn && noteCount > 0 && (
          <Box
            as="span"
            ml={0.5}
            fontSize="2xs"
            opacity={0.8}
            fontWeight="normal"
          >
            · {noteCount}
          </Box>
        )}
      </Button>
      <DriverNotesDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        projectId={projectId}
        onTurnOff={() => {
          setMode.mutate("off");
          setDrawerOpen(false);
        }}
      />
    </>
  );
}

function DriverNotesDrawer({
  open,
  onClose,
  projectId,
  onTurnOff,
}: {
  open: boolean;
  onClose: () => void;
  projectId: string;
  onTurnOff: () => void;
}) {
  const { data: notes } = useDriverNotes(open ? projectId : undefined);
  return (
    <Drawer.Root
      open={open}
      onOpenChange={(e) => (e.open ? null : onClose())}
      placement={{ base: "bottom", md: "end" }}
      size={{ base: "full", md: "md" }}
    >
      <Portal>
        <Drawer.Backdrop />
        <Drawer.Positioner>
          <Drawer.Content pb="env(safe-area-inset-bottom)">
            <Drawer.Header>
              <Stack gap={1}>
                <Drawer.Title>Autopilot activity</Drawer.Title>
                <Text fontSize="xs" color="fg.muted">
                  What the driver is doing without you.
                </Text>
              </Stack>
            </Drawer.Header>
            <Drawer.Body>
              {!notes && (
                <Flex justify="center" py={10}>
                  <Spinner size="sm" />
                </Flex>
              )}
              {notes && notes.length === 0 && (
                <Text fontSize="sm" color="fg.muted" py={6} textAlign="center">
                  No driver activity yet.
                </Text>
              )}
              <Stack gap={2}>
                {(notes ?? []).map((n) => (
                  <NoteRow key={n.id} note={n} />
                ))}
              </Stack>
            </Drawer.Body>
            <Drawer.Footer borderTopWidth="1px" borderColor="border.subtle">
              <HStack justify="space-between" w="full">
                <Button
                  size="sm"
                  variant="outline"
                  colorPalette="purple"
                  onClick={onTurnOff}
                >
                  Turn off autopilot
                </Button>
                <Button variant="outline" size="sm" onClick={onClose}>
                  Close
                </Button>
              </HStack>
            </Drawer.Footer>
          </Drawer.Content>
        </Drawer.Positioner>
      </Portal>
    </Drawer.Root>
  );
}

interface NoteRowProps {
  note: {
    id: string;
    severity: string;
    kind: string;
    message: string;
    created_at: string;
  };
}

function NoteRow({ note }: NoteRowProps) {
  const tone =
    note.severity === "escalate"
      ? "red"
      : note.severity === "warn"
        ? "orange"
        : note.kind === "ran"
          ? "blue"
          : note.kind === "integrated"
            ? "green"
            : "gray";
  const dotColor = {
    red: "red.solid",
    orange: "orange.solid",
    blue: "blue.solid",
    green: "green.solid",
    gray: "border",
  }[tone];
  return (
    <Box bg="bg.subtle" rounded="md" px={3.5} py={3}>
      <Flex gap={2.5} align="flex-start">
        <Box boxSize="1.5" rounded="full" bg={dotColor} mt={1.5} flexShrink={0} />
        <Stack gap={0.5} flex="1" minW={0}>
          <HStack gap={2} fontSize="2xs" color="fg.muted">
            <Text fontWeight="medium" textTransform="uppercase" letterSpacing="wider">
              {note.kind}
            </Text>
            {note.severity !== "info" && (
              <Text textTransform="uppercase" letterSpacing="wider">
                · {note.severity}
              </Text>
            )}
            <Text>· {relativeTime(parseServerDate(note.created_at))}</Text>
          </HStack>
          <Text fontSize="sm" color="fg">
            {note.message}
          </Text>
        </Stack>
      </Flex>
    </Box>
  );
}
