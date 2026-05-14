import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Center,
  Drawer,
  Field,
  IconButton,
  Input,
  Portal,
  Spinner,
  Stack,
  Text,
  Textarea,
  createListCollection,
} from "@chakra-ui/react";
import { LuPlus } from "react-icons/lu";

import { Shell } from "../components/Shell";
import { JobCard } from "../components/JobCard";
import { useCreateJob, useJobs } from "../hooks/useJobs";
import { useCreateProject, useProjects } from "../hooks/useProjects";

export function JobsPage() {
  const { data: jobs, isLoading, error } = useJobs();
  const [open, setOpen] = useState(false);
  return (
    <Shell title="Jobs">
      <Stack gap={3}>
        {isLoading && (
          <Center py={8}>
            <Spinner />
          </Center>
        )}
        {error && <Text color="red.fg">Failed to load jobs.</Text>}
        {jobs && jobs.length === 0 && (
          <Text color="fg.muted">No jobs yet. Tap + to create one.</Text>
        )}
        {jobs?.map((j) => (
          <JobCard key={j.id} job={j} />
        ))}
      </Stack>
      <Fab onClick={() => setOpen(true)} />
      <NewJobDrawer open={open} onClose={() => setOpen(false)} />
    </Shell>
  );
}

function Fab({ onClick }: { onClick: () => void }) {
  return (
    <IconButton
      aria-label="New job"
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

function NewJobDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const projects = useProjects();
  const createProject = useCreateProject();
  const createJob = useCreateJob();
  const navigate = useNavigate();

  const [showNewProject, setShowNewProject] = useState(false);
  const [projectId, setProjectId] = useState<string>("");
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectPath, setNewProjectPath] = useState("");
  const [prompt, setPrompt] = useState("");

  const list = projects.data ?? [];

  const submit = async () => {
    let pid = projectId;
    if (showNewProject) {
      if (!newProjectName.trim() || !newProjectPath.trim()) return;
      const p = await createProject.mutateAsync({
        name: newProjectName.trim(),
        path: newProjectPath.trim(),
        permission_mode: "acceptEdits",
        dangerously_skip: false,
      });
      pid = p.id;
    }
    if (!pid || !prompt.trim()) return;
    const job = await createJob.mutateAsync({
      project_id: pid,
      prompt: prompt.trim(),
      title: prompt.trim().slice(0, 80),
    });
    onClose();
    navigate(`/jobs/${job.id}`);
  };

  const collection = createListCollection({
    items: list.map((p) => ({ label: `${p.name} (${p.path})`, value: p.id })),
  });
  void collection; // (kept for future <Select.Root> upgrade)

  return (
    <Drawer.Root open={open} onOpenChange={(e) => (e.open ? null : onClose())} placement="bottom">
      <Portal>
        <Drawer.Backdrop />
        <Drawer.Positioner>
          <Drawer.Content roundedTop="lg" pb="env(safe-area-inset-bottom)">
            <Drawer.Header>
              <Drawer.Title>New job</Drawer.Title>
            </Drawer.Header>
            <Drawer.Body>
              <Stack gap={4}>
                {!showNewProject && list.length > 0 && (
                  <Field.Root>
                    <Field.Label>Project</Field.Label>
                    <select
                      value={projectId}
                      onChange={(e) => setProjectId(e.target.value)}
                      style={{ padding: 8, borderRadius: 6, borderWidth: 1 }}
                    >
                      <option value="">-- pick --</option>
                      {list.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.path})
                        </option>
                      ))}
                    </select>
                  </Field.Root>
                )}
                {(showNewProject || list.length === 0) && (
                  <>
                    <Field.Root>
                      <Field.Label>New project name</Field.Label>
                      <Input
                        value={newProjectName}
                        onChange={(e) => setNewProjectName(e.target.value)}
                        placeholder="book"
                      />
                    </Field.Root>
                    <Field.Root>
                      <Field.Label>Path (on Mac)</Field.Label>
                      <Input
                        value={newProjectPath}
                        onChange={(e) => setNewProjectPath(e.target.value)}
                        placeholder="/Users/you/book"
                      />
                    </Field.Root>
                  </>
                )}
                {!showNewProject && list.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowNewProject(true)}
                  >
                    + New project
                  </Button>
                )}
                <Field.Root>
                  <Field.Label>Prompt</Field.Label>
                  <Textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder="build chapter 0 of the QFT book"
                    rows={4}
                  />
                </Field.Root>
                <Box pt={2}>
                  <Button
                    colorPalette="blue"
                    onClick={submit}
                    loading={createJob.isPending || createProject.isPending}
                    disabled={!prompt.trim()}
                    w="full"
                  >
                    Start job
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
