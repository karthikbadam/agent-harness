import { useNavigate } from "react-router-dom";
import { Box, Button, Flex, HStack, IconButton, Stack, Text } from "@chakra-ui/react";
import { LuTrash2, LuListTodo } from "react-icons/lu";

import type { JobOut } from "../types";
import { parseServerDate, relativeTime } from "../api/dates";
import { useDeleteJob, useStopJob } from "../hooks/useJobs";
import { StatusPill } from "./StatusPill";

export function JobCard({ job }: { job: JobOut }) {
  const navigate = useNavigate();
  const stop = useStopJob(job.id);
  const del = useDeleteJob();
  const created = parseServerDate(job.created_at);
  const live = job.status === "running" || job.status === "queued";

  const onDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm(`Delete this job?\n\n${job.title || job.id}`)) {
      del.mutate(job.id);
    }
  };

  return (
    <Box
      onClick={() => navigate(`/jobs/${job.id}`)}
      cursor="pointer"
      bg="bg"
      rounded="lg"
      px={4}
      py={3.5}
      _hover={{ bg: "bg.muted" }}
      transition="background-color 0.15s"
      opacity={del.isPending ? 0.5 : 1}
    >
      <Flex justify="space-between" align="flex-start" gap={3}>
        <Stack gap={1} flex="1" minW={0}>
          <Text fontWeight="medium" truncate lineHeight="short">
            {job.title || "(untitled)"}
          </Text>
          <HStack gap={2} fontSize="2xs" color="fg.muted" wrap="wrap">
            <StatusPill status={job.status} />
            {job.kind && job.kind !== "ad_hoc" && (
              <Text textTransform="uppercase" letterSpacing="wider">
                · {job.kind}
              </Text>
            )}
            {job.task_id && (
              <HStack gap={1}>
                <Text>·</Text>
                <Box lineHeight="0" color="fg.muted">
                  <LuListTodo />
                </Box>
                <Text fontFamily="mono">{job.task_id.slice(0, 8)}</Text>
              </HStack>
            )}
            <Text>· {(job.turns ?? []).length} turn{(job.turns ?? []).length === 1 ? "" : "s"}</Text>
            <Text color="fg.subtle">· {relativeTime(created)}</Text>
          </HStack>
        </Stack>
        <HStack gap={1} flexShrink={0}>
          {live ? (
            <Button
              size="2xs"
              variant="outline"
              colorPalette="red"
              onClick={(e) => {
                e.stopPropagation();
                stop.mutate();
              }}
              loading={stop.isPending}
            >
              Stop
            </Button>
          ) : (
            <IconButton
              aria-label="Delete job"
              size="2xs"
              variant="ghost"
              color="fg.subtle"
              _hover={{ color: "red.fg", bg: "red.subtle" }}
              onClick={onDelete}
              loading={del.isPending}
            >
              <LuTrash2 />
            </IconButton>
          )}
        </HStack>
      </Flex>
    </Box>
  );
}
