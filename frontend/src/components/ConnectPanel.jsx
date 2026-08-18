// frontend/src/components/ConnectPanel.jsx
// Phase F: MVP này không có hệ đăng nhập người dùng (SRS auth thật thuộc MVP
// sau) — nhưng Phase D-14/E đã bật xác thực THẬT ở cả hai hệ (Mini CRM ghi,
// Backend đọc). Đứng giữa hai sự thật đó, bảng này là cách NGẮN NHẤT, TRUNG
// THỰC NHẤT để một người vận hành dùng được các trang scoped hôm nay: dán
// TRỰC TIẾP hai token vai trò (được cấp thủ công, ngoài băng tần) vào bộ nhớ
// trình duyệt (`setAccessToken`/`setMiniCrmToken` — KHÔNG localStorage, SRS
// NFR-S11) — không giả vờ đây là một màn hình đăng nhập thật.
//
// Hai token TÁCH RIÊNG vì hai hệ khác nhau (xem client.js) — dán nhầm một cái
// vào chỗ kia sẽ lộ ra ngay ở khối trạng thái bên dưới (role hiện ra sai/không
// hiện ra), không phải một lỗi 500 khó hiểu.
import React, { useState } from "react";
import { setAccessToken, setMiniCrmToken, isAuthError } from "../api/client";
import { getMePermissions } from "../api/endpoints";
import { color, size, radius, space } from "../styles/tokens";

export default function ConnectPanel({ onConnected }) {
  const [open, setOpen] = useState(false);
  const [backendToken, setBackendToken] = useState("");
  const [minicrmToken, setMinicrmTokenValue] = useState("");
  const [status, setStatus] = useState(null); // { role, project_scope } | null
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function connect(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setAccessToken(backendToken || null);
    setMiniCrmToken(minicrmToken || null);
    try {
      const permissions = await getMePermissions();
      setStatus(permissions);
      onConnected?.(permissions);
      setOpen(false);
    } catch (e2) {
      setStatus(null);
      setError(isAuthError(e2) ? "Token đọc backend sai hoặc thiếu." : e2.message || "Không kết nối được.");
    } finally {
      setBusy(false);
    }
  }

  function disconnect() {
    setAccessToken(null);
    setMiniCrmToken(null);
    setStatus(null);
    setBackendToken("");
    setMinicrmTokenValue("");
  }

  return (
    <div style={S.wrap}>
      <button style={S.trigger} onClick={() => setOpen((v) => !v)} type="button">
        {status ? `${status.role} · ${status.project_scope === "ALL" ? "ALL" : `${status.project_scope.length} dự án`}` : "Kết nối"}
      </button>
      {open && (
        <form style={S.panel} onSubmit={connect}>
          <label style={S.label}>
            Token đọc (Backend)
            <input
              style={S.input}
              type="password"
              value={backendToken}
              onChange={(e) => setBackendToken(e.target.value)}
              placeholder="Authorization: Bearer …"
              autoComplete="off"
            />
          </label>
          <label style={S.label}>
            Token ghi (Mini CRM)
            <input
              style={S.input}
              type="password"
              value={minicrmToken}
              onChange={(e) => setMinicrmTokenValue(e.target.value)}
              placeholder="Authorization: Bearer …"
              autoComplete="off"
            />
          </label>
          {error && <p style={S.error}>{error}</p>}
          <div style={S.actions}>
            <button style={S.button} type="submit" disabled={busy}>
              {busy ? "Đang kiểm tra…" : "Kết nối"}
            </button>
            {status && (
              <button style={{ ...S.button, ...S.buttonGhost }} type="button" onClick={disconnect}>
                Ngắt kết nối
              </button>
            )}
          </div>
        </form>
      )}
    </div>
  );
}

const S = {
  wrap: { position: "relative" },
  trigger: {
    fontSize: size.tiny,
    fontWeight: 600,
    padding: `${space(1)}px ${space(3)}px`,
    borderRadius: radius.pill,
    border: `1px solid ${color.borderStrong}`,
    background: color.surface,
    cursor: "pointer",
  },
  panel: {
    position: "absolute",
    right: 0,
    top: "calc(100% + 8px)",
    background: color.surface,
    border: `1px solid ${color.border}`,
    borderRadius: radius.md,
    padding: space(4),
    width: 280,
    boxShadow: "0 8px 24px rgba(0,0,0,.12)",
    zIndex: 20,
    display: "flex",
    flexDirection: "column",
    gap: space(3),
  },
  label: { display: "flex", flexDirection: "column", gap: 4, fontSize: size.tiny, fontWeight: 600, color: color.ink },
  input: {
    padding: `${space(2)}px`,
    borderRadius: radius.sm,
    border: `1px solid ${color.border}`,
    fontSize: size.small,
  },
  actions: { display: "flex", gap: space(2) },
  button: {
    padding: `${space(2)}px ${space(3)}px`,
    borderRadius: radius.sm,
    border: "none",
    background: color.accent,
    color: "#fff",
    fontSize: size.small,
    fontWeight: 600,
    cursor: "pointer",
  },
  buttonGhost: { background: "transparent", color: color.danger, border: `1px solid ${color.danger}` },
  error: { fontSize: size.tiny, color: color.danger, margin: 0 },
};
