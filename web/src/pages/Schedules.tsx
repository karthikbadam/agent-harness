import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Center,
  Code,
  Drawer,
  Field,
  Flex,
  Heading,
  HStack,
  IconButton,
  Input,
  Portal,
  Spinner,
  Stack,
  Switch,
  Text,
  Textarea,
} from "@chakra-ui/react";
import { LuPlus, LuTrash2 } from "react-icons/lu";

import { Shell } from "../components/Shell";
import { CronPicker } from "../components/CronPicker";
import { useProjects } from "../hooks/useProjects";
import {
  useCreateSchedule,
  useDeleteSchedule,
  useSchedules,
  useUpdateSchedule,
} from "../hooks/useSchedules";
import type { ProjectOut, ScheduleOut } from "../types";

export function SchedulesPage() {
  const { data: schedules, isLoading, error } = useSchedules();
  const { data: projects } = useProjects();
  const [open, setOpen] = useState(false);

  const groups = useMemo(
    () => groupByProject(schedules ?? [], projects ?? []),
    [schedules, projects],
  );

  return (
    <Shell
      title="Schedules"
      right={
        <Button size="xs" colorPalette="blue" onClick={() => setOpen(true)} gap={1.5}>
          <LuPlus />
          New
        </Button>
      }
    >
      <Box maxW="container.md">
        {isLoading && (
          <Center py={10}>
            <Spinner />
          </Center>
        )}
        {error && <Text color="red.fg">Failed to load schedules.</Text>}
        {schedules && schedules.length === 0 && (
          <Center py={12}>
            <Stack gap={2} align="center" maxW="md" textAlign="center">
              <Heading size="sm" color="fg.muted">
                No schedules
              </Heading>
              <Text fontSize="sm" color="fg.subtle">
                Schedules fire a job on a cron. Tap{" "}
                <Box as="b" color="fg">
                  New
                </Box>{" "}
                to set one up.
              </Text>
            </Stack>
          </Center>
        )}
        <Stack gap={6}>
          {groups.map((g) => (
            <Stack key={g.projectId} gap={2.5}>
              <Flex align="baseline" gap={2}>
                <Heading
                  size="xs"
                  color="fg.muted"
                  textTransform="uppercase"
                  letterSpacing="wider"
                  fontWeight="medium"
                >
                  {g.label}
                </Heading>
                <Text fontSize="2xs" color="fg.subtle">
                  {g.schedules.length}
                </Text>
              </Flex>
              <Stack gap={2}>
                {g.schedules.map((s) => (
                  <ScheduleRow key={s.id} schedule={s} />
                ))}
              </Stack>
            </Stack>
          ))}
        </Stack>
      </Box>
      <NewScheduleDrawer open={open} onClose={() => setOpen(false)} />
    </Shell>
  );
}

interface ScheduleGroup {
  projectId: string;
  label: string;
  schedules: ScheduleOut[];
}

function groupByProject(
  schedules: ScheduleOut[],
  projects: ProjectOut[],
): ScheduleGroup[] {
  const nameById = new Map(projects.map((p) => [p.id, p.name]));
  const m = new Map<string, ScheduleOut[]>();
  for (const s of schedules) {
    const arr = m.get(s.project_id) ?? [];
    arr.push(s);
    m.set(s.project_id, arr);
  }
  return Array.from(m.entries())
    .map(([pid, ss]) => ({
      projectId: pid,
      label: nameById.get(pid) ?? pid.slice(0, 8),
      schedules: ss.sort((a, b) => a.name.localeCompare(b.name)),
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function ScheduleRow({ schedule }: { schedule: ScheduleOut }) {
  const update = useUpdateSchedule(schedule.id);
  const del = useDeleteSchedule();
  return (
    <Box bg="bg.subtle" rounded="lg" px={4} py={3.5}>
      <Stack gap={2.5}>
        <Flex justify="space-between" align="flex-start" gap={2}>
          <Stack gap={1} flex="1" minW={0}>
            <Text fontWeight="medium" lineHeight="short" truncate>
              {schedule.name}
            </Text>
            <HStack gap={2} fontSize="2xs" color="fg.muted">
              <Box
                boxSize="1.5"
                rounded="full"
                bg={schedule.enabled ? "green.solid" : "border"}
              />
              <Text fontWeight="medium" color={schedule.enabled ? "fg" : "fg.muted"}>
                {schedule.enabled ? "Enabled" : "Disabled"}
              </Text>
              <Text>·</Text>
              <Code fontSize="2xs" px={1} py={0} bg="bg" rounded="sm">
                {schedule.cron}
              </Code>
              <Text>UTC</Text>
            </HStack>
          </Stack>
          <HStack gap={1.5}>
            <Switch.Root
              size="sm"
              checked={schedule.enabled}
              onCheckedChange={(d) => update.mutate({ enabled: d.checked })}
            >
              <Switch.HiddenInput />
              <Switch.Control />
            </Switch.Root>
            <IconButton
              aria-label="Delete schedule"
              size="2xs"
              variant="ghost"
              color="fg.subtle"
              _hover={{ color: "red.fg", bg: "red.subtle" }}
              onClick={() => {
                if (confirm("Delete this schedule?")) del.mutate(schedule.id);
              }}
            >
              <LuTrash2 />
            </IconButton>
          </HStack>
        </Flex>
        <Text fontSize="xs" color="fg.muted" truncate>
          {schedule.prompt}
        </Text>
      </Stack>
    </Box>
  );
}

function NewScheduleDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const projects = useProjects();
  const create = useCreateSchedule();
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [prompt, setPrompt] = useState("");

  const submit = async () => {
    if (!name.trim() || !projectId || !cron.trim() || !prompt.trim()) return;
    await create.mutateAsync({
      project_id: projectId,
      name: name.trim(),
      cron: cron.trim(),
      prompt: prompt.trim(),
      enabled: true,
    });
    setName("");
    setProjectId("");
    setPrompt("");
    setCron("0 9 * * *");
    onClose();
  };

  return (
    <Drawer.Root
      open={open}
      onOpenChange={(e) => (e.open ? null : onClose())}
      placement="bottom"
    >
      <Portal>
        <Drawer.Backdrop />
        <Drawer.Positioner>
          <Drawer.Content roundedTop="lg" pb="env(safe-area-inset-bottom)">
            <Drawer.Header>
              <Drawer.Title>New schedule</Drawer.Title>
            </Drawer.Header>
            <Drawer.Body>
              <Stack gap={4}>
                <Field.Root>
                  <Field.Label>Name</Field.Label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Daily book progress"
                  />
                </Field.Root>
                <Field.Root>
                  <Field.Label>Project</Field.Label>
                  <select
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}
                    style={{
                      padding: 8,
                      borderRadius: 6,
                      borderWidth: 1,
                    }}
                  >
                    <option value="">-- pick --</option>
                    {(projects.data ?? []).map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} ({p.path})
                      </option>
                    ))}
                  </select>
                </Field.Root>
                <CronPicker value={cron} onChange={setCron} />
                <Field.Root>
                  <Field.Label>Prompt</Field.Label>
                  <Textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    rows={3}
                  />
                </Field.Root>
                <Box pt={2}>
                  <Button
                    colorPalette="blue"
                    onClick={submit}
                    loading={create.isPending}
                    disabled={!name.trim() || !projectId || !prompt.trim()}
                    w="full"
                  >
                    Save
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
