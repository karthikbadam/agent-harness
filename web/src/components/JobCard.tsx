import { useNavigate } from "react-router-dom";
import { Box, Button, Flex, IconButton, Stack, Text } from "@chakra-ui/react";
import { LuTrash2 } from "react-icons/lu";

import type { JobOut } from "../types";
import { useDeleteJob, useStopJob } from "../hooks/useJobs";
import { StatusPill } from "./StatusPill";

export function JobCard({ job }: { job: JobOut }) {
  const navigate = useNavigate();
  const stop = useStopJob(job.id);
  const del = useDeleteJob();
  const created = new Date(job.created_at);
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
      <Stack gap={1}>
        <Flex justify="space-between" align="center" gap={2}>
          <Text fontWeight="medium" truncate flex="1">
            {job.title || "(untitled)"}
          </Text>
          <Flex gap={2} align="center">
            <StatusPill status={job.status} />
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
                size="xs"
                variant="ghost"
                colorPalette="red"
                onClick={onDelete}
                loading={del.isPending}
              >
                <LuTrash2 />
              </IconButton>
            )}
          </Flex>
        </Flex>
        <Flex justify="space-between" fontSize="xs" color="fg.muted">
          <Text>
            {(job.turns ?? []).length} turn{(job.turns ?? []).length === 1 ? "" : "s"}
          </Text>
          <Text>{created.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</Text>
        </Flex>
      </Stack>
    </Box>
  );
}
