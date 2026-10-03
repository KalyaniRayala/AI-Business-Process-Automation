import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dashboard calls the backend under /api. In development Vite forwards
// those requests to the FastAPI server and strips the prefix, so the browser
// only ever talks to one origin (no CORS changes on the backend).
const backend = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

const proxy = {
  "/api": {
    target: backend,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
