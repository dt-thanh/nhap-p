import React, { useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { color, radius, size, space } from "../styles/tokens";

export default function LogoutButton() {
  const { user, logout } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  if (!user) return null;

  async function handleLogout() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await logout();
    } catch {
      setPending(false);
      setError("Đăng xuất thất bại. Vui lòng thử lại.");
    }
  }

  return (
    <div style={S.wrap}>
      {error && <span role="alert" style={S.error}>{error}</span>}
      <button
        type="button"
        onClick={handleLogout}
        disabled={pending}
        aria-busy={pending}
        style={{ ...S.button, ...(pending ? S.buttonPending : null) }}
      >
        {pending ? "Đang đăng xuất…" : "Đăng xuất"}
      </button>
    </div>
  );
}

const S = {
  wrap: { display: "flex", alignItems: "center", gap: space(2), minWidth: 0 },
  button: {
    minHeight: 38,
    padding: `0 ${space(3)}px`,
    border: `1px solid ${color.borderStrong}`,
    borderRadius: radius.sm,
    background: color.surface,
    color: color.body,
    cursor: "pointer",
    fontSize: size.tiny,
    fontWeight: 700,
    whiteSpace: "nowrap",
    transition: "background 140ms ease, border-color 140ms ease, opacity 140ms ease",
  },
  buttonPending: { cursor: "wait", opacity: 0.65 },
  error: { maxWidth: 240, color: color.danger, fontSize: size.tiny, lineHeight: 1.3 },
};
