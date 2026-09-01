import React, { useState } from "react";
import { getAdvisorAnalysisReviewDetail, getAdvisorAnalysisReviewQueue, publishGovernanceProposal, submitGovernanceReview } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/States";
import { color, radius, shadow, size, space } from "../styles/tokens";

const statusText = { approved: "Đã phê duyệt", rejected: "Cần chỉnh sửa" };

export default function AdvisorAnalysisReviewPage() {
  const queue = useAsync(() => getAdvisorAnalysisReviewQueue(), []);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState("");

  async function openDetail(proposalId) {
    try {
      setDetailError("");
      setDetail(await getAdvisorAnalysisReviewDetail(proposalId));
    } catch (error) {
      setDetail(null);
      setDetailError(error?.message || "Không thể mở chi tiết đề xuất.");
    }
  }

  async function reviewed() {
    setDetail(null);
    await queue.reload();
  }

  const items = queue.data?.items || [];
  return <main style={S.page}>
    <header><h1 style={S.h1}>Phê duyệt phân tích cố vấn</h1><p style={S.muted}>Chỉ đề xuất đã gửi duyệt trong phạm vi được cấp được hiển thị. Phê duyệt không công bố hoặc chạy lại ranking.</p></header>
    {queue.loading && <Skeleton height={140} />}
    {queue.error && <ErrorState error={queue.error} onRetry={queue.reload} />}
    {!queue.loading && !queue.error && !items.length && <EmptyState title="Không có đề xuất chờ duyệt" />}
    {items.map((item) => <QueueCard key={item.proposal_id} item={item} onOpen={openDetail} />)}
    {detailError && <p role="alert" style={S.muted}>{detailError}</p>}
    {detail && <ReviewDetail detail={detail} onReviewed={reviewed} onClose={() => setDetail(null)} />}
  </main>;
}

function proposalKindLabel(item) {
  if (item.proposal_type === "ahp_ranking_proposal") return "Đề xuất trọng số AHP";
  return item.assertion_kind === "value" ? "Báo cáo đánh giá định tính" : "Đề xuất trọng số";
}

function QueueCard({ item, onOpen }) {
  return <section style={S.card}>
    <div style={S.row}><h2 style={S.h2}>{proposalKindLabel(item)}</h2>{item.requires_attention && <span style={S.warning}>Cần kiểm tra</span>}</div>
    <p style={S.muted}>Cố vấn thực hiện: Cố vấn · gửi lúc: {new Date(item.submitted_at).toLocaleString("vi-VN")} · {item.evidence_document_count} tài liệu · {item.evidence_ready ? "Bằng chứng sẵn sàng" : "Bằng chứng cần kiểm tra"}</p>
    <button type="button" style={S.secondary} onClick={() => onOpen(item.proposal_id)}>Xem chi tiết</button>
  </section>;
}

function ReviewDetail({ detail, onReviewed, onClose }) {
  const [decision, setDecision] = useState("approved");
  const [comment, setComment] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [message, setMessage] = useState("");
  const [approved, setApproved] = useState(false);
  const [publishing, setPublishing] = useState(false);
  async function review(event) {
    event.preventDefault();
    const label = decision === "approved" ? "phê duyệt" : "từ chối";
    if (!window.confirm(`Xác nhận ${label} đề xuất này? Quyết định sẽ được lưu bất biến.`)) return;
    try {
      await submitGovernanceReview(detail.proposal_id, { decision, comment, evidence_review_acknowledged: decision === "approved" && acknowledged });
      setMessage(`Đã ${statusText[decision].toLowerCase()}; không có cấu hình nào được công bố.`);
      if (decision === "approved") setApproved(true);
      else await onReviewed();
    } catch (error) { setMessage(error?.message || "Không thể ghi nhận quyết định."); }
  }
  async function publish() {
    if (publishing) return;
    if (!window.confirm("Xác nhận công bố value assertion đã được CEO phê duyệt?")) return;
    setPublishing(true);
    try {
      await publishGovernanceProposal(detail.proposal_id);
      setMessage("Đã công bố đánh giá định tính; chưa có ranking run nào được tự động chạy.");
      await onReviewed();
    } catch (error) { setMessage(error?.message || "Không thể công bố đánh giá."); }
    finally { setPublishing(false); }
  }
  const isAhp = detail.proposal_type === "ahp_ranking_proposal";
  return <section style={S.card} aria-label="Chi tiết đề xuất cố vấn">
    <div style={S.row}><h2 style={S.h2}>{isAhp ? "Đề xuất trọng số AHP" : "Chi tiết đề xuất"}</h2><button type="button" style={S.secondary} onClick={onClose}>Đóng</button></div>
    <p style={S.muted}>Cố vấn thực hiện: Cố vấn · gửi lúc: {new Date(detail.submitted_at).toLocaleString("vi-VN")}</p>
    <p style={detail.evidence_ready ? S.ok : S.warning}>{detail.validation}</p>
    {isAhp && detail.ahp_package && <AhpPackageSummary ahpPackage={detail.ahp_package} />}
    {!isAhp && <section><h3 style={S.h3}>Lý do, rubric và giá trị suy ra</h3><ul style={S.list}>{detail.justifications.map((item, index) => <li key={`${item.feature_name}-${index}`}><strong>{item.feature_name}</strong>: {item.rationale}<br /><span style={S.muted}>Phương pháp: {item.methodology}; tin cậy: {item.confidence}; giá trị suy ra: {item.derived_value || "—"}; band rubric: {item.rubric_band_value || "—"}. Giới hạn: {item.limitations}</span></li>)}</ul></section>}
    <section><h3 style={S.h3}>Bằng chứng đã lưu</h3><ul style={S.list}>{detail.evidence_documents.map((document, index) => <li key={`${document.original_filename}-${index}`}><strong>{document.original_filename}</strong> · {document.ready ? "sẵn sàng" : "chưa sẵn sàng"} · {document.extraction_status}<br /><span style={S.muted}>{document.citation_position_note}</span>{document.file_url && <><br /><a href={document.file_url} target="_blank" rel="noreferrer">Mở PDF bằng chứng</a></>}</li>)}</ul></section>
    <form onSubmit={review} style={S.form}>
      <label>Quyết định<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="approved">Phê duyệt</option><option value="rejected">Từ chối</option></select></label>
      <label>{decision === "rejected" ? "Lý do từ chối (bắt buộc)" : "Nhận xét"}<textarea required minLength={8} value={comment} onChange={(event) => setComment(event.target.value)} /></label>
      {decision === "approved" && <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /> Tôi xác nhận đã xem bằng chứng đã lưu trước khi phê duyệt.</label>}
    {!approved && <button type="submit" style={S.primary} disabled={!detail.evidence_ready || (decision === "approved" && !acknowledged)}>Ghi nhận quyết định</button>}
    {approved && <button type="button" style={S.primary} onClick={publish} disabled={publishing}>{publishing ? "Đang công bố…" : "Công bố đánh giá đã duyệt"}</button>}
    </form>
    {message && <p role="status" style={S.muted}>{message}</p>}
  </section>;
}

