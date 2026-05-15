import { useNavigate } from "react-router-dom";
import { Box, Button, Flex, Stack, Text } from "@chakra-ui/react";

import type { JobOut } from "../types";
import { useStopJob } from "../hooks/useJobs";
import { StatusPill } from "./StatusPill";

export function JobCard({ job }: { job: JobOut }) {
  const navigate = useNavigate();
  const stop = useStopJob(job.id);
  const created = new Date(job.created_at);
  const live = job.status === "running" || job.status === "queued";

  return (
    <Box
      onClick={() => navigate(`/jobs/${job.id}`)}
      cursor="pointer"
      borderWidth="1px"
      borderRadius="md"
      px={4}
      py={3}
      _hover={{ bg: "bg.subtle" }}
    >
      <Stack gap={1}>
        <Flex justify="space-between" align="center" gap={2}>
          <Text fontWeight="medium" truncate flex="1">
            {job.title || "(untitled)"}
          </Text>
          <Flex gap={2} align="center">
            <StatusPill status={job.status} />
            {live && (
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
            )}
          </Flex>
        </Flex>
        <Flex justify="space-between" fontSize="xs" color="fg.muted">
          <Text>
            {(job.turns ?? []).length} turn{(job.turns ?? []).length === 1 ? "" : "s"}
          </Text>
          <Text>{created.toLocaleString()}</Text>
        </Flex>
      </Stack>
    </Box>
  );
}
