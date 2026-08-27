import React, { useState } from "react";
import { linkEvidence, registerEvidence, requestEvidenceExtraction } from "../api/endpoints";
import { color, radius, size, space } from "../styles/tokens";

const ACCEPTED = new Set(["application/pdf", "text/plain", "text/markdown"]);

async function checksum(file) {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export default function EvidenceUploader({ proposalId, justificationId, expertId, onRegistered }) {
  const [file, setFile] = useState(null);
  const [storageKey, setStorageKey] = useState("");
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (!file || !expertId || !storageKey.trim()) return;
    if (!ACCEPTED.has(file.type)) {
      setStatus({ kind: "error", text: "Chỉ nhận PDF, TXT hoặc Markdown." });
      return;
    }
    setBusy(true);
    setStatus(null);
    try {
      const document = await registerEvidence({
        proposal_id: proposalId || null,
        uploaded_by_expert_id: expertId,
        original_filename: file.name,
        mime_type: file.type,
        object_storage_key: storageKey.trim(),
        sha256_checksum: await checksum(file),
        file_size_bytes: file.size,
      });
      const extraction = await requestEvidenceExtraction(document.id);
      if (justificationId) await linkEvidence({ document_id: document.id, feature_justification_id: justificationId });
      setStatus({ kind: "ok", text: `Đã đăng ký ${file.name}; trạng thái trích xuất: ${extraction.extraction_status}.` });
      setFile(null);
      setStorageKey("");
      onRegistered?.({ ...document, extraction_status: extraction.extraction_status });
    } catch (error) {
      setStatus({ kind: "error", text: error?.message || "Không thể đăng ký bằng chứng." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} style={S.form}>
      <div style={S.notice}>Backend hiện nhận metadata của file đã có trên storage. Nhập storage key sau khi file đã được đưa lên kho dùng chung.</div>
      <label style={S.label}>File báo cáo<input type="file" accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label>
      <label style={S.label}>Object storage key<input style={S.input} value={storageKey} onChange={(event) => setStorageKey(event.target.value)} placeholder="reports/project/report.pdf" /></label>
      <button type="submit" style={S.button} disabled={busy || !file || !expertId || !storageKey.trim()}>{busy ? "Đang đăng ký…" : "Đăng ký và trích xuất"}</button>
      {status && <div role="status" style={status.kind === "error" ? S.error : S.success}>{status.text}</div>}
    </form>
  );
}

const S = {
  form: { display: "grid", gap: space(3) },
  label: { display: "grid", gap: 5, color: color.ink, fontSize: size.tiny, fontWeight: 700 },
  input: { minWidth: 0, padding: "9px 10px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontFamily: "inherit" },
  notice: { padding: space(3), borderRadius: radius.sm, background: color.warnSoft, color: color.body, fontSize: size.tiny, lineHeight: 1.5 },
  button: { justifySelf: "start", border: 0, borderRadius: radius.sm, padding: "10px 14px", background: color.accent, color: "#fff", fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  success: { padding: space(3), borderRadius: radius.sm, background: color.okSoft, color: color.ok, fontSize: size.tiny },
  error: { padding: space(3), borderRadius: radius.sm, background: color.dangerSoft, color: color.danger, fontSize: size.tiny },
};
