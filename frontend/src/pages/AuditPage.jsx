// frontend/src/pages/AuditPage.jsx
// S07 — Nhật ký. Trước đây chạy trên MarketPrototypePage (mô phỏng, không nối
// API thật). Không có endpoint "nhật ký nghiệp vụ" nào phù hợp với phiên
// người dùng của trang này: `ops.py`/`reconciliation.py` tồn tại nhưng dùng
// mặt phẳng xác thực RIÊNG (X-Ops-Token / X-API-Key CRM), không phải token
// dashboard của phiên này — và việc này không được phép dựng một console vận
// hành mới chỉ vì một vài endpoint tồn tại. Hiện trạng thái CHƯA CÓ tường
// minh, không giả lập dữ liệu.
import React from "react";
import { EmptyState } from "../components/ui/States";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { color, size, radius, shadow, space, font } from "../styles/tokens";

export default function AuditPage() {
  return (
    <>
      <GlobalKeyframes />
      <div style={S.head}>
        <h1 style={S.h1}>Nhật ký</h1>
        <p style={S.sub}>Lịch sử thao tác và sự kiện vận hành.</p>
      </div>
      <div style={S.card}>
        <EmptyState
          icon="calendar"
          title="Nhật ký chưa khả dụng"
          hint="Chưa có endpoint nhật ký nghiệp vụ nào cho phiên người dùng hiện tại — các bảng vận hành/đối soát đang tồn tại dùng một mặt phẳng xác thực khác (công cụ vận hành nội bộ), không phải phiên đăng nhập này."
        />
      </div>
    </>
  );
}

const S = {
  head: { marginBottom: space(5) },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  sub: { fontSize: size.small, color: color.muted, margin: "4px 0 0" },
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, padding: space(6) },
};
