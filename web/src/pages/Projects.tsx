import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Center,
  Heading,
  HStack,
  Spinner,
  Stack,
  Text,
  Textarea,
} from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { TaskCard } from "../components/TaskCard";
import { useProjects } from "../hooks/useProjects";
import { usePlan, useTasks } from "../hooks/useTasks";
import type { ProjectOut, TaskOut } from "../types";

export function ProjectsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { data: projects, isLoading } = useProjects();
  const selectedId = params.get("project") ?? projects?.[0]?.id;

  return (
    <Shell title="Projects" right={<Button size="xs" variant="ghost" onClick={() => navigate("/jobs")}>Jobs</Button>}>
      <Stack gap={4}>
        {isLoading && (
          <Center py={6}>
            <Spinner />
          </Center>
        )}
        {projects && projects.length === 0 && (
          <Text color="fg.muted" textAlign="center" py={6}>
            No projects yet.
          </Text>
        )}
        {projects && projects.length > 0 && (
          <HStack gap={2} overflowX="auto" pb={1}>
            {projects.map((p) => (
              <Button
                key={p.id}
                size="xs"
                variant={p.id === selectedId ? "solid" : "outline"}
                onClick={() => setParams({ project: p.id })}
              >
                {p.name}
              </Button>
            ))}
          </HStack>
        )}
        {selectedId && projects && projects.length > 0 && (() => {
          const proj = projects.find((p) => p.id === selectedId) ?? projects[0];
          return proj ? <ProjectTasks project={proj} /> : null;
        })()}
      </Stack>
    </Shell>
  );
}

function ProjectTasks({ project }: { project: ProjectOut }) {
  const { data: tasks, isLoading } = useTasks(project.id);
  const plan = usePlan(project.id);
  const [ask, setAsk] = useState("");

  const groups = useMemo(() => groupByPhase(tasks ?? []), [tasks]);

  return (
    <Stack gap={4}>
      <Box borderWidth="1px" borderRadius="md" p={3}>
        <Text fontSize="xs" color="fg.muted" mb={2}>
          {project.path}
        </Text>
        <Textarea
          placeholder="Describe an ask. Hit Plan and the planner will decompose it into draft tasks."
          value={ask}
          onChange={(e) => setAsk(e.target.value)}
          size="sm"
          rows={2}
        />
        <HStack gap={2} mt={2} justify="flex-end">
          <Button
            size="xs"
            onClick={async () => {
              if (!ask.trim()) return;
              await plan.mutateAsync(ask.trim());
              setAsk("");
            }}
            loading={plan.isPending}
            disabled={!ask.trim()}
          >
            Plan
          </Button>
        </HStack>
      </Box>
      {isLoading && (
        <Center py={4}>
          <Spinner />
        </Center>
      )}
      {tasks && tasks.length === 0 && (
        <Text color="fg.muted" textAlign="center" py={4} fontSize="sm">
          No tasks. Use the planner above or create one via the API.
        </Text>
      )}
      <Stack gap={5}>
        {groups.map((g) => (
          <Stack key={g.label} gap={2}>
            <Heading
              size="xs"
              color="fg.muted"
              textTransform="uppercase"
              letterSpacing="wider"
            >
              {g.label} ({g.tasks.length})
            </Heading>
            <Stack gap={2}>
              {g.tasks.map((t) => (
                <TaskCard key={t.id} task={t} />
              ))}
            </Stack>
          </Stack>
        ))}
      </Stack>
    </Stack>
  );
}

interface PhaseGroup {
  label: string;
  tasks: TaskOut[];
}

function groupByPhase(tasks: TaskOut[]): PhaseGroup[] {
  // Order matters — first matching bucket wins per task.
  // awaiting_ack tasks are technically status=running but show up under their
  // own group so the user sees the actionable subset distinctly.
  const order: { label: string; match: (t: TaskOut) => boolean }[] = [
    { label: "Awaiting ack", match: (t) => t.phase === "awaiting_ack" },
    { label: "Running", match: (t) => t.status === "running" },
    { label: "Ready", match: (t) => t.status === "ready" },
    { label: "Blocked", match: (t) => t.status === "pending" },
    { label: "Failed", match: (t) => t.status === "failed" },
    { label: "Done", match: (t) => t.status === "done" },
    { label: "Canceled", match: (t) => t.status === "canceled" },
  ];
  const seen = new Set<string>();
  const groups: PhaseGroup[] = [];
  for (const g of order) {
    const ts = tasks.filter((t) => !seen.has(t.id) && g.match(t));
    ts.forEach((t) => seen.add(t.id));
    if (ts.length > 0) groups.push({ label: g.label, tasks: ts });
  }
  return groups;
}
