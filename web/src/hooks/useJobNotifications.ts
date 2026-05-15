/**
 * Watches the jobs list (polled every 5s by useJobs) and fires an in-page
 * toast when a job transitions to a terminal status (done/failed/stopped).
 *
 * Also flashes the tab title with a 🔔 prefix until the window regains
 * focus. No service worker, no Push API — works on plain HTTP.
 */
import { useEffect, useRef } from "react";

import { toaster } from "../components/toaster";
import { useJobs } from "./useJobs";

const TERMINAL = new Set(["done", "failed", "stopped"]);
const TYPE_FOR_STATUS: Record<string, "success" | "error" | "warning"> = {
  done: "success",
  failed: "error",
  stopped: "warning",
};

let originalTitle = "";
let flashTimer: ReturnType<typeof setInterval> | null = null;

function startFlash() {
  if (flashTimer) return;
  if (typeof document === "undefined") return;
  originalTitle = originalTitle || document.title;
  let on = false;
  flashTimer = setInterval(() => {
    on = !on;
    document.title = on ? `🔔 ${originalTitle}` : originalTitle;
  }, 800);
}

function stopFlash() {
  if (flashTimer) {
    clearInterval(flashTimer);
    flashTimer = null;
  }
  if (typeof document !== "undefined" && originalTitle) {
    document.title = originalTitle;
  }
}

export function useJobNotifications() {
  const jobs = useJobs();
  const prevStatus = useRef<Map<string, string>>(new Map());
  const initialized = useRef(false);

  useEffect(() => {
    if (!jobs.data) return;
    if (!initialized.current) {
      // Seed without firing toasts on first load.
      for (const j of jobs.data) prevStatus.current.set(j.id, j.status);
      initialized.current = true;
      return;
    }
    for (const j of jobs.data) {
      const prev = prevStatus.current.get(j.id);
      if (prev !== j.status) {
        if (prev && !TERMINAL.has(prev) && TERMINAL.has(j.status)) {
          toaster.create({
            title: `${labelFor(j.status)}: ${j.title || "(untitled)"}`,
            description: descriptionFor(j),
            type: TYPE_FOR_STATUS[j.status] ?? "info",
            duration: 6000,
            action: { label: "Open", onClick: () => goTo(`/jobs/${j.id}`) },
          });
          startFlash();
        }
        prevStatus.current.set(j.id, j.status);
      }
    }
  }, [jobs.data]);

  useEffect(() => {
    const onFocus = () => stopFlash();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);
}

function labelFor(status: string): string {
  if (status === "done") return "Done";
  if (status === "failed") return "Failed";
  if (status === "stopped") return "Stopped";
  return status;
}

function descriptionFor(j: { turns?: { idx: number }[] }): string {
  const n = j.turns?.length ?? 0;
  return `${n} turn${n === 1 ? "" : "s"}`;
}

function goTo(path: string): void {
  // Defer to react-router via plain history; avoids router context in this hook.
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
