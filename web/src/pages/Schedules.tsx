import { useState } from "react";
import {
  Box,
  Button,
  Center,
  Code,
  Drawer,
  Field,
  Flex,
  IconButton,
  Input,
  Portal,
  Spinner,
  Stack,
  Text,
  Textarea,
} from "@chakra-ui/react";
import { LuPlus, LuTrash2 } from "react-icons/lu";

import { Shell } from "../components/Shell";
import { CronPicker } from "../components/CronPicker";
import { StatusPill } from "../components/StatusPill";
import { useProjects } from "../hooks/useProjects";
import {
  useCreateSchedule,
  useDeleteSchedule,
  useSchedules,
  useUpdateSchedule,
} from "../hooks/useSchedules";
import type { ScheduleOut } from "../types";

export function SchedulesPage() {
  const { data, isLoading, error } = useSchedules();
  const [open, setOpen] = useState(false);
  return (
    <Shell title="Schedules">
      <Stack gap={3}>
        {isLoading && (
          <Center py={8}>
            <Spinner />
          </Center>
        )}
        {error && <Text color="red.fg">Failed to load schedules.</Text>}
        {data && data.length === 0 && (
          <Text color="fg.muted">No schedules yet. Tap + to create one.</Text>
        )}
        {data?.map((s) => (
          <ScheduleRow key={s.id} schedule={s} />
        ))}
      </Stack>
      <NewScheduleFab onClick={() => setOpen(true)} />
      <NewScheduleDrawer open={open} onClose={() => setOpen(false)} />
    </Shell>
  );
}

function ScheduleRow({ schedule }: { schedule: ScheduleOut }) {
  const update = useUpdateSchedule(schedule.id);
  const del = useDeleteSchedule();
  return (
    <Box borderWidth="1px" borderRadius="md" px={4} py={3}>
      <Flex justify="space-between" align="center" mb={2}>
        <Text fontWeight="medium">{schedule.name}</Text>
        <Flex gap={2} align="center">
          <StatusPill status={schedule.enabled ? "running" : "stopped"} />
          <IconButton
            aria-label="delete"
            size="xs"
            variant="ghost"
            onClick={() => {
              if (confirm("Delete this schedule?")) del.mutate(schedule.id);
            }}
          >
            <LuTrash2 />
          </IconButton>
        </Flex>
      </Flex>
      <Stack gap={1} fontSize="sm">
        <Text>
          <Code>{schedule.cron}</Code> · UTC
        </Text>
        <Text color="fg.muted" truncate>
          {schedule.prompt}
        </Text>
        <Button
          size="xs"
          variant="ghost"
          alignSelf="flex-start"
          onClick={() => update.mutate({ enabled: !schedule.enabled })}
        >
          {schedule.enabled ? "Disable" : "Enable"}
        </Button>
      </Stack>
    </Box>
  );
}

function NewScheduleFab({ onClick }: { onClick: () => void }) {
  return (
    <IconButton
      aria-label="New schedule"
      position="fixed"
      bottom="calc(80px + env(safe-area-inset-bottom))"
      right={4}
      rounded="full"
      size="lg"
      colorPalette="blue"
      shadow="lg"
      onClick={onClick}
    >
      <LuPlus />
    </IconButton>
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
    <Drawer.Root open={open} onOpenChange={(e) => (e.open ? null : onClose())} placement="bottom">
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
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Daily book progress" />
                </Field.Root>
                <Field.Root>
                  <Field.Label>Project</Field.Label>
                  <select
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}
                    style={{ padding: 8, borderRadius: 6, borderWidth: 1 }}
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
                  <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} />
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
