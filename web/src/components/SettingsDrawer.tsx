import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Drawer,
  Field,
  Flex,
  HStack,
  Heading,
  Input,
  Portal,
  SegmentGroup,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useTheme } from "next-themes";
import { LuCalendarClock } from "react-icons/lu";

import { useProjects, useUpdateProject } from "../hooks/useProjects";
import { useUI } from "../stores/ui";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function SettingsDrawer({ open, onClose }: Props) {
  const navigate = useNavigate();
  const setToken = useUI((s) => s.setToken);
  const projects = useProjects();
  const defaultProject = (projects.data ?? []).find((p) => p.is_default) ?? null;
  const updateProject = useUpdateProject(defaultProject?.id ?? "");

  const [pathDraft, setPathDraft] = useState("");
  useEffect(() => {
    setPathDraft(defaultProject?.path ?? "");
  }, [defaultProject?.path]);

  const savePath = () => {
    const v = pathDraft.trim();
    if (!defaultProject || !v || v === defaultProject.path) return;
    updateProject.mutate({ path: v });
  };

  return (
    <Drawer.Root open={open} onOpenChange={(e) => (e.open ? null : onClose())} placement="end">
      <Portal>
        <Drawer.Backdrop />
        <Drawer.Positioner>
          <Drawer.Content>
            <Drawer.Header>
              <Drawer.Title>Settings</Drawer.Title>
            </Drawer.Header>
            <Drawer.Body>
              <Stack gap={8}>
                <ThemeSection />

                <section>
                  <Heading size="sm" mb={2}>
                    Default workspace
                  </Heading>
                  {defaultProject ? (
                    <Stack gap={2}>
                      <Text fontSize="xs" color="fg.muted">
                        New jobs run in this folder.
                      </Text>
                      <Field.Root>
                        <Input
                          value={pathDraft}
                          onChange={(e) => setPathDraft(e.target.value)}
                          fontFamily="mono"
                          fontSize="sm"
                        />
                      </Field.Root>
                      <Flex justify="flex-end">
                        <Button
                          size="sm"
                          onClick={savePath}
                          loading={updateProject.isPending}
                          disabled={!pathDraft.trim() || pathDraft.trim() === defaultProject.path}
                        >
                          Save
                        </Button>
                      </Flex>
                    </Stack>
                  ) : (
                    <Text color="fg.muted" fontSize="sm">
                      Bootstrapping…
                    </Text>
                  )}
                </section>

                <section>
                  <Heading size="sm" mb={2}>
                    Advanced
                  </Heading>
                  <Stack gap={2}>
                    <Button
                      variant="outline"
                      justifyContent="flex-start"
                      onClick={() => {
                        onClose();
                        navigate("/schedules");
                      }}
                    >
                      <LuCalendarClock /> Schedules
                    </Button>
                  </Stack>
                </section>

                <Box pt={4} borderTopWidth="1px">
                  <Button
                    size="sm"
                    colorPalette="red"
                    variant="ghost"
                    onClick={() => {
                      setToken(null);
                      onClose();
                    }}
                  >
                    Sign out
                  </Button>
                </Box>
              </Stack>
            </Drawer.Body>
          </Drawer.Content>
        </Drawer.Positioner>
      </Portal>
    </Drawer.Root>
  );
}

function ThemeSection() {
  const { theme, setTheme, resolvedTheme } = useTheme();
  const value = theme === "system" ? "system" : (resolvedTheme ?? "light");
  return (
    <section>
      <Heading size="sm" mb={2}>
        Appearance
      </Heading>
      <SegmentGroup.Root
        value={value}
        onValueChange={(d) => setTheme(d.value ?? "system")}
        size="sm"
      >
        <SegmentGroup.Indicator />
        <SegmentGroup.Items
          items={[
            { value: "light", label: "Light" },
            { value: "dark", label: "Dark" },
            { value: "system", label: "System" },
          ]}
        />
      </SegmentGroup.Root>
      <HStack mt={2} gap={2}>
        <Text fontSize="xs" color="fg.muted">
          Follows your phone or laptop when set to System.
        </Text>
      </HStack>
    </section>
  );
}
