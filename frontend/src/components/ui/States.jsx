// frontend/src/components/ui/States.jsx
// Các trạng thái UI dùng chung: Skeleton · Empty · Error (API/mạng).
// Kèm fmt(): hiển thị "N/A" khi thiếu dữ liệu, NHƯNG giữ số 0 thật.
import React from "react";
import { color, size, radius, space } from "../../styles/tokens";
import Icon from "./Icon";

// --- Định dạng giá trị: phân biệt "thiếu" (null/undefined) với 0 thật ---
export function fmt(v, { suffix = "", digits } = {}) {
  if (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) return "N/A";
  let out = v;
  if (typeof v === "number") {
    out = digits != null ? v.toFixed(digits) : v.toLocaleString("vi-VN");
  }
  return `${out}${suffix}`;
}

export function Skeleton({ height = 16, width = "100%", radius: r = radius.sm, style }) {
  return (
    <span
      style={{
        display: "inline-block", height, width, borderRadius: r,
        background: `linear-gradient(90deg, ${color.canvas} 25%, ${color.border} 37%, ${color.canvas} 63%)`,
        backgroundSize: "400% 100%", animation: "aiq-shimmer 1.4s ease infinite", ...style,
      }}
    />
  );
}

export function EmptyState({ title = "Chưa có dữ liệu", hint, icon = "inbox", compact }) {
  return (
    <div style={{ ...S.center, padding: compact ? space(6) : space(12) }}>
      <div style={S.emptyIcon}><Icon name={icon} size={22} color={color.muted} /></div>
      <div style={S.emptyTitle}>{title}</div>
      {hint && <div style={S.emptyHint}>{hint}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, compact }) {
  const isNet = error?.network;
  return (
    <div style={{ ...S.center, padding: compact ? space(6) : space(12) }}>
      <div style={{ ...S.emptyIcon, background: color.dangerSoft }}>
        <Icon name="warning" size={22} color={color.danger} />
      </div>
      <div style={S.emptyTitle}>{isNet ? "Lỗi kết nối mạng" : "Không tải được dữ liệu"}</div>
      <div style={S.emptyHint}>
        {isNet ? "Kiểm tra kết nối rồi thử lại." : (error?.message || "Đã xảy ra lỗi từ máy chủ.")}
      </div>
      {onRetry && <button style={S.retry} onClick={onRetry}>Thử lại</button>}
    </div>
  );
}

/** Bọc một section: tự chọn hiển thị skeleton / error / empty / nội dung. */
export function SectionState({ loading, error, empty, onRetry, skeleton, compact, children }) {
  if (loading) return skeleton || <DefaultSkeleton compact={compact} />;
  if (error) return <ErrorState error={error} onRetry={onRetry} compact={compact} />;
  if (empty) return <EmptyState compact={compact} />;
  return children;
}

function DefaultSkeleton({ compact }) {
  return (
    <div style={{ padding: compact ? space(3) : space(4), display: "grid", gap: space(2) }}>
      <Skeleton height={14} width="40%" />
      <Skeleton height={compact ? 40 : 90} />
    </div>
  );
}

const S = {
  center: { display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", gap: space(2) },
  emptyIcon: { width: 46, height: 46, borderRadius: "50%", background: color.canvas, display: "grid", placeItems: "center" },
  emptyTitle: { fontSize: size.small, fontWeight: 600, color: color.ink },
  emptyHint: { fontSize: size.tiny, color: color.muted, maxWidth: "42ch" },
  retry: {
    marginTop: space(2), background: color.surface, border: `1px solid ${color.borderStrong}`,
    color: color.body, borderRadius: radius.sm, padding: "6px 14px",
    fontSize: size.tiny, fontWeight: 600, cursor: "pointer", fontFamily: "inherit",
  },
};
