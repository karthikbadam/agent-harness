/// <reference lib="webworker" />
/**
 * Push + notificationclick handlers.
 *
 * Payload shape (matches server's PushPayload.to_json):
 *   { title, body, job_id, url, tag }
 *
 * iOS quirk: notifications only fire on standalone-installed PWAs, and the
 * site must show a *visible* notification for every push (no silent pushes,
 * or iOS revokes permission). We always call showNotification().
 */
declare const self: ServiceWorkerGlobalScope;

self.addEventListener("install", () => {
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

interface PushBody {
  title?: string;
  body?: string;
  job_id?: string;
  url?: string;
  tag?: string;
}

self.addEventListener("push", (event) => {
  let data: PushBody = {};
  try {
    data = (event.data?.json() as PushBody) ?? {};
  } catch {
    data = { body: event.data?.text() || "" };
  }
  const title = data.title || "agent-harness";
  const tag = data.tag || (data.job_id ? `job:${data.job_id}` : "agent-harness");
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      tag,
      icon: "/icons/192.png",
      badge: "/icons/badge-72.png",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data as { url?: string } | null)?.url ?? "/";
  event.waitUntil(
    (async () => {
      const clients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const c of clients) {
        try {
          // Navigate the first focusable window to target, then focus it.
          if ("focus" in c) {
            const win = c as WindowClient;
            if (win.url.indexOf(target) === -1 && "navigate" in win) {
              await win.navigate(target);
            }
            return win.focus();
          }
        } catch {
          // ignore and continue
        }
      }
      return self.clients.openWindow(target);
    })()
  );
});

export {};
