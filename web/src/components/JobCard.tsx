import { useNavigate } from "react-router-dom";
import { Box, Button, Flex, IconButton, Stack, Text } from "@chakra-ui/react";
import { LuTrash2 } from "react-icons/lu";

import type { JobOut } from "../types";
import { parseServerDate } from "../api/dates";
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
      borderWidth="1px"
      borderRadius="md"
      px={4}
      py={3}
      _hover={{ bg: "bg.subtle" }}
      opacity={del.isPending ? 0.5 : 1}
    >
      <Stack gap={2}>
        <Flex justify="space-between" align="flex-start" gap={2}>
          <Text fontWeight="medium" truncate flex="1">
            {job.title || "(untitled)"}
          </Text>
          {live ? (
            <Button
              size="xs"
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
              color="fg.muted"
              _hover={{ color: "red.fg", bg: "red.subtle" }}
              onClick={onDelete}
              loading={del.isPending}
            >
              <LuTrash2 />
            </IconButton>
          )}
        </Flex>
        <Flex justify="space-between" align="center" gap={2}>
          <Flex gap={2} align="center" fontSize="xs" color="fg.muted" wrap="wrap">
            <StatusPill status={job.status} />
            {job.kind && job.kind !== "ad_hoc" && (
              <>
                <Text>·</Text>
                <Text>{job.kind}</Text>
              </>
            )}
            <Text>·</Text>
            <Text>
              {(job.turns ?? []).length} turn{(job.turns ?? []).length === 1 ? "" : "s"}
            </Text>
          </Flex>
          <Text fontSize="xs" color="fg.muted">
            {created.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </Text>
        </Flex>
      </Stack>
    </Box>
  );
}
