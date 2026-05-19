import { useNavigate, useParams } from "react-router-dom";
import { Box, Button, Center, Spinner, Text } from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { TurnTranscript } from "../components/TurnTranscript";
import { Composer } from "../components/Composer";
import { StickyComposer } from "../components/StickyComposer";
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
  const subtitleParts: string[] = [];
  if (job.data?.kind && job.data.kind !== "ad_hoc") subtitleParts.push(job.data.kind);
  if (job.data?.status) subtitleParts.push(job.data.status);
  const subtitle = subtitleParts.join(" · ") || undefined;

  return (
    <Shell
      title={job.data?.title ?? "Job"}
      subtitle={subtitle}
      back={job.data?.task_id ? `/jobs?task_id=${job.data.task_id}` : "/jobs"}
      composerHeight={110}
      right={
        <>
          {job.data?.task_id && (
            <Button
              size="xs"
              variant="outline"
              onClick={() => navigate(`/projects/${job.data!.project_id}`)}
            >
              Project
            </Button>
          )}
          {running && (
            <Button size="xs" variant="outline" colorPalette="red" onClick={() => stop.mutate()}>
              Stop
            </Button>
          )}
        </>
      }
    >
      {job.isLoading && (
        <Center py={8}>
          <Spinner />
        </Center>
      )}
      {job.error && <Text color="red.fg">Failed to load job.</Text>}
      <Box>
        <TurnTranscript events={events} job={job.data} />
      </Box>
      <StickyComposer>
        <Composer
          placeholder={running ? "Wait for current turn..." : "Type a followup..."}
          disabled={running}
          onSend={async (prompt) => {
            await followup.mutateAsync({ prompt });
          }}
        />
      </StickyComposer>
    </Shell>
  );
}
