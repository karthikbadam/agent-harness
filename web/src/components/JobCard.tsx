import { Link as RouterLink } from "react-router-dom";
import { Box, Flex, Stack, Text } from "@chakra-ui/react";

import type { JobOut } from "../types";
import { StatusPill } from "./StatusPill";

export function JobCard({ job }: { job: JobOut }) {
  const created = new Date(job.created_at);
  return (
    <Box
      as={RouterLink}
      // @ts-expect-error react-router prop
      to={`/jobs/${job.id}`}
      display="block"
      borderWidth="1px"
      borderRadius="md"
      px={4}
      py={3}
      _hover={{ bg: "bg.subtle" }}
    >
      <Stack gap={1}>
        <Flex justify="space-between" align="center">
          <Text fontWeight="medium" truncate>
            {job.title || "(untitled)"}
          </Text>
          <StatusPill status={job.status} />
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
