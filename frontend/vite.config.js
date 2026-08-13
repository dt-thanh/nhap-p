import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Trong compose, proxy trỏ tới service `api` qua compose network.
// Ngoài Docker, mặc định về localhost:8000.
const target = process.env.VITE_API_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    watch: { usePolling: true },
    proxy: {
      "/api": { target, changeOrigin: true },
      "/ws": { target, ws: true, changeOrigin: true },
      "/health": { target, changeOrigin: true },
    },
  },
});
