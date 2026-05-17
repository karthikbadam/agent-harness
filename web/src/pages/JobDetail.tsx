import { useNavigate, useParams } from "react-router-dom";
import { Box, Button, Center, HStack, Spinner, Text } from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { TurnTranscript } from "../components/TurnTranscript";
import { Composer } from "../components/Composer";
import { useFollowup, useJob, useStopJob } from "../hooks/useJobs";
import { useJobEvents, useJobStream } from "../hooks/useJobStream";

export function JobDetailPage() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const job = useJob(jobId);
  useJobStream(jobId);
  const { data: events = [] } = useJobEvents(jobId);
  const followup = useFollowup(jobId ?? "");
  const stop = useStopJob(jobId ?? "");

  const running = job.data?.status === "running" || job.data?.status === "queued";

  return (
    <Shell
      title={job.data?.title ?? "Job"}
      back={job.data?.task_id ? `/jobs?task_id=${job.data.task_id}` : "/jobs"}
      right={
        running ? (
          <Button size="xs" variant="outline" colorPalette="red" onClick={() => stop.mutate()}>
            Stop
          </Button>
        ) : undefined
      }
    >
      {job.isLoading && (
        <Center py={8}>
          <Spinner />
        </Center>
      )}
      {job.error && <Text color="red.fg">Failed to load job.</Text>}
      {job.data && (
        <HStack gap={2} mb={3} fontSize="xs" color="fg.muted" wrap="wrap">
          {job.data.kind && job.data.kind !== "ad_hoc" && (
            <Text>kind: {job.data.kind}</Text>
          )}
          {job.data.task_id && (
            <>
              <Text>·</Text>
              <Button
                size="2xs"
                variant="outline"
                onClick={() =>
                  navigate(`/projects/${job.data!.project_id}`)
                }
              >
                Open project
              </Button>
            </>
          )}
        </HStack>
      )}
      <Box pb={20}>
        <TurnTranscript events={events} job={job.data} />
      </Box>
      <Box position="fixed" left={0} right={0} bottom={0} bg="bg">
        <Composer
          placeholder={running ? "Wait for current turn..." : "Type a followup..."}
          disabled={running}
          onSend={async (prompt) => {
            await followup.mutateAsync({ prompt });
          }}
        />
      </Box>
    </Shell>
  );
}
