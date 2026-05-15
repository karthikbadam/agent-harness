import { useNavigate } from "react-router-dom";
import { Box, Center, Spinner, Stack, Text } from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { Composer } from "../components/Composer";
import { JobCard } from "../components/JobCard";
import { useCreateJob, useJobs } from "../hooks/useJobs";

export function JobsPage() {
  const navigate = useNavigate();
  const { data: jobs, isLoading, error } = useJobs();
  const createJob = useCreateJob();

  return (
    <Shell title="Jobs">
      <Box pb="calc(140px + env(safe-area-inset-bottom))">
        <Stack gap={3}>
          {isLoading && (
            <Center py={8}>
              <Spinner />
            </Center>
          )}
          {error && <Text color="red.fg">Failed to load jobs.</Text>}
          {jobs && jobs.length === 0 && (
            <Text color="fg.muted" textAlign="center" py={8}>
              No jobs yet. Type a prompt below to start.
            </Text>
          )}
          {jobs?.map((j) => (
            <JobCard key={j.id} job={j} />
          ))}
        </Stack>
      </Box>
      <Box
        position="fixed"
        left={0}
        right={0}
        bottom={0}
        bg="bg"
        borderTopWidth="1px"
        zIndex={5}
      >
        <Box maxW="container.sm" mx="auto">
          <Composer
            placeholder="What should claude work on?"
            onSend={async (prompt) => {
              const job = await createJob.mutateAsync({ prompt });
              navigate(`/jobs/${job.id}`);
            }}
          />
        </Box>
      </Box>
    </Shell>
  );
}
