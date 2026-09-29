import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the UI runs on :5173 and forwards API and WebSocket calls to the
// backend on :8000, or with `npm run dev:demo` to the canned demo backend on :8010
// (scripts/ui_demo.py). In production the backend serves the built files itself.
export default defineConfig(({ mode }) => {
  const backend = mode === "demo" ? "127.0.0.1:8010" : "127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": `http://${backend}`,
        "/ws": { target: `ws://${backend}`, ws: true },
      },
    },
  };
});
