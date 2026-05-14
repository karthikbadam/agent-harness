import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: "injectManifest",
      registerType: "autoUpdate",
      srcDir: "src",
      filename: "sw.ts",
      injectRegister: false,
      manifest: {
        name: "agent-harness",
        short_name: "harness",
        start_url: "/",
        display: "standalone",
        background_color: "#0b0b0c",
        theme_color: "#0b0b0c",
        icons: [
          { src: "/icons/192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/512.png", sizes: "512x512", type: "image/png" },
          {
            src: "/icons/512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      injectManifest: {
        injectionPoint: undefined,
      },
    }),
  ],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: true, secure: false },
      "/healthz": { target: "http://127.0.0.1:8765", changeOrigin: true, secure: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
