import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Vite 5 blocks requests whose Host header isn't on the allow-list. When
    // we expose this dev server via a Cloudflare Tunnel for remote demos, the
    // browser sends Host: <slug>.trycloudflare.com — without this, Vite
    // returns a "Blocked request" page instead of the SPA.
    allowedHosts: [".trycloudflare.com", ".cfargotunnel.com", "localhost", "127.0.0.1"],
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE_URL ?? "http://localhost:8000",
        changeOrigin: true,
        // FastAPI routes are unprefixed (/health, /analyse/...) — strip /api
        // before forwarding so the frontend can use a consistent /api/* path.
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
