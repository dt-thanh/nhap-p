// frontend/src/components/Brand.jsx
// Logo AbsorbIQ dùng chung (logomark "con trỏ radar" + chữ). Không cần file ảnh.
// Dùng ở topbar, trang login, landing... để nhận diện đồng nhất.
import React from "react";
import { color, font } from "../styles/tokens";

export function Logomark({ size = 32, light = false }) {
  return (
    <span
      style={{
        width: size, height: size, borderRadius: size * 0.28, position: "relative",
        display: "inline-block", flex: "none",
        background: light ? "rgba(255,255,255,.2)" : `linear-gradient(135deg, ${color.accent}, ${color.accentHover})`,
      }}
      aria-hidden="true"
    >
      {/* vòng radar khuyết + tâm */}
      <span style={{
        position: "absolute", inset: size * 0.25,
        border: `${Math.max(2, size * 0.08)}px solid #fff`, borderRadius: "50%",
        borderRightColor: "transparent", transform: "rotate(-45deg)",
      }} />
      <span style={{
        position: "absolute", left: "50%", top: "50%", width: size * 0.16, height: size * 0.16,
        background: "#fff", borderRadius: "50%", transform: "translate(-50%,-50%)",
      }} />
    </span>
  );
}

export function Wordmark({ size = 19, withAI = false, light = false }) {
  return (
    <span style={{ fontFamily: font.display, fontSize: size, fontWeight: 700, letterSpacing: "-.02em", color: light ? "#fff" : color.ink }}>
      Absorb<b style={{ color: light ? "#fff" : color.accent }}>IQ</b>{withAI ? " AI" : ""}
    </span>
  );
}

export default function Brand({ size = 32, wordSize = 19, withAI = false, light = false }) {
  return (
    <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <Logomark size={size} light={light} />
      <Wordmark size={wordSize} withAI={withAI} light={light} />
    </span>
  );
}
