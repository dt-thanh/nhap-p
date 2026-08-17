// frontend/src/hooks/useBreakpoint.js
// ---------------------------------------------------------------------------
// Phát hiện độ rộng màn hình để giao diện tự thích ứng.
//
// VÌ SAO CẦN HOOK NÀY: chúng ta dùng inline style (style={{...}}) trong React,
// mà inline style KHÔNG viết được media query (@media) như CSS thường.
// Nên phải hỏi JavaScript "màn đang rộng bao nhiêu?" rồi chọn giá trị tương ứng.
//
// Ngưỡng chọn theo thực tế thiết bị của nhóm:
//   mobile  : < 640px   (điện thoại)
//   tablet  : 640–1024  (tablet, laptop nhỏ chia đôi màn)
//   laptop  : 1024–1440 (laptop 13–15 inch — phổ biến nhất trong nhóm)
//   desktop : > 1440    (màn rời lớn)
// ---------------------------------------------------------------------------
import { useState, useEffect } from "react";

function detect(w) {
  if (w < 640) return "mobile";
  if (w < 1024) return "tablet";
  if (w < 1440) return "laptop";
  return "desktop";
}

export function useBreakpoint() {
  const [bp, setBp] = useState(() =>
    typeof window === "undefined" ? "desktop" : detect(window.innerWidth)
  );

  useEffect(() => {
    let frame = null;
    const onResize = () => {
      // Gộp nhiều sự kiện resize vào 1 lần cập nhật -> không giật khi kéo cửa sổ
      if (frame) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setBp(detect(window.innerWidth)));
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return {
    bp,
    isMobile: bp === "mobile",
    isTablet: bp === "tablet",
    isNarrow: bp === "mobile" || bp === "tablet",
    isDesktop: bp === "desktop",
  };
}

/** Chọn giá trị theo breakpoint, có giá trị mặc định.
 *  Ví dụ: pick(bp, { mobile: 16, tablet: 24, laptop: 28, desktop: 32 }) */
export function pick(bp, map) {
  return map[bp] ?? map.desktop ?? Object.values(map)[0];
}