import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Cấu hình test RIÊNG, không chung với vite.config.js (dev server) — vitest
// không cần proxy /api, và trộn hai cấu hình sẽ khiến build dev vô tình phụ
// thuộc vào các gói chỉ dùng khi test (jsdom, @testing-library/*).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
  },
});
