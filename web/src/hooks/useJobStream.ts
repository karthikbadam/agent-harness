/**
 * Opens an EventSource for /api/jobs/<id>/stream, parses events, and writes
 * them into TanStack Query cache at ["job-events", jobId]. Status-changing
 * events also invalidate the jobs list + job detail.
 *
 * Reconnect strategy: on error, close and retry after 1.5s with
 * ?last_event_id=<highest_seq_seen>. The server replays anything we missed.
 */
import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { sseUrl } from "../api/client";
import { jobKey, jobsKey } from "./useJobs";
import type { StreamEvent } from "../types";

const EVENT_TYPES = [
  "tool_use",
  "tool_result",
  "assistant_text",
  "turn_done",
  "job_status",
  "tool_blocked",
] as const;

export const jobEventsKey = (id: string) => ["job-events", id] as const;

export function useJobEvents(jobId: string | undefined) {
  return useQuery<StreamEvent[]>({
    queryKey: jobEventsKey(jobId ?? ""),
    queryFn: () => [],
    enabled: Boolean(jobId),
    staleTime: Infinity,
    initialData: [],
  });
}

export function useJobStream(jobId: string | undefined) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!jobId) return;
    let lastSeq = 0;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const append = (ev: StreamEvent) => {
      qc.setQueryData<StreamEvent[]>(jobEventsKey(jobId), (old = []) => {
        if (old.some((o) => o.seq === ev.seq)) return old;
        const next = [...old, ev];
        next.sort((a, b) => a.seq - b.seq);
        return next;
      });
      if (ev.type === "job_status" || ev.type === "turn_done") {
        qc.invalidateQueries({ queryKey: jobsKey });
        qc.invalidateQueries({ queryKey: jobKey(jobId) });
      }
    };

    const connect = () => {
      const suffix = lastSeq ? `?last_event_id=${lastSeq}` : "";
      const url = sseUrl(`/api/jobs/${jobId}/stream${suffix}`);
      es = new EventSource(url);

      EVENT_TYPES.forEach((type) => {
        es!.addEventListener(type, (e: MessageEvent) => {
          try {
            const ev = JSON.parse(e.data) as StreamEvent;
            lastSeq = Math.max(lastSeq, ev.seq ?? 0);
            append(ev);
          } catch {
            /* ignore parse errors */
          }
        });
      });

      es.onerror = () => {
        es?.close();
        es = null;
        if (cancelled) return;
        reconnectTimer = setTimeout(connect, 1500);
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  }, [jobId, qc]);
}
