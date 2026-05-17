import { useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Box, Button, Center, Heading, HStack, Spinner, Stack, Text } from "@chakra-ui/react";

import { Shell } from "../components/Shell";
import { Composer } from "../components/Composer";
import { JobCard } from "../components/JobCard";
import { parseServerDate } from "../api/dates";
import { useCreateJob, useJobs } from "../hooks/useJobs";
import type { JobOut } from "../types";

export function JobsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const taskFilter = params.get("task_id") ?? "";
  const { data: allJobs, isLoading, error } = useJobs();
  const createJob = useCreateJob();
  const jobs = useMemo(() => {
    if (!allJobs) return undefined;
    if (taskFilter) return allJobs.filter((j) => j.task_id === taskFilter);
    return allJobs;
  }, [allJobs, taskFilter]);
  const groups = useMemo(() => groupByDay(jobs ?? []), [jobs]);

  return (
    <Shell
      title={taskFilter ? `Jobs · task ${taskFilter.slice(0, 6)}` : "Jobs"}
      back="/"
      right={
        taskFilter ? (
          <Button
            size="xs"
            variant="ghost"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete("task_id");
              setParams(next);
            }}
          >
            Clear filter
          </Button>
        ) : undefined
      }
    >
      <Box pb="calc(160px + env(safe-area-inset-bottom))">
        {taskFilter && (
          <HStack mb={3} fontSize="xs" color="fg.muted">
            <Text>Filtered to one task</Text>
          </HStack>
        )}
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
        <Stack gap={6}>
          {groups.map((g) => (
            <Stack key={g.label} gap={2}>
              <Heading
                size="xs"
                color="fg.muted"
                textTransform="uppercase"
                letterSpacing="wider"
                pl={1}
              >
                {g.label}
              </Heading>
              <Stack gap={2}>
                {g.jobs.map((j) => (
                  <JobCard key={j.id} job={j} />
                ))}
              </Stack>
            </Stack>
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

interface DayGroup {
  label: string;
  jobs: JobOut[];
}

function groupByDay(jobs: JobOut[]): DayGroup[] {
  const map = new Map<string, JobOut[]>();
  for (const j of jobs) {
    const d = parseServerDate(j.created_at);
    const key = ymd(d);
    const arr = map.get(key) ?? [];
    arr.push(j);
    map.set(key, arr);
  }
  // Sort group keys descending (newest day first); jobs inside already sorted by API.
  const keys = Array.from(map.keys()).sort((a, b) => b.localeCompare(a));
  return keys.map((k) => ({ label: dayLabel(new Date(k)), jobs: map.get(k)! }));
}

function ymd(d: Date): string {
  // Local-day key (not UTC), so a job at 11pm doesn't get bucketed into tomorrow.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function dayLabel(d: Date): string {
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (ymd(d) === ymd(today)) return "Today";
  if (ymd(d) === ymd(yesterday)) return "Yesterday";
  const sameYear = d.getFullYear() === today.getFullYear();
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
  });
}
