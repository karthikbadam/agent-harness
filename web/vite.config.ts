import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// We don't run vite's dev server. The harness serves web/dist/ at :8765, so
// dev = `npm run build -- --watch` (rebuild on change) and the browser hits
// http://localhost:8765/. scripts/dev.sh wires both backend + watch build.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
