import { useState } from "react";
import {
  Box,
  Button,
  Code,
  Field,
  Flex,
  Heading,
  IconButton,
  Input,
  Stack,
  Switch,
  Text,
} from "@chakra-ui/react";
import { LuTrash2 } from "react-icons/lu";

import { Shell } from "../components/Shell";
import { useAllowlist, useCreateRule, useDeleteRule } from "../hooks/useAllowlist";
import { useProjects, useUpdateProject } from "../hooks/useProjects";
import { usePush } from "../hooks/usePushSubscription";
import { useUI } from "../stores/ui";
import type { ProjectOut } from "../types";

export function SettingsPage() {
  const setToken = useUI((s) => s.setToken);
  const projects = useProjects();
  return (
    <Shell title="Settings">
      <Stack gap={8}>
        <section>
          <Heading size="sm" mb={2}>
            Allowlist
          </Heading>
          <AllowlistSection projects={projects.data ?? []} />
        </section>

        <section>
          <Heading size="sm" mb={2}>
            Projects
          </Heading>
          <Stack gap={3}>
            {(projects.data ?? []).map((p) => (
              <ProjectRow key={p.id} project={p} />
            ))}
            {(projects.data ?? []).length === 0 && (
              <Text color="fg.muted">Create a project from the Jobs page.</Text>
            )}
          </Stack>
        </section>

        <section>
          <Heading size="sm" mb={2}>
            Notifications
          </Heading>
          <NotificationsSection />
        </section>

        <section>
          <Heading size="sm" mb={2}>
            Auth
          </Heading>
          <Button size="sm" colorPalette="red" variant="outline" onClick={() => setToken(null)}>
            Sign out
          </Button>
        </section>
      </Stack>
    </Shell>
  );
}

function AllowlistSection({ projects }: { projects: ProjectOut[] }) {
  const [scope, setScope] = useState<string>("");
  const rules = useAllowlist(scope || undefined);
  const create = useCreateRule();
  const del = useDeleteRule();
  const [draft, setDraft] = useState("");

  const submit = async () => {
    if (!draft.trim()) return;
    await create.mutateAsync({ rule: draft.trim(), project_id: scope || null });
    setDraft("");
  };

  return (
    <Stack gap={3}>
      <Field.Root>
        <Field.Label>Scope</Field.Label>
        <select
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          style={{ padding: 8, borderRadius: 6, borderWidth: 1 }}
        >
          <option value="">Global</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </Field.Root>
      <Stack gap={2}>
        {(rules.data ?? []).map((r) => (
          <Flex
            key={r.id}
            justify="space-between"
            align="center"
            borderWidth="1px"
            borderRadius="md"
            px={3}
            py={2}
          >
            <Stack direction="row" align="center" gap={2}>
              <Code>{r.rule}</Code>
              <Text fontSize="xs" color="fg.muted">
                {r.project_id ? "project" : "global"}
              </Text>
            </Stack>
            <IconButton
              aria-label="delete"
              size="xs"
              variant="ghost"
              onClick={() => {
                if (confirm("Delete this rule?")) del.mutate(r.id);
              }}
            >
              <LuTrash2 />
            </IconButton>
          </Flex>
        ))}
        {rules.data && rules.data.length === 0 && (
          <Text color="fg.muted" fontSize="sm">
            No rules yet.
          </Text>
        )}
      </Stack>
      <Flex gap={2}>
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Bash(npm test:*)"
          fontFamily="mono"
        />
        <Button onClick={submit} loading={create.isPending} disabled={!draft.trim()}>
          Add
        </Button>
      </Flex>
    </Stack>
  );
}

function NotificationsSection() {
  const { eligibility, subscribed, busy, error, subscribe, unsubscribe } = usePush();
  if (!eligibility.ok) {
    const reasons: Record<string, string> = {
      "no-sw": "Service workers not supported by this browser.",
      "no-push": "Push API not supported by this browser.",
      "not-standalone":
        "Add this app to your home screen (Share → Add to Home Screen) and reopen from there. iOS requires standalone mode for notifications.",
      denied:
        "Notification permission is denied. Enable it in iOS Settings → Notifications → harness.",
    };
    return (
      <Box borderWidth="1px" borderRadius="md" px={3} py={2}>
        <Text fontSize="sm" color="fg.muted">
          {reasons[eligibility.reason]}
        </Text>
      </Box>
    );
  }
  return (
    <Stack gap={2}>
      <Flex justify="space-between" align="center">
        <Box>
          <Text fontSize="sm">Push notifications</Text>
          <Text fontSize="xs" color="fg.muted">
            Job done, tool blocked, schedule fired.
          </Text>
        </Box>
        <Button
          size="sm"
          colorPalette={subscribed ? "red" : "blue"}
          variant={subscribed ? "outline" : "solid"}
          loading={busy}
          onClick={() => (subscribed ? unsubscribe() : subscribe())}
        >
          {subscribed ? "Disable" : "Enable"}
        </Button>
      </Flex>
      {error && (
        <Text color="red.fg" fontSize="sm">
          {error}
        </Text>
      )}
    </Stack>
  );
}

function ProjectRow({ project }: { project: ProjectOut }) {
  const update = useUpdateProject(project.id);
  return (
    <Box borderWidth="1px" borderRadius="md" px={4} py={3}>
      <Stack gap={2}>
        <Flex justify="space-between" align="center">
          <Box>
            <Text fontWeight="medium">{project.name}</Text>
            <Text fontSize="xs" color="fg.muted" fontFamily="mono">
              {project.path}
            </Text>
          </Box>
          <Text fontSize="xs" color="fg.muted">
            mode: {project.permission_mode}
          </Text>
        </Flex>
        <Flex justify="space-between" align="center">
          <Box>
            <Text fontSize="sm">Dangerously skip permissions</Text>
            <Text fontSize="xs" color="red.fg">
              Bypasses every permission check. Use only for trusted repos.
            </Text>
          </Box>
          <Switch.Root
            checked={project.dangerously_skip}
            onCheckedChange={(d) => update.mutate({ dangerously_skip: d.checked })}
          >
            <Switch.HiddenInput />
            <Switch.Control>
              <Switch.Thumb />
            </Switch.Control>
          </Switch.Root>
        </Flex>
      </Stack>
    </Box>
  );
}
