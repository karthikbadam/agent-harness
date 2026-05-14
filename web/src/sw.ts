/// <reference lib="webworker" />
/**
 * Minimal SW for v1. Real push + notificationclick handlers land in step (k).
 * This file exists so vite-plugin-pwa's injectManifest mode has a srcDir target.
 */
declare const self: ServiceWorkerGlobalScope;

self.addEventListener("install", () => {
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

export {};
