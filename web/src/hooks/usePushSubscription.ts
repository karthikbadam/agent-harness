/**
 * Push subscription lifecycle.
 *
 * iOS quirks (handled by `eligibility`):
 *  - Notifications only work when launched from a home-screen install
 *    (`display-mode: standalone`).
 *  - Notification permission must be granted before subscribing.
 *
 * Why a Zustand store: the SW registration + push subscription should survive
 * cross-page navigation without re-running the whole dance.
 */
import { useCallback, useEffect, useState } from "react";

import { pushApi } from "../api/push";

export type Eligibility =
  | { ok: true }
  | { ok: false; reason: "no-sw" | "no-push" | "not-standalone" | "denied" };

export function getEligibility(): Eligibility {
  if (typeof window === "undefined") return { ok: false, reason: "no-sw" };
  if (!("serviceWorker" in navigator)) return { ok: false, reason: "no-sw" };
  if (!("PushManager" in window)) return { ok: false, reason: "no-push" };
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    // @ts-expect-error iOS Safari nonstandard
    Boolean(window.navigator?.standalone);
  if (!standalone) return { ok: false, reason: "not-standalone" };
  if (Notification.permission === "denied") return { ok: false, reason: "denied" };
  return { ok: true };
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function bufToB64(buf: ArrayBuffer | null): string {
  if (!buf) return "";
  const arr = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < arr.length; i++) s += String.fromCharCode(arr[i]!);
  return btoa(s);
}

export function usePush() {
  const [eligibility, setEligibility] = useState<Eligibility>(() => getEligibility());
  const [subscribed, setSubscribed] = useState<boolean>(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setEligibility(getEligibility());
    if (!("serviceWorker" in navigator)) return;
    const reg = await navigator.serviceWorker.ready.catch(() => null);
    const sub = await reg?.pushManager?.getSubscription().catch(() => null);
    setSubscribed(Boolean(sub));
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const subscribe = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") throw new Error("Notification permission denied");
      const reg = await navigator.serviceWorker.ready;
      const vapid = await pushApi.vapidKey();
      const key = urlBase64ToUint8Array(vapid.public_key);
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: key.buffer as ArrayBuffer,
      });
      const json = sub.toJSON() as {
        endpoint: string;
        keys?: { p256dh?: string; auth?: string };
      };
      await pushApi.subscribe({
        endpoint: json.endpoint,
        keys: {
          p256dh: json.keys?.p256dh ?? bufToB64(sub.getKey("p256dh")),
          auth: json.keys?.auth ?? bufToB64(sub.getKey("auth")),
        },
        label: "iPhone",
      });
      setSubscribed(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      await refresh();
    }
  }, [refresh]);

  const unsubscribe = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        const ep = sub.endpoint;
        await sub.unsubscribe();
        const remote = await pushApi.list();
        const match = remote.find((r) => r.endpoint === ep);
        if (match) await pushApi.unsubscribe(match.id);
      }
      setSubscribed(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      await refresh();
    }
  }, [refresh]);

  return { eligibility, subscribed, busy, error, subscribe, unsubscribe };
}
