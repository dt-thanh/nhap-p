// frontend/src/components/ui/GlobalKeyframes.jsx
// Chèn keyframes dùng cho skeleton shimmer + spin icon.
// Dùng cách này để KHÔNG phải sửa file CSS toàn cục (giữ thay đổi tối thiểu).
import { useEffect } from "react";

const CSS = `
@keyframes aiq-shimmer { 0% { background-position: 100% 0 } 100% { background-position: -100% 0 } }
@keyframes aiq-spin { to { transform: rotate(360deg) } }
`;

export default function GlobalKeyframes() {
  useEffect(() => {
    if (document.getElementById("aiq-keyframes")) return;
    const el = document.createElement("style");
    el.id = "aiq-keyframes";
    el.textContent = CSS;
    document.head.appendChild(el);
  }, []);
  return null;
}
