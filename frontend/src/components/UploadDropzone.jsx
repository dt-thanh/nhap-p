// frontend/src/components/UploadDropzone.jsx
// SRS §5.2: "kéo-thả file, hiển thị thanh tiến độ upload, chặn file sai định
// dạng phía client". Giới hạn 20 MB theo §2.4.
//
// Chặn ở client chỉ để phản hồi nhanh cho người dùng — backend VẪN phải kiểm
// lại, vì kiểm tra phía client không phải là bảo mật.
import React, { useRef, useState } from "react";
import { color, size, radius, space } from "../styles/tokens";

const MAX_MB = 20;
const ACCEPT = [".xlsx", ".xls", ".csv"];

export default function UploadDropzone({ onFile, busy }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState(null);

  function validate(file) {
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!ACCEPT.includes(ext)) {
      return `Chỉ nhận file ${ACCEPT.join(", ")} — file bạn chọn là ${ext}`;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      return `File nặng ${(file.size / 1048576).toFixed(1)} MB, vượt giới hạn ${MAX_MB} MB`;
    }
    return null;
  }

  function handleFile(file) {
    if (!file) return;
    const err = validate(file);
    if (err) { setLocalError(err); return; }
    setLocalError(null);
    onFile(file);
  }

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFile(e.dataTransfer.files?.[0]);
        }}
        onClick={() => !busy && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
        style={{
          ...S.zone,
          ...(dragging ? S.zoneDrag : null),
          ...(busy ? S.zoneBusy : null),
        }}
      >
        <div style={S.icon} aria-hidden="true">⬆</div>
        <div style={S.title}>
          {busy ? "Đang tải lên…" : "Kéo file vào đây hoặc bấm để chọn"}
        </div>
        <div style={S.hint}>
          Excel hoặc CSV theo template · tối đa {MAX_MB} MB
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT.join(",")}
          style={{ display: "none" }}
          onChange={(e) => {
            handleFile(e.target.files?.[0]);
            e.target.value = ""; // cho phép chọn lại cùng file
          }}
        />
      </div>

      {localError && <div style={S.err}>{localError}</div>}
    </div>
  );
}

const S = {
  zone: {
    border: `1.5px dashed ${color.borderStrong}`,
    borderRadius: radius.md,
    background: color.surface,
    padding: `${space(10)}px ${space(6)}px`,
    textAlign: "center",
    cursor: "pointer",
    transition: "border-color .15s, background .15s",
  },
  zoneDrag: { borderColor: color.accent, background: color.accentSoft },
  zoneBusy: { opacity: 0.6, cursor: "progress" },
  icon: { fontSize: 22, color: color.muted, marginBottom: space(2) },
  title: { fontSize: size.body, fontWeight: 600, color: color.ink },
  hint: { fontSize: size.tiny, color: color.muted, marginTop: space(1) },
  err: {
    marginTop: space(3), background: color.dangerSoft,
    border: `1px solid ${color.danger}33`, color: color.danger,
    borderRadius: radius.sm, padding: `${space(2)}px ${space(3)}px`, fontSize: size.small,
  },
};
