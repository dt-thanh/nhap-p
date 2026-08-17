// frontend/src/components/ValidationErrorPanel.jsx
// SRS §5.2: "bảng lỗi theo dòng/cột, cho phép tải file lỗi dạng CSV".
//
// Nguyên tắc viết thông báo lỗi: nói RÕ dòng nào, cột nào, sai gì — để người
// dùng mở Excel sửa được ngay, không phải đoán.
import React from "react";
import { color, size, radius, shadow, space, font } from "../styles/tokens";

export default function ValidationErrorPanel({ file, errors = [], loading, onClose }) {
  function downloadCsv() {
    const header = "dong,cot,ma_loi,mo_ta\n";
    const rows = errors
      .map((e) => `${e.row_number},${e.column_name},${e.error_code},"${e.message}"`)
      .join("\n");
    // \uFEFF = BOM, để Excel mở tiếng Việt không bị lỗi font
    const blob = new Blob(["\uFEFF" + header + rows], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `loi_${file?.filename || "validate"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div style={S.overlay} onClick={onClose}>
      <aside style={S.panel} onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Chi tiết lỗi">
        <header style={S.head}>
          <div>
            <h2 style={S.title}>Lỗi dữ liệu</h2>
            <p style={S.sub}>{file?.filename}</p>
          </div>
          <button onClick={onClose} style={S.close} aria-label="Đóng">✕</button>
        </header>

        {loading ? (
          <div style={S.state}>Đang tải danh sách lỗi…</div>
        ) : !errors.length ? (
          <div style={S.state}>File này không có lỗi nào.</div>
        ) : (
          <>
            <div style={S.summary}>
              <b>{errors.length} dòng</b> cần sửa. Mở file gốc, sửa đúng số dòng bên dưới rồi nạp lại.
              <button style={S.csv} onClick={downloadCsv}>Tải CSV lỗi</button>
            </div>

            <div style={S.tableWrap}>
              <table style={S.table}>
                <thead>
                  <tr>
                    <th style={{ ...S.th, width: 64 }}>Dòng</th>
                    <th style={S.th}>Cột</th>
                    <th style={S.th}>Mô tả</th>
                  </tr>
                </thead>
                <tbody>
                  {errors.map((e, i) => (
                    <tr key={i}>
                      <td style={{ ...S.td, ...S.rowNum }}>{e.row_number}</td>
                      <td style={{ ...S.td, fontFamily: font.mono, fontSize: size.tiny }}>{e.column_name}</td>
                      <td style={S.td}>{e.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

const S = {
  overlay: {
    position: "fixed", inset: 0, background: "rgba(16,24,32,.32)",
    display: "flex", justifyContent: "flex-end", zIndex: 50,
  },
  panel: {
    width: "min(560px, 100%)", background: color.surface, height: "100%",
    boxShadow: "-8px 0 32px rgba(16,24,32,.14)", display: "flex", flexDirection: "column",
  },
  head: {
    display: "flex", justifyContent: "space-between", alignItems: "flex-start",
    padding: space(5), borderBottom: `1px solid ${color.border}`,
  },
  title: { fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0 },
  sub: { fontSize: size.tiny, color: color.muted, margin: "3px 0 0", fontFamily: font.mono },
  close: {
    background: "transparent", border: "none", fontSize: 16,
    color: color.muted, cursor: "pointer", padding: 4, lineHeight: 1,
  },
  summary: {
    display: "flex", alignItems: "center", gap: space(3), flexWrap: "wrap",
    padding: `${space(4)}px ${space(5)}px`, fontSize: size.small, color: color.body,
    background: color.warnSoft, borderBottom: `1px solid ${color.border}`,
  },
  csv: {
    marginLeft: "auto", background: color.surface, border: `1px solid ${color.borderStrong}`,
    borderRadius: radius.sm, padding: "5px 12px", fontSize: size.tiny,
    cursor: "pointer", fontFamily: "inherit", color: color.body, fontWeight: 600,
  },
  tableWrap: { overflow: "auto", flex: 1 },
  table: { width: "100%", borderCollapse: "collapse" },
  th: {
    textAlign: "left", fontSize: size.tiny, color: color.muted,
    textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600,
    padding: `${space(2)}px ${space(5)}px`, borderBottom: `1px solid ${color.border}`,
    position: "sticky", top: 0, background: color.canvas,
  },
  td: {
    padding: `${space(3)}px ${space(5)}px`, fontSize: size.small,
    borderBottom: `1px solid ${color.border}`, color: color.body, verticalAlign: "top",
  },
  rowNum: { fontFamily: font.mono, fontWeight: 700, color: color.danger, fontSize: size.tiny },
  state: { padding: space(12), textAlign: "center", color: color.muted, fontSize: size.small },
};
