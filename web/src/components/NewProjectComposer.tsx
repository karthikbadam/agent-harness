import { useState } from "react";
import {
  Box,
  Button,
  Flex,
  HStack,
  Input,
  Menu,
  Portal,
  Spinner,
  Text,
} from "@chakra-ui/react";
import {
  LuChevronDown,
  LuCheck,
  LuFolderGit2,
  LuFolder,
  LuFolderPlus,
} from "react-icons/lu";
import { useNavigate } from "react-router-dom";

import { Composer } from "./Composer";
import { tasksApi } from "../api/tasks";
import { useCreateProject, usePathSuggestions } from "../hooks/useProjects";
import type { PathSuggestion } from "../types";

const NEW_FOLDER_BASE = "~/Code/";

export function NewProjectComposer() {
  const navigate = useNavigate();
  const create = useCreateProject();
  const [selected, setSelected] = useState<PathSuggestion | null>(null);
  const [newFolder, setNewFolder] = useState<string | null>(null); // null = existing-path mode
  const { data: suggestions, isLoading: loadingSuggestions } =
    usePathSuggestions(true);

  const eligible = (suggestions ?? []).filter((s) => !s.already_registered);
  const isNew = newFolder !== null;
  const newName = (newFolder ?? "").trim().replace(/^\/+|\/+$/g, "");
  const ready = isNew ? newName.length > 0 : Boolean(selected);
  const placeholder = ready
    ? "Describe what you want to do"
    : isNew
      ? "Name the new folder above"
      : "Pick a path above to start";

  return (
    <Box>
      <PathPicker
        selected={selected}
        onSelect={(s) => {
          setSelected(s);
          setNewFolder(null);
        }}
        options={eligible}
        loading={loadingSuggestions}
        newFolder={newFolder}
        onNewFolder={(v) => {
          setNewFolder(v);
          setSelected(null);
        }}
      />
      <Composer
        placeholder={placeholder}
        disabled={!ready || create.isPending}
        onSend={async (ask, attachmentIds) => {
          if (!ready) return;
          const body = isNew
            ? {
                name: newName.split("/").pop() || newName,
                path: `${NEW_FOLDER_BASE}${newName}`,
                create_dir: true,
                permission_mode: "acceptEdits" as const,
                dangerously_skip: true,
                is_default: false,
              }
            : {
                name: selected!.name,
                path: selected!.path,
                create_dir: false,
                permission_mode: "acceptEdits" as const,
                dangerously_skip: false,
                is_default: false,
              };
          const p = await create.mutateAsync(body);
          navigate(`/projects/${p.id}`);
          if (ask.trim() || attachmentIds.length > 0) {
            tasksApi
              .plan(p.id, ask.trim() || "Explore the project", attachmentIds)
              .catch((err) => {
                console.error("planner failed for new project:", err);
              });
          }
          setSelected(null);
          setNewFolder(null);
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
  newFolder: string | null;
  onNewFolder: (v: string | null) => void;
}

function PathPicker({
  selected,
  onSelect,
  options,
  loading,
  newFolder,
  onNewFolder,
}: PathPickerProps) {
  const isNew = newFolder !== null;

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

      {isNew ? (
        <HStack gap={1} flex="1" minW={0}>
          <Box lineHeight="0" color="blue.fg">
            <LuFolderPlus />
          </Box>
          <Text fontSize="xs" fontFamily="mono" color="fg.muted" flexShrink={0}>
            {NEW_FOLDER_BASE}
          </Text>
          <Input
            autoFocus
            size="xs"
            variant="flushed"
            fontFamily="mono"
            placeholder="new-folder-name"
            value={newFolder ?? ""}
            onChange={(e) => onNewFolder(e.target.value)}
            maxW="14rem"
          />
          <Button
            size="2xs"
            variant="ghost"
            color="fg.muted"
            onClick={() => onNewFolder(null)}
          >
            Cancel
          </Button>
        </HStack>
      ) : (
        <>
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
                maxW={{ base: "55%", md: "lg" }}
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
                  <Menu.Item value="__new__" onClick={() => onNewFolder("")}>
                    <HStack gap={2} color="blue.fg">
                      <Box lineHeight="0">
                        <LuFolderPlus />
                      </Box>
                      <Text fontSize="sm" fontWeight="medium">
                        New folder…
                      </Text>
                    </HStack>
                  </Menu.Item>
                  <Menu.Separator />
                  {options.length === 0 && !loading && (
                    <Box px={3} py={2} fontSize="xs" color="fg.muted">
                      No unregistered directories found in <code>~/Code</code>,{" "}
                      <code>~/src</code>, or <code>~/projects</code>.
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
                            <Text
                              fontSize="2xs"
                              color="fg.subtle"
                              truncate
                              fontFamily="mono"
                            >
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
        </>
      )}
    </Flex>
  );
}
