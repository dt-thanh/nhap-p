import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// CRM có backend riêng, độc lập với service `api` của nhap-p (AbsorptionForecast).
// Mặc định trỏ về localhost:8100 khi chạy ngoài Docker; đổi qua VITE_API_PROXY_TARGET khi cần.
const target = process.env.VITE_API_PROXY_TARGET || "http://localhost:8100";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5174, // khác port với frontend/ (5173) để chạy song song
    watch: { usePolling: true },
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        rewrite: (path: string) => path.replace(/^\/api/, ""),
      },
      "/ws": { target, ws: true, changeOrigin: true },
      "/health": { target, changeOrigin: true },
    },
  },
});