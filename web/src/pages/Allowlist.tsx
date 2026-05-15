import { useState } from "react";
import {
  Button,
  Code,
  Field,
  Flex,
  IconButton,
  Input,
  Stack,
  Text,
} from "@chakra-ui/react";
import { LuTrash2 } from "react-icons/lu";

import { Shell } from "../components/Shell";
import { useAllowlist, useCreateRule, useDeleteRule } from "../hooks/useAllowlist";
import { useProjects } from "../hooks/useProjects";

export function AllowlistPage() {
  const projects = useProjects();
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
    <Shell title="Allowlist" back="/">
      <Stack gap={4}>
        <Field.Root>
          <Field.Label>Scope</Field.Label>
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            style={{ padding: 8, borderRadius: 6, borderWidth: 1 }}
          >
            <option value="">Global</option>
            {(projects.data ?? []).map((p) => (
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
    </Shell>
  );
}
