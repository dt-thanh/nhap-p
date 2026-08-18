import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Trong compose, proxy trỏ tới service `api` qua compose network.
// Ngoài Docker, mặc định về localhost:8000.
const target = process.env.VITE_API_PROXY_TARGET || "http://localhost:8000";

// Phase F/G: cổng GHI của FE là Mini CRM, KHÔNG BAO GIỜ backend trực tiếp — xem
// `docs/crm/phase_a_domain_freeze.md` §A7.3 ("FE ghi vào đâu? → Mini CRM").
// Proxy cùng-nguồn (same-origin) thay vì gọi thẳng `localhost:8100` từ trình
// duyệt: tránh phải bật CORS ở Mini CRM (§A0 "Module này KHÔNG import gì từ
// src/" — không nới lỏng biên đó chỉ để FE gọi được), và giữ đúng "dùng cổng
// gateway đã có" mà đặc tả yêu cầu khi không gọi trực tiếp an toàn được.
// `rewrite` cắt tiền tố: route Mini CRM nằm ở gốc ("/projects", "/areas", ...),
// không có tiền tố "/api/v1" như backend.
const minicrmTarget = process.env.VITE_MINICRM_PROXY_TARGET || "http://localhost:8100";

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
      "/minicrm-api": {
        target: minicrmTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/minicrm-api/, ""),
      },
    },
  },
});
