import { useParams } from "react-router-dom";
import { Box, Button, Center, Spinner, Text } from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { TurnTranscript } from "../components/TurnTranscript";
import { Composer } from "../components/Composer";
import { useFollowup, useJob, useStopJob } from "../hooks/useJobs";
import { useJobEvents, useJobStream } from "../hooks/useJobStream";

export function JobDetailPage() {
  const { jobId } = useParams();
  const job = useJob(jobId);
  useJobStream(jobId);
  const { data: events = [] } = useJobEvents(jobId);
  const followup = useFollowup(jobId ?? "");
  const stop = useStopJob(jobId ?? "");

  const running = job.data?.status === "running" || job.data?.status === "queued";

  return (
    <Shell
      title={job.data?.title ?? "Job"}
      back="/"
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
