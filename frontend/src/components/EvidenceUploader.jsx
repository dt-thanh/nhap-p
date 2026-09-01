import React, { useState } from "react";
import { linkEvidence, requestEvidenceExtraction, uploadEvidenceDocument } from "../api/endpoints";
import { color, radius, size, space } from "../styles/tokens";

const ACCEPTED = new Set(["application/pdf", "text/plain", "text/markdown"]);

export default function EvidenceUploader({ proposalId, justificationId, expertId, onRegistered }) {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (!file || !expertId) return;
    if (!ACCEPTED.has(file.type)) {
      setStatus({ kind: "error", text: "Chỉ nhận PDF, TXT hoặc Markdown." });
      return;
    }
    setBusy(true);
    setStatus(null);
    try {
      const document = await uploadEvidenceDocument(file, { proposalId: proposalId || null });
      const extraction = document.reused
        ? { extraction_status: document.extraction_status }
        : await requestEvidenceExtraction(document.id);
      if (justificationId) await linkEvidence({ document_id: document.id, feature_justification_id: justificationId });
      setStatus({
        kind: "ok",
        text: document.reused
          ? `${file.name} trùng nội dung với một file đã có — dùng lại bản ghi cũ.`
          : `Đã tải lên ${file.name}; trạng thái trích xuất: ${extraction.extraction_status}.`,
      });
      setFile(null);
      onRegistered?.({ ...document, extraction_status: extraction.extraction_status });
    } catch (error) {
      setStatus({ kind: "error", text: error?.message || "Không thể tải lên bằng chứng." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} style={S.form}>
      <label style={S.label}>
        File báo cáo (PDF/TXT/Markdown)
        <input
          type="file"
          accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
          onChange={(event) => setFile(event.target.files?.[0] || null)}
        />
      </label>
      <button type="submit" style={S.button} disabled={busy || !file || !expertId}>
        {busy ? "Đang tải lên…" : "Tải lên và trích xuất"}
      </button>
      {status && <div role="status" style={status.kind === "error" ? S.error : S.success}>{status.text}</div>}
    </form>
  );
}

const S = {
  form: { display: "grid", gap: space(3) },
  label: { display: "grid", gap: 5, color: color.ink, fontSize: size.tiny, fontWeight: 700 },
  button: { justifySelf: "start", border: 0, borderRadius: radius.sm, padding: "10px 14px", background: color.accent, color: "#fff", fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  success: { padding: space(3), borderRadius: radius.sm, background: color.okSoft, color: color.ok, fontSize: size.tiny },
  error: { padding: space(3), borderRadius: radius.sm, background: color.dangerSoft, color: color.danger, fontSize: size.tiny },
};