function AhpPackageSummary({ ahpPackage }) {
  return <section aria-label="Gói đề xuất AHP — chỉ đọc">
    <h3 style={S.h3}>Gói đề xuất AHP (chỉ đọc — không có nút sửa/công bố/chạy lại ở đây)</h3>
    <p style={S.muted}>Chế độ: {ahpPackage.mode === "pairwise" ? "So sánh cặp (AHP)" : "Nhập trực tiếp"} · Đóng băng lúc: {ahpPackage.frozen_at ? new Date(ahpPackage.frozen_at).toLocaleString("vi-VN") : "—"}</p>
    <p style={S.muted}>Cấu hình đang áp dụng hiện tại: v{ahpPackage.current_active_config_version ?? "—"} ({ahpPackage.current_active_config_note || "—"})</p>
    {ahpPackage.levels && <ul style={S.list}>{ahpPackage.levels.map((level) => <li key={level.level}>{level.level}: CR={level.cr} (ngưỡng {level.threshold}) — {level.consistent ? "đạt" : "KHÔNG đạt"}</li>)}</ul>}
    <ul style={S.list}>{["grain_weights", "market", "project", "area"].map((grain) => (
      <li key={grain}><strong>{grain}</strong>
        {!Object.keys(ahpPackage.hierarchical_weights[grain] || {}).length && ": —"}
        <ul style={S.list}>{Object.entries(ahpPackage.hierarchical_weights[grain] || {}).map(([key, spec]) => <li key={key}>
          {key}={Number(spec.weight).toFixed(3)}
          {grain !== "grain_weights" && <p style={S.muted}><strong>Giải thích từ Expert:</strong> {spec.rationale || "Không có giải thích"}</p>}
        </li>)}</ul>
      </li>
    ))}</ul>
  </section>;
}

const S = {
  page: { display: "grid", gap: space(4) }, h1: { margin: 0, fontSize: size.title, color: color.ink }, h2: { margin: 0, fontSize: size.large }, h3: { margin: `0 0 ${space(2)}px`, fontSize: size.body }, muted: { margin: 0, color: color.muted, fontSize: size.small, lineHeight: 1.55 }, ok: { margin: 0, color: "#047857", fontSize: size.small }, warning: { margin: 0, color: "#b45309", fontSize: size.small }, card: { background: color.surface, borderRadius: radius.lg, boxShadow: shadow.card, padding: space(5), display: "grid", gap: space(3) }, form: { display: "grid", gap: space(3) }, list: { margin: 0, paddingLeft: space(4), display: "grid", gap: space(2) }, row: { display: "flex", gap: space(2), alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }, primary: { border: 0, borderRadius: radius.md, padding: `${space(2)}px ${space(3)}px`, background: color.accent, color: "#fff", cursor: "pointer", width: "fit-content" }, secondary: { border: 0, borderRadius: radius.md, padding: `${space(2)}px ${space(3)}px`, background: color.surfaceMuted, cursor: "pointer", width: "fit-content" },
};
