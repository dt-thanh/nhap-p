/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Sidebar — navy đậm, gần đen, kiểu enterprise SaaS (Stripe/Linear)
        navy: {
          900: "#08142C",
          800: "#0C1B38",
          700: "#122447",
          600: "#1A3260",
        },
        // Primary — xanh dương, dùng cho hành động chính/active state.
        // Teal giữ lại CHỈ để biểu thị "sold/success" (xem status.sold), không
        // còn là màu thương hiệu bao trùm UI nữa.
        primary: {
          DEFAULT: "#2563EB",
          600: "#1D4ED8",
          700: "#1E40AF",
          soft: "#EFF4FF",
          subtle: "#F5F8FF",
        },
        // Màu thương hiệu nguyên bản của khu vực nội dung CRM.
        teal: {
          DEFAULT: "#17976E",
          600: "#12855F",
          700: "#0F6E52",
          soft: "#E4F3EE",
        },
        // Gold — chỉ còn dùng cho trang Login/branding, không dùng trong CRM UI.
        gold: {
          DEFAULT: "#C6982F",
          light: "#E0B354",
          dark: "#A87F22",
          soft: "#FBF3DE",
        },
        // Text / bề mặt (light content)
        ink: { DEFAULT: "#18233A", muted: "#64708A", faint: "#98A2B3" },
        surface: { page: "#F6F7F9", card: "#FFFFFF", raised: "#FCFCFD" },
        line: { DEFAULT: "#E9ECF1", strong: "#DCE0E7" },
        // Trạng thái — semantic, KHÔNG dùng làm màu thương hiệu chung.
        status: {
          green: "#1F9D57", greenbg: "#E6F5EC",
          amber: "#C98A16", amberbg: "#FBF0DA",
          indigo: "#6366F1", indigobg: "#E0E7FF",
          gray: "#6B7688", graybg: "#EEF0F3",
          blue: "#2A72C4", bluebg: "#E5EFFA",
          red: "#D0483C", redbg: "#FBEAE8",
        },
      },
      fontFamily: {
        // Manrope tạo nhịp tiêu đề rõ và hiện đại; Inter giữ phần dữ liệu/bảng
        // dễ đọc ở mật độ cao.
        display: ["Manrope", "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        body: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "'Segoe UI'", "sans-serif"],
      },
      borderRadius: { card: "14px" },
      boxShadow: {
        card: "0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)",
        panel: "0 8px 24px rgba(16,24,40,0.12)",
        float: "0 12px 32px rgba(37,99,235,0.14)",
      },
    },
  },
  plugins: [],
};
