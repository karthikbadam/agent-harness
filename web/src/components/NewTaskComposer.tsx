import { useState } from "react";
import {
  Box,
  Button,
  Flex,
  HStack,
  IconButton,
  Input,
  Stack,
  Switch,
  Text,
  Textarea,
} from "@chakra-ui/react";
import { LuPlus, LuX } from "react-icons/lu";

import { tasksApi } from "../api/tasks";
import { useQueryClient } from "@tanstack/react-query";
import { tasksKey } from "../hooks/useTasks";
import { jobsKey } from "../hooks/useJobs";

interface Props {
  projectId: string;
}

export function NewTaskComposer({ projectId }: Props) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [skipPlan, setSkipPlan] = useState(true);
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setTitle("");
    setPrompt("");
    setSkipPlan(true);
    setBusy(false);
  };
  const close = () => {
    reset();
    setOpen(false);
  };

  const submit = async () => {
    if (!title.trim() || !prompt.trim() || busy) return;
    setBusy(true);
    try {
      await tasksApi.create(
        projectId,
        {
          title: title.trim(),
          prompt: prompt.trim(),
          mode: skipPlan ? "one_shot" : "plan_then_execute",
          order_idx: 0,
        },
        { run: true },
      );
      qc.invalidateQueries({ queryKey: tasksKey(projectId) });
      qc.invalidateQueries({ queryKey: jobsKey });
      close();
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        gap={2}
        w="full"
        justifyContent="flex-start"
        color="fg.muted"
        fontWeight="normal"
        borderStyle="dashed"
      >
        <LuPlus />
        New task
      </Button>
    );
  }

  return (
    <Box bg="bg.subtle" rounded="lg" p={4}>
      <Stack gap={3}>
        <Flex justify="space-between" align="center">
          <Text fontSize="sm" fontWeight="medium">
            New task
          </Text>
          <IconButton aria-label="Cancel" size="xs" variant="ghost" onClick={close}>
            <LuX />
          </IconButton>
        </Flex>
        <Input
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          size="sm"
          autoFocus
        />
        <Textarea
          placeholder="What should the agent do? Be specific — file paths, expected behavior."
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          size="sm"
          rows={3}
        />
        <Flex justify="space-between" align="center" gap={2}>
          <HStack gap={2}>
            <Switch.Root
              checked={skipPlan}
              onCheckedChange={(d) => setSkipPlan(d.checked)}
              size="sm"
            >
              <Switch.HiddenInput />
              <Switch.Control />
              <Switch.Label fontSize="xs">Skip planning</Switch.Label>
            </Switch.Root>
            <Text fontSize="2xs" color="fg.muted">
              {skipPlan
                ? "runs directly in project.path"
                : "plans first; needs ack to execute in a worktree"}
            </Text>
          </HStack>
          <Button
            size="xs"
            colorPalette="blue"
            onClick={submit}
            loading={busy}
            disabled={!title.trim() || !prompt.trim()}
          >
            Create & run
          </Button>
        </Flex>
      </Stack>
    </Box>
  );
}
