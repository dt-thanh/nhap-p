// frontend/src/components/FileStatusTable.jsx
// SRS §5.2: "lịch sử upload, trạng thái parse, số dòng OK/lỗi, link mở chi tiết lỗi".
import React from "react";
import { color, size, radius, shadow, space, font } from "../styles/tokens";

const STATUS = {
  pending: { label: "Chờ xử lý", bg: color.canvas,     fg: color.muted },
  parsing: { label: "Đang đọc",  bg: color.accentSoft, fg: color.accent },
  done:    { label: "Hoàn tất",  bg: color.okSoft,     fg: color.ok },
  failed:  { label: "Thất bại",  bg: color.dangerSoft, fg: color.danger },
};

export default function FileStatusTable({ files = [], loading, onViewErrors }) {
  if (loading) return <div style={S.state}>Đang tải lịch sử…</div>;
  if (!files.length) {
    return <div style={S.state}>Chưa có file nào được nạp. Tải lên file đầu tiên để bắt đầu.</div>;
  }

  return (
    <div style={S.card}>
      <table style={S.table}>
        <thead>
          <tr>
            <th style={S.th}>Tên file</th>
            <th style={S.th}>Trạng thái</th>
            <th style={{ ...S.th, textAlign: "right" }}>Dòng hợp lệ</th>
            <th style={{ ...S.th, textAlign: "right" }}>Dòng lỗi</th>
            <th style={S.th}>Nạp lúc</th>
            <th style={S.th} />
          </tr>
        </thead>
        <tbody>
          {files.map((f) => {
            const st = STATUS[f.status] || STATUS.pending;
            return (
              <tr key={f.id}>
                <td style={{ ...S.td, fontWeight: 500, color: color.ink }}>{f.filename}</td>
                <td style={S.td}>
                  <span style={{ ...S.badge, background: st.bg, color: st.fg }}>{st.label}</span>
                </td>
                <td style={{ ...S.td, ...S.num }}>{f.rows_ok ?? "—"}</td>
                <td style={{ ...S.td, ...S.num, color: f.rows_failed ? color.danger : color.muted }}>
                  {f.rows_failed ?? "—"}
                </td>
                <td style={{ ...S.td, color: color.muted }}>{fmt(f.uploaded_at)}</td>
                <td style={{ ...S.td, textAlign: "right" }}>
                  {f.rows_failed > 0 && (
                    <button style={S.link} onClick={() => onViewErrors(f)}>
                      Xem lỗi
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function fmt(t) {
  if (!t) return "—";
  return new Date(t).toLocaleString("vi-VN", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

const S = {
  card: {
    background: color.surface, border: `1px solid ${color.border}`,
    borderRadius: radius.md, boxShadow: shadow, overflow: "hidden",
  },
  table: { width: "100%", borderCollapse: "collapse" },
  th: {
    textAlign: "left", fontSize: size.tiny, color: color.muted,
    textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600,
    padding: `${space(3)}px ${space(4)}px`, borderBottom: `1px solid ${color.border}`,
    background: color.canvas,
  },
  td: {
    padding: `${space(3)}px ${space(4)}px`, fontSize: size.small,
    borderBottom: `1px solid ${color.border}`, color: color.body,
  },
  num: { textAlign: "right", fontVariantNumeric: "tabular-nums", fontFamily: font.mono, fontSize: size.tiny },
  badge: {
    display: "inline-block", fontSize: size.tiny, fontWeight: 600,
    padding: "3px 10px", borderRadius: radius.pill,
  },
  link: {
    background: "transparent", border: "none", color: color.accent,
    fontSize: size.tiny, fontWeight: 600, cursor: "pointer",
    fontFamily: "inherit", textDecoration: "underline", padding: 0,
  },
  state: {
    background: color.surface, border: `1px solid ${color.border}`,
    borderRadius: radius.md, padding: `${space(10)}px`, textAlign: "center",
    color: color.muted, fontSize: size.small,
  },
};
