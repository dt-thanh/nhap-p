/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Sidebar navy đậm
        navy: {
          900: "#0A1526",
          800: "#0D1C31",
          700: "#12263F",
          600: "#1A324F",
        },
        // Brand teal — nút chính trong app, active state
        teal: {
          DEFAULT: "#17976E",
          600: "#12855F",
          700: "#0F6E52",
          soft: "#E4F3EE",
        },
        // Gold — branding + login
        gold: {
          DEFAULT: "#C6982F",
          light: "#E0B354",
          dark: "#A87F22",
          soft: "#FBF3DE",
        },
        // Text / bề mặt (light content)
        ink: { DEFAULT: "#18233A", muted: "#64708A", faint: "#98A2B3" },
        surface: { page: "#F6F7F9", card: "#FFFFFF" },
        line: { DEFAULT: "#E9ECF1", strong: "#DCE0E7" },
        // Trạng thái
        status: {
          green: "#1F9D57", greenbg: "#E6F5EC",
          amber: "#C98A16", amberbg: "#FBF0DA",
          gray: "#6B7688", graybg: "#EEF0F3",
          blue: "#2A72C4", bluebg: "#E5EFFA",
          red: "#D0483C", redbg: "#FBEAE8",
        },
      },
      fontFamily: {
        display: ["'Playfair Display'", "Georgia", "serif"],
        body: ["Inter", "system-ui", "sans-serif"],
      },
      borderRadius: { card: "14px" },
      boxShadow: {
        card: "0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)",
        panel: "0 8px 24px rgba(16,24,40,0.12)",
      },
    },
  },
  plugins: [],
};
