import { useState } from "react";
import {
  Box,
  Button,
  Flex,
  HStack,
  Menu,
  Portal,
  Spinner,
  Text,
} from "@chakra-ui/react";
import { LuChevronDown, LuCheck, LuFolderGit2, LuFolder } from "react-icons/lu";
import { useNavigate } from "react-router-dom";

import { Composer } from "./Composer";
import { tasksApi } from "../api/tasks";
import { useCreateProject, usePathSuggestions } from "../hooks/useProjects";
import type { PathSuggestion } from "../types";

export function NewProjectComposer() {
  const navigate = useNavigate();
  const create = useCreateProject();
  const [selected, setSelected] = useState<PathSuggestion | null>(null);
  const { data: suggestions, isLoading: loadingSuggestions } =
    usePathSuggestions(true);

  const eligible = (suggestions ?? []).filter((s) => !s.already_registered);
  const placeholder = selected
    ? "Describe what should happen first (optional)"
    : "Pick a path above, then describe what to do";

  return (
    <Box>
      <PathPicker
        selected={selected}
        onSelect={setSelected}
        options={eligible}
        loading={loadingSuggestions}
      />
      <Composer
        placeholder={placeholder}
        disabled={!selected || create.isPending}
        onSend={async (ask) => {
          if (!selected) return;
          const p = await create.mutateAsync({
            name: selected.name,
            path: selected.path,
            permission_mode: "acceptEdits",
            dangerously_skip: false,
            is_default: false,
          });
          navigate(`/projects/${p.id}`);
          if (ask.trim()) {
            tasksApi.plan(p.id, ask.trim()).catch((err) => {
              console.error("planner failed for new project:", err);
            });
          }
          setSelected(null);
        }}
      />
    </Box>
  );
}

interface PathPickerProps {
  selected: PathSuggestion | null;
  onSelect: (s: PathSuggestion | null) => void;
  options: PathSuggestion[];
  loading: boolean;
}

function PathPicker({ selected, onSelect, options, loading }: PathPickerProps) {
  return (
    <Flex
      align="center"
      gap={2}
      px={4}
      pt={3}
      pb={2}
      borderBottomWidth="1px"
      borderColor="border.subtle"
    >
      <Text fontSize="2xs" color="fg.muted" letterSpacing="wide">
        PATH
      </Text>
      <Menu.Root positioning={{ placement: "top-start" }}>
        <Menu.Trigger asChild>
          <Button
            size="2xs"
            variant={selected ? "subtle" : "outline"}
            colorPalette={selected ? "blue" : "gray"}
            fontFamily={selected ? "mono" : "body"}
            fontWeight="normal"
            justifyContent="space-between"
            gap={2}
            maxW={{ base: "60%", md: "lg" }}
          >
            <HStack gap={1.5} minW={0}>
              <Box lineHeight="0">
                {selected?.is_git ? <LuFolderGit2 /> : <LuFolder />}
              </Box>
              <Text truncate>
                {selected
                  ? selected.path
                  : loading
                    ? "Loading…"
                    : "Pick a project directory"}
              </Text>
            </HStack>
            <LuChevronDown />
          </Button>
        </Menu.Trigger>
        <Portal>
          <Menu.Positioner>
            <Menu.Content maxH="80" overflowY="auto" minW="sm">
              {options.length === 0 && !loading && (
                <Box px={3} py={2} fontSize="xs" color="fg.muted">
                  No unregistered directories found in <code>~/Code</code>,{" "}
                  <code>~/code</code>, <code>~/src</code>, or <code>~/projects</code>.
                </Box>
              )}
              {loading && (
                <HStack px={3} py={2} gap={2}>
                  <Spinner size="xs" />
                  <Text fontSize="xs" color="fg.muted">
                    Scanning…
                  </Text>
                </HStack>
              )}
              {options.map((s) => {
                const current = selected?.path === s.path;
                return (
                  <Menu.Item
                    key={s.path}
                    value={s.path}
                    onClick={() => onSelect(s)}
                  >
                    <HStack gap={2} flex="1" minW={0}>
                      <Box lineHeight="0" color="fg.muted">
                        {s.is_git ? <LuFolderGit2 /> : <LuFolder />}
                      </Box>
                      <Box flex="1" minW={0}>
                        <Text fontSize="sm" fontWeight="medium" truncate>
                          {s.name}
                        </Text>
                        <Text fontSize="2xs" color="fg.subtle" truncate fontFamily="mono">
                          {s.path}
                        </Text>
                      </Box>
                      {current && (
                        <Box color="blue.fg" lineHeight="0">
                          <LuCheck />
                        </Box>
                      )}
                    </HStack>
                  </Menu.Item>
                );
              })}
            </Menu.Content>
          </Menu.Positioner>
        </Portal>
      </Menu.Root>
      {selected && (
        <Button
          size="2xs"
          variant="ghost"
          color="fg.muted"
          onClick={() => onSelect(null)}
        >
          Clear
        </Button>
      )}
    </Flex>
  );
}
