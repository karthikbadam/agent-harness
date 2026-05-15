/**
 * Watches the jobs list (polled every 5s by useJobs) and toasts once when a
 * job transitions to a terminal status.
 */
import { useEffect, useRef } from "react";

import { toaster } from "../components/toaster";
import { useJobs } from "./useJobs";

const TERMINAL = new Set(["done", "failed", "stopped"]);

export function useJobNotifications() {
  const jobs = useJobs();
  const prev = useRef<Map<string, string>>(new Map());
  const seeded = useRef(false);

  useEffect(() => {
    if (!jobs.data) return;
    if (!seeded.current) {
      for (const j of jobs.data) prev.current.set(j.id, j.status);
      seeded.current = true;
      return;
    }
    for (const j of jobs.data) {
      const was = prev.current.get(j.id);
      prev.current.set(j.id, j.status);
      if (was && !TERMINAL.has(was) && TERMINAL.has(j.status)) {
        toaster.create({
          title: `${j.status} · ${j.title || "(untitled)"}`,
          type: j.status === "done" ? "success" : "error",
        });
      }
    }
  }, [jobs.data]);
}
