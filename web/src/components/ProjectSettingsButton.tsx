import { IconButton, Popover, Portal, Stack, Text } from "@chakra-ui/react";
import { LuSettings2 } from "react-icons/lu";

import { useUpdateProject } from "../hooks/useProjects";
import { ProviderPicker, type ProviderValue } from "./ProviderPicker";
import type { ProjectOut } from "../types";

/**
 * Header affordance to edit a project's stored defaults. Currently the default
 * agent provider; the picker writes straight through to the project via PATCH.
 */
export function ProjectSettingsButton({ project }: { project: ProjectOut }) {
  const update = useUpdateProject(project.id);
  const provider = (project.agent_provider as ProviderValue) ?? "claude";
  return (
    <Popover.Root positioning={{ placement: "bottom-end" }}>
      <Popover.Trigger asChild>
        <IconButton
          aria-label="Project settings"
          size="xs"
          variant="outline"
          loading={update.isPending}
        >
          <LuSettings2 />
        </IconButton>
      </Popover.Trigger>
      <Portal>
        <Popover.Positioner>
          <Popover.Content width="auto">
            <Popover.Arrow />
            <Popover.Body>
              <Stack gap={2}>
                <Text fontSize="xs" fontWeight="medium" color="fg.muted">
                  Default agent
                </Text>
                <ProviderPicker
                  label=""
                  size="sm"
                  value={provider}
                  onChange={(v) => update.mutate({ agent_provider: v })}
                />
                <Text fontSize="2xs" color="fg.subtle" maxW="15rem">
                  New prompts on this project default to this. Auto plans with
                  Claude and executes with Codex.
                </Text>
              </Stack>
            </Popover.Body>
          </Popover.Content>
        </Popover.Positioner>
      </Portal>
    </Popover.Root>
  );
}
