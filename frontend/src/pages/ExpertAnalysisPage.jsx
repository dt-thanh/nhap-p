import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  createAhpProposal,
  createGovernanceProposal,
  getCurrentFeatureRubric,
  getRankingV3Coverage,
  getExpertAnalysisOverview,
  linkEvidence,
  listEvidenceDocuments,
  listFeatureDefinitions,
  listGovernanceProposals,
  listGovernanceReviews,
  registerExpert,
  requestEvidenceExtraction,
  saveAhpProposalDraft,
  submitGovernanceProposal,
  upsertJustification,
  uploadEvidenceDocument,
} from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { useProjectScope } from "../hooks/useProjectScope";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/States";
import { color, radius, shadow, size, space } from "../styles/tokens";

function ready(document) {
  return document?.lifecycle_status === "active"
    && document?.extraction_status === "succeeded"
    && Number(document?.chunk_count || 0) > 0
    && Number(document?.embedded_chunk_count || 0) > 0;
}

export default function ExpertAnalysisPage() {
  const scope = useProjectScope();
  const projectId = scope.currentProject?.project_id || "";
  const [expert, setExpert] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const documents = useAsync(() => projectId ? listEvidenceDocuments({ projectId }) : Promise.resolve([]), [projectId]);
  const proposals = useAsync(() => projectId ? listGovernanceProposals({ project_id: projectId }) : Promise.resolve([]), [projectId]);
  const overview = useAsync(() => projectId ? getExpertAnalysisOverview(projectId) : Promise.resolve(null), [projectId]);
  const coverage = useAsync(() => projectId ? getRankingV3Coverage(projectId) : Promise.resolve(null), [projectId]);
  const ownDrafts = useMemo(() => (proposals.data || []).filter((proposal) => proposal.status !== "published"), [proposals.data]);
  const ahpProposal = useMemo(
    () => (proposals.data || []).find((proposal) => proposal.proposal_type === "ahp_ranking_proposal" && proposal.status === "draft"),
    [proposals.data],
  );
  const reviewKey = useMemo(() => ownDrafts.map((proposal) => `${proposal.id}:${proposal.status}`).join(","), [ownDrafts]);
  const reviews = useAsync(async () => Object.fromEntries(await Promise.all(
    ownDrafts.filter((proposal) => proposal.status === "approved" || proposal.status === "rejected")
      .map(async (proposal) => [proposal.id, await listGovernanceReviews(proposal.id)]),
  )), [reviewKey]);

  async function start() {
    if (starting) return;
    setStarting(true);
    setStartError("");
    try {
      setExpert(await registerExpert());
    } catch (error) {
      const status = error?.status;
      const message = status === 403
        ? "Tài khoản chưa đủ quyền mở workspace Phân tích cố vấn."
        : status === 404
          ? "Workspace Phân tích cố vấn chưa sẵn sàng trên máy chủ. Vui lòng thử lại sau."
          : status >= 500
            ? "Máy chủ chưa thể mở workspace. Vui lòng thử lại sau."
            : error?.name === "TypeError" || status === 0 || status === undefined
              ? "Không thể kết nối máy chủ. Kiểm tra kết nối rồi thử lại."
              : error?.message || "Không thể mở workspace. Vui lòng thử lại.";
      setStartError(message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <main style={S.page}>
      <header style={S.header}>
        <h1 style={S.h1}>Phân tích cố vấn</h1>
        <p style={S.muted}>Soạn đánh giá của bạn trong phạm vi dự án được cấp. Gửi duyệt không công bố cấu hình hoặc chạy lại ranking.</p>
      </header>
      {!expert ? (
        <section style={S.card}>
          <h2 style={S.h2}>Bắt đầu phân tích</h2>
          <p style={S.muted}>Hồ sơ tác giả được suy ra từ danh tính OIDC đã xác thực.</p>
          <button type="button" style={S.primary} onClick={start} disabled={starting} aria-busy={starting}>
            {starting ? "Đang mở workspace…" : "Mở workspace"}
          </button>
          {startError && <div role="alert" style={S.error}>{startError} <button type="button" style={S.retry} onClick={start} disabled={starting}>Thử lại</button></div>}
        </section>
      ) : (
        <>
          {!projectId && <EmptyState title="Chưa chọn dự án" hint="Chọn một dự án trong phạm vi được cấp để bắt đầu." />}
          {projectId && <>
            <Documents documents={documents} overview={overview} projectId={projectId} areaId={scope.currentArea?.area_id} />
            <CoveragePanel coverage={coverage} />
            <Qualitative documents={documents.data || []} projectId={projectId} areas={scope.areas} areaId={scope.currentArea?.area_id} proposals={ownDrafts} onChanged={proposals.reload} />
            <AhpProposal projectId={projectId} proposal={ahpProposal} documents={documents.data || []} onChanged={proposals.reload} />
            <PackageSummary ahpProposal={ahpProposal} proposals={ownDrafts} reviews={reviews.data || {}} />
            <Drafts proposals={ownDrafts} reviews={reviews.data || {}} onChanged={proposals.reload} />
          </>}
        </>
      )}
    </main>
  );
}

function Documents({ documents, overview, projectId, areaId }) {
  const [file, setFile] = useState(null);
  const [message, setMessage] = useState("");
  async function upload(event) {
    event.preventDefault();
    if (!file) return;
    setMessage("Đang tải và yêu cầu trích xuất…");
    try {
      const document = await uploadEvidenceDocument(file, { projectId, areaId });
      if (!document.reused) await requestEvidenceExtraction(document.id);
      await documents.reload();
      await overview.reload();
      setMessage("Đã lưu báo cáo. Trạng thái sẵn sàng chỉ xuất hiện khi chunk và embedding đã hoàn tất.");
    } catch (error) { setMessage(error?.message || "Không thể tải báo cáo."); }
  }
  return <section style={S.card}>
    <h2 style={S.h2}>Báo cáo tư vấn chi tiết</h2>
    <form onSubmit={upload} style={S.row}><input type="file" aria-label="Chọn báo cáo" accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown" onChange={(event) => setFile(event.target.files?.[0] || null)} /><button style={S.primary} disabled={!file}>Tải lên</button></form>
    {message && <p role="status" style={S.muted}>{message}</p>}
    {overview.loading ? <Skeleton height={48} /> : <p style={S.muted}>Sẵn sàng: {overview.data?.documents_ready || 0}; đang xử lý: {overview.data?.documents_processing || 0}; lỗi: {overview.data?.documents_failed || 0}.</p>}
    {documents.loading && <Skeleton height={120} />}
    {documents.error && <ErrorState error={documents.error} onRetry={documents.reload} compact />}
    {!documents.loading && !documents.error && <ul style={S.list}>{(documents.data || []).map((document) => <li key={document.id}><strong>{document.original_filename}</strong> — {ready(document) ? "Sẵn sàng" : document.extraction_status === "succeeded" ? "Đã trích xuất, chưa sẵn sàng (thiếu chunk/embedding)" : document.extraction_status}</li>)}</ul>}
  </section>;
}

function CoveragePanel({ coverage }) {
  if (coverage.loading) return <section style={S.card}><h2 style={S.h2}>Độ phủ Ranking V3</h2><Skeleton height={72} /></section>;
  if (coverage.error) return <section style={S.card} role="status"><h2 style={S.h2}>Độ phủ Ranking V3</h2><p style={S.error}>Chưa đọc được trạng thái độ phủ. Bạn vẫn có thể soạn đánh giá và gửi theo các cổng kiểm tra của máy chủ.</p></section>;
  const data = coverage.data;
  if (!data) return null;
  const renderScope = (label, scope) => <div key={label} style={S.coverageScope}>
    <strong>{label}</strong>
    <span>{scope.published || 0}/{scope.required || 0} đã công bố</span>
    {(scope.missing || scope.blocked || scope.expired) > 0 && <span style={S.warning}>Thiếu {scope.missing || 0} · chặn {scope.blocked || 0} · hết hạn {scope.expired || 0}</span>}
  </div>;
  return <section style={S.card} aria-label="Độ phủ Ranking V3">
    <div style={S.row}><h2 style={S.h2}>Độ phủ Ranking V3</h2><span style={S.muted}>Config v{data.config_version ?? "—"}</span></div>
    <div style={S.coverageGrid}>{renderScope("Dự án", data.project || {})}{renderScope("Thị trường", data.market || {})}</div>
    {!!data.areas?.length && <div style={S.coverageGrid}>{data.areas.map((area) => renderScope(area.name || area.external_id || "Phân khu", area))}</div>}
    {!!data.evidence_blockers?.length && <p style={S.muted}>Nút chặn bằng chứng: {data.evidence_blockers.map((item) => `${item.grain}.${item.feature_key} (${item.reason})`).join("; ")}</p>}
  </section>;
}

function Qualitative({ documents, projectId, areas, areaId, proposals, onChanged }) {
  const features = useAsync(() => listFeatureDefinitions(), []);
  const [featureId, setFeatureId] = useState("");
  const [scopeType, setScopeType] = useState(areaId ? "area" : "project");
  const [selectedAreaId, setSelectedAreaId] = useState(areaId || "");
  const [rubric, setRubric] = useState(null);
  const [band, setBand] = useState("");
  const [rationale, setRationale] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [citation, setCitation] = useState("");
  const [effectiveAt, setEffectiveAt] = useState(() => new Date().toISOString().slice(0, 16));
  const [expiresAt, setExpiresAt] = useState(() => new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 16));
  const [message, setMessage] = useState("");
  // Only the value-authorable V3 grains are valid for this UI.  Unit-level
  // scores are produced by the ranking engine, while the legal-status feature
  // is a gate and must never be authored as a value assertion.
  const choices = (features.data || []).filter((feature) => GRAINS.includes(feature.grain) && feature.feature_key !== "project_legal_status");
  const usableEvidence = documents.filter(ready);
  async function choose(id) {
    const feature = choices.find((item) => item.id === id);
    setFeatureId(id); setBand(""); setRubric(null);
    if (feature) {
      setScopeType(feature.grain === "area" ? "area" : feature.grain);
      if (feature.grain !== "area") setSelectedAreaId("");
      setRubric(await getCurrentFeatureRubric(id));
    }
  }
  async function save(event) {
    event.preventDefault();
    const feature = choices.find((item) => item.id === featureId);
    if (!feature || !rubric || !band || !rationale.trim() || !documentId || (scopeType === "area" && !selectedAreaId)) {
      setMessage("Vui lòng chọn tiêu chí, phạm vi, band rubric, bằng chứng và diễn giải.");
      return;
    }
    if (scopeType === "market" && !citation.trim()) {
      setMessage("Đánh giá Thị trường cần nguồn trích dẫn.");
      return;
    }
    try {
      const proposal = proposals.find((item) => item.assertion_kind === "value" && item.status === "draft" && item.scope_type === scopeType && (scopeType !== "area" || item.area_id === selectedAreaId))
        || await createGovernanceProposal({ project_id: projectId, assertion_kind: "value", scope_type: scopeType, area_id: scopeType === "area" ? selectedAreaId : null });
      const justification = await upsertJustification(proposal.id, {
        feature_definition_id: featureId, assertion_kind: "value", rationale, methodology: "Đánh giá cố vấn theo rubric công bố.",
        evidence_summary: "Bằng chứng dự án đã trích xuất và có embedding.", expected_effect: "context_dependent", confidence: "medium", limitations: "Cần rà soát lại khi bằng chứng mới xuất hiện.",
        effective_at: new Date(effectiveAt).toISOString(), expires_at: expiresAt ? new Date(expiresAt).toISOString() : null, external_source_citation: scopeType === "market" ? citation : null, rubric_id: rubric.id, rubric_band_value: band,
      });
      await linkEvidence({ document_id: documentId, feature_justification_id: justification.id });
      setMessage("Đã lưu đánh giá và gắn bằng chứng vào bản nháp của bạn.");
      await onChanged();
    } catch (error) { setMessage(error?.message || "Không thể lưu đánh giá."); }
  }
  return <section style={S.card}>
    <h2 style={S.h2}>Rubrics — Đánh giá định tính</h2>
    <p style={S.muted}>Mỗi đánh giá rubric vẫn là một đề xuất định tính độc lập, được CEO phê duyệt riêng với đề xuất trọng số AHP.</p>
    {!usableEvidence.length && <p style={S.muted}>Cần ít nhất một evidence ở trạng thái sẵn sàng trước khi lưu đánh giá.</p>}
    <form onSubmit={save} style={S.form}>
      <label>Tiêu chí<select value={featureId} onChange={(event) => choose(event.target.value)}><option value="">Chọn tiêu chí</option>{choices.map((feature) => <option key={feature.id} value={feature.id}>{feature.name} · {feature.grain}</option>)}</select></label>
      <label>Phạm vi<select value={scopeType} onChange={(event) => setScopeType(event.target.value)} disabled={!featureId}><option value="project">Dự án</option><option value="market">Thị trường</option><option value="area">Phân khu</option></select></label>
      {scopeType === "area" && <label>Phân khu<select value={selectedAreaId} onChange={(event) => setSelectedAreaId(event.target.value)}><option value="">Chọn phân khu</option>{areas.map((area) => <option key={area.area_id || area.id} value={area.area_id || area.id}>{area.area_name || area.name}</option>)}</select></label>}
      <label>Band rubric<select value={band} onChange={(event) => setBand(event.target.value)} disabled={!rubric}><option value="">Chọn band</option>{(rubric?.bands || []).map((item) => <option key={item.id} value={item.band_value}>{item.label} ({item.band_value})</option>)}</select></label>
      <label>Bằng chứng<select value={documentId} onChange={(event) => setDocumentId(event.target.value)}><option value="">Chọn tài liệu sẵn sàng</option>{usableEvidence.map((document) => <option key={document.id} value={document.id}>{document.original_filename}</option>)}</select></label>
      {scopeType === "market" && <label>Nguồn trích dẫn<input value={citation} onChange={(event) => setCitation(event.target.value)} placeholder="URL hoặc tài liệu nguồn" /></label>}
      <label>Hiệu lực từ<input type="datetime-local" value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} /></label>
      <label>Hết hạn lúc<input type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} /></label>
      <label>Diễn giải<textarea value={rationale} onChange={(event) => setRationale(event.target.value)} /></label>
      <button style={S.primary} disabled={!usableEvidence.length}>Lưu bản nháp</button>
    </form>
    {message && <p role="status" style={S.muted}>{message}</p>}
  </section>;
}

const GRAINS = ["market", "project", "area"];
const GRAIN_LABELS = { market: "Thị trường", project: "Dự án", area: "Phân khu" };
const ALL_GRAINS = [...GRAINS, "unit"];
const TOTAL_GRAIN_BASIS_POINTS = 10_000; // 100.00% — avoids float drift in UI validation.
const DEFAULT_GRAIN_ALLOCATION = { market: 2500, project: 2500, area: 2500, unit: 2500 };

function allocationFromSnapshot(snapshot) {
  const weights = snapshot?.hierarchical_weights?.grain_weights;
  if (!weights || typeof weights !== "object") return { allocation: DEFAULT_GRAIN_ALLOCATION, source: "fallback" };
  const allocation = Object.fromEntries(ALL_GRAINS.map((grain) => [
    grain,
    Math.round(Number(weights[grain]?.weight) * TOTAL_GRAIN_BASIS_POINTS),
  ]));
  const valid = ALL_GRAINS.every((grain) => Number.isInteger(allocation[grain]) && allocation[grain] >= 0)
    && Object.values(allocation).reduce((sum, value) => sum + value, 0) === TOTAL_GRAIN_BASIS_POINTS;
  return valid ? { allocation, source: "saved" } : { allocation: DEFAULT_GRAIN_ALLOCATION, source: "fallback" };
}

function formatPercent(basisPoints) {
  return (basisPoints / 100).toLocaleString("vi-VN", { maximumFractionDigits: 2 });
}

function inputPercent(basisPoints) {
  return basisPoints / 100;
}

function distributeBasisPoints(total, grains, current) {
  const currentTotal = grains.reduce((sum, grain) => sum + current[grain], 0);
  const raw = grains.map((grain) => ({
    grain,
    value: currentTotal > 0 ? (total * current[grain]) / currentTotal : total / grains.length,
  }));
  const result = Object.fromEntries(raw.map(({ grain, value }) => [grain, Math.floor(value)]));
  let remainder = total - Object.values(result).reduce((sum, value) => sum + value, 0);
  raw.sort((left, right) => (right.value - Math.floor(right.value)) - (left.value - Math.floor(left.value))
    || left.grain.localeCompare(right.grain));
  for (let index = 0; remainder > 0; index = (index + 1) % raw.length, remainder -= 1) result[raw[index].grain] += 1;
  return result;
}

function apiFailureMessage(error, fallback) {
  const detail = error?.body?.detail || error?.data?.detail || {};
  const code = detail?.error_code || error?.error_code;
  const message = detail?.message || error?.message || fallback;
  return code ? `${code}: ${message}` : message;
}

function AhpProposal({ projectId, proposal, documents, onChanged }) {
  const features = useAsync(() => listFeatureDefinitions(), []);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState({}); // feature_key -> raw importance
  const [rationales, setRationales] = useState({}); // feature_key -> optional authored explanation
  const [grainWeight, setGrainWeight] = useState(DEFAULT_GRAIN_ALLOCATION);
  const [lockedGrains, setLockedGrains] = useState({ market: false, project: false, area: false, unit: false });
  const [allocationSource, setAllocationSource] = useState("fallback");
  const [allocationMessage, setAllocationMessage] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [blockers, setBlockers] = useState([]);
  const [confirmingSubmit, setConfirmingSubmit] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const ctaRef = useRef(null);
  // `GET /governance/proposals` (the list this component's own `proposal` prop
  // comes from) never carries `proposed_hierarchy_snapshot` — only the draft-save
  // response below does. Without this local cache, the submit CTA can never
  // leave `disabled` after a real save+reload cycle: this was the actual root
  // cause of "the confirm dialog never appears" (the button was silently inert,
  // not a CSS/z-index/portal problem — see pipeline_status.md).
  const [localSnapshot, setLocalSnapshot] = useState(null);
  const blockersRef = useRef(null);
  const byGrain = useMemo(() => {
    const out = { market: [], project: [], area: [] };
    for (const feature of features.data || []) {
      if (feature.feature_key !== "project_legal_status" && out[feature.grain]) out[feature.grain].push(feature);
    }
    return out;
  }, [features.data]);

  useEffect(() => {
    if (!proposal?.id) return;
    setLocalSnapshot(null); // switching proposal identity — any cached snapshot belonged to a different draft
    const initial = allocationFromSnapshot(proposal.proposed_hierarchy_snapshot);
    setGrainWeight(initial.allocation);
    setAllocationSource(initial.source);
    setLockedGrains({ market: false, project: false, area: false, unit: false });
    setAllocationMessage("");
    setBlockers([]);
  }, [proposal?.id]);

  useEffect(() => {
    if (blockers.length && blockersRef.current) {
      blockersRef.current.scrollIntoView?.({ behavior: "smooth", block: "center" });
      blockersRef.current.focus();
    }
  }, [blockers]);

  async function start() {
    setCreating(true);
    setMessage("");
    try { await createAhpProposal(projectId); await onChanged(); }
    catch (error) { setMessage(error?.message || "Không thể tạo đề xuất AHP."); }
    finally { setCreating(false); }
  }

  function toggle(featureKey) {
    setSelected((prev) => {
      const next = { ...prev };
      if (featureKey in next) delete next[featureKey]; else next[featureKey] = 1;
      return next;
    });
  }

  function normalizedGrainBlock(grain) {
    const chosen = byGrain[grain].filter((feature) => Number(selected[feature.feature_key]) > 0);
    const total = chosen.reduce((sum, feature) => sum + (Number(selected[feature.feature_key]) || 0), 0);
    if (!chosen.length || total <= 0) return null;
    return Object.fromEntries(chosen.map((feature) => [
      feature.feature_key,
      {
        weight: (Number(selected[feature.feature_key]) || 0) / total,
        direction: feature.direction,
        missing_value_policy: feature.missing_policy === "zero" ? "neutral" : feature.missing_policy,
        ...(rationales[feature.feature_key]?.trim() ? { rationale: rationales[feature.feature_key].trim() } : {}),
      },
    ]));
  }

  function updateGrainWeight(grain, rawValue) {
    const percentage = Number(rawValue);
    if (!Number.isFinite(percentage) || percentage < 0 || percentage > 100) {
      setAllocationMessage("Mỗi trọng số phải nằm trong khoảng từ 0% đến 100%.");
      return;
    }
    const edited = Math.round(percentage * 100);
    const unlockedOthers = ALL_GRAINS.filter((key) => key !== grain && !lockedGrains[key]);
    if (!unlockedOthers.length) {
      setAllocationMessage("Không còn khối nào chưa khóa để cân bằng lại. Hãy mở khóa ít nhất một khối khác trước khi sửa.");
      return;
    }
    const lockedTotal = ALL_GRAINS
      .filter((key) => key !== grain && lockedGrains[key])
      .reduce((sum, key) => sum + grainWeight[key], 0);
    const remaining = TOTAL_GRAIN_BASIS_POINTS - edited - lockedTotal;
    if (remaining < 0) {
      setAllocationMessage(`Giá trị này vượt phần còn lại sau các khối đã khóa. Tối đa: ${formatPercent(TOTAL_GRAIN_BASIS_POINTS - lockedTotal)}%.`);
      return;
    }
    setGrainWeight((current) => ({
      ...current,
      [grain]: edited,
      ...distributeBasisPoints(remaining, unlockedOthers, current),
    }));
    setAllocationMessage("");
  }

  async function saveDraft() {
    setMessage("");
    const total = ALL_GRAINS.reduce((sum, grain) => sum + grainWeight[grain], 0);
    if (total !== TOTAL_GRAIN_BASIS_POINTS) {
      setAllocationMessage(`Tổng trọng số phải bằng 100%. Hiện tại: ${formatPercent(total)}%.`);
      return;
    }
    const blocks = { market: normalizedGrainBlock("market"), project: normalizedGrainBlock("project"), area: normalizedGrainBlock("area") };
    const emptyRequiredGrains = GRAINS.filter((grain) => !blocks[grain] && grainWeight[grain] > 0);
    if (emptyRequiredGrains.length) {
      const labels = emptyRequiredGrains.map((grain) => GRAIN_LABELS[grain]).join(", ");
      setMessage(`Cần chọn ít nhất một tiêu chí có trọng số dương cho khối: ${labels}. Khối có trọng số grain bằng 0 có thể để trống.`);
      return;
    }
    const grain_weights = Object.fromEntries(
      ALL_GRAINS.map((grain) => [grain, { weight: grainWeight[grain] / TOTAL_GRAIN_BASIS_POINTS, missing_value_policy: "neutral" }]),
    );
    try {
      const saved = await saveAhpProposalDraft(proposal.id, {
        mode: "direct",
        direct_hierarchical_weights: {
          grain_weights,
          ...Object.fromEntries(GRAINS.map((grain) => [grain, blocks[grain] || {}])),
        },
      });
      setLocalSnapshot(saved.proposed_hierarchy_snapshot || null);
      setAllocationSource(allocationFromSnapshot(saved.proposed_hierarchy_snapshot).source);
      setBlockers([]);
      setMessage("Đã lưu bản nháp hierarchy — chưa thay đổi ranking.");
      await onChanged();
    } catch (error) { setMessage(apiFailureMessage(error, "Không thể lưu bản nháp.")); }
  }

  function submitBlockers() {
    const reasons = [];
    if (!allocationValid) reasons.push(`Tổng trọng số cấp phân hạng phải bằng 100%. Hiện tại: ${formatPercent(grainTotal)}%.`);
    if (!hasSnapshot) reasons.push("Cần lưu bản nháp hierarchy hợp lệ (Lưu bản nháp) trước khi gửi — chọn ít nhất một tiêu chí có trọng số dương cho mỗi khối đang có trọng số grain lớn hơn 0%.");
    if (!hasReadyProjectEvidence) reasons.push("Cần ít nhất một tài liệu sẵn sàng trong dự án trước khi gửi — tải lên ở mục Báo cáo tư vấn chi tiết.");
    return reasons;
  }

  function reviewAndSubmit() {
    const reasons = submitBlockers();
    if (reasons.length) { setBlockers(reasons); return; }
    setBlockers([]);
    setSubmitError("");
    setConfirmingSubmit(true);
  }

  async function submit() {
    if (submitting) return; // guards against a double-fire while the request is in flight
    setSubmitting(true);
    setSubmitError("");
    try {
      await submitGovernanceProposal(proposal.id);
      setConfirmingSubmit(false);
      await onChanged();
      setMessage("Đã gửi CEO duyệt đề xuất ranking.");
    } catch (error) {
      // Kept open (not `setConfirmingSubmit(false)`): the error belongs in the
      // dialog the user is already looking at, not a message hidden behind it.
      setSubmitError(apiFailureMessage(error, "Không thể gửi đề xuất."));
    } finally {
      setSubmitting(false);
    }
  }

  if (!proposal) {
    return <section style={S.card}>
      <h2 style={S.h2}>Đề xuất trọng số AHP</h2>
      <p style={S.muted}>Bản nháp — chưa thay đổi ranking. Bắt đầu một đề xuất mới để chọn tiêu chí và trọng số cho dự án này.</p>
      <button type="button" style={S.primary} onClick={start} disabled={creating}>{creating ? "Đang tạo…" : "Tạo đề xuất AHP"}</button>
      {message && <p role="status" style={S.muted}>{message}</p>}
    </section>;
  }

  const effectiveSnapshot = localSnapshot ?? proposal.proposed_hierarchy_snapshot ?? null;
  const hasSnapshot = Boolean(effectiveSnapshot?.hierarchical_weights);
  const usableEvidence = documents.filter(ready);
  const hasReadyProjectEvidence = usableEvidence.length > 0;
  const grainTotal = ALL_GRAINS.reduce((sum, grain) => sum + grainWeight[grain], 0);
  const allocationValid = grainTotal === TOTAL_GRAIN_BASIS_POINTS;
  return <section style={S.card}>
    <h2 style={S.h2}>Đề xuất trọng số AHP</h2>
    <p style={S.muted}>Bản nháp — chưa thay đổi ranking. Chỉ tiêu chí đã đăng ký (active) trong danh mục mới được chọn.</p>
    {features.loading && <Skeleton height={80} />}
    {GRAINS.map((grain) => <fieldset key={grain} style={S.fieldset}>
      <legend>{GRAIN_LABELS[grain]}</legend>
      {(byGrain[grain] || []).map((feature) => <label key={feature.id} style={S.row}>
        <input type="checkbox" checked={feature.feature_key in selected} onChange={() => toggle(feature.feature_key)} />
        {feature.name}
        {feature.feature_key in selected && <input type="number" min="0" step="0.1" style={S.narrow} value={selected[feature.feature_key]} onChange={(event) => setSelected((prev) => ({ ...prev, [feature.feature_key]: event.target.value }))} aria-label={`Mức độ quan trọng của ${feature.name}`} />}
        {feature.feature_key in selected && (grain === "market" || grain === "area") && <textarea
          aria-label={`Giải thích cho ${feature.name}`}
          placeholder="Tại sao chọn trọng số này? (tùy chọn)"
          maxLength={500}
          rows={2}
          style={S.rationale}
          value={rationales[feature.feature_key] || ""}
          onChange={(event) => setRationales((prev) => ({ ...prev, [feature.feature_key]: event.target.value }))}
        />}
      </label>)}
      {!(byGrain[grain] || []).length && !features.loading && <p style={S.muted}>Chưa có tiêu chí active nào đã đăng ký cho khối này.</p>}
    </fieldset>)}
    <fieldset style={S.fieldset}>
      <legend>Trọng số cấp phân hạng (grain)</legend>
      <p style={S.muted}>Trọng số tổng phải bằng 100%. Điểm ranking v3 được tổng hợp từ: Thị trường × trọng số Thị trường + Dự án × trọng số Dự án + Phân khu × trọng số Phân khu + Điểm Căn hộ hiện có × trọng số Căn hộ.</p>
      <p style={S.muted}>Căn hộ dùng điểm ranking nền hiện có; không có tiêu chí con. Dự án chỉ có thể có trọng số lớn hơn 0% khi đã chọn tiêu chí Dự án hợp lệ. Trọng số tiêu chí bên trong từng khối cũng phải bằng 100% của khối đó.</p>
      <p style={S.muted}>{allocationSource === "saved" ? "Đã nạp phân bổ từ bản nháp AHP đã lưu." : "Chưa có phân bổ hierarchy khả dụng trong hợp đồng Advisor; dùng mặc định cân bằng 25% cho mỗi khối."}</p>
      {ALL_GRAINS.map((grain) => <div key={grain} style={S.row}>
        <label style={S.row}>{GRAIN_LABELS[grain] || "Căn hộ"}
          <input type="number" min="0" max="100" step="0.01" inputMode="decimal" style={S.narrow} value={inputPercent(grainWeight[grain])} disabled={lockedGrains[grain]} onChange={(event) => updateGrainWeight(grain, event.target.value)} aria-label={`Trọng số ${GRAIN_LABELS[grain] || "Căn hộ"} (%)`} />
          <span aria-hidden="true">%</span>
        </label>
        <button type="button" style={S.secondary} aria-pressed={lockedGrains[grain]} aria-label={`${lockedGrains[grain] ? "Mở khóa" : "Khóa"} trọng số ${GRAIN_LABELS[grain] || "Căn hộ"}`} onClick={() => setLockedGrains((current) => ({ ...current, [grain]: !current[grain] }))}>{lockedGrains[grain] ? "Mở khóa" : "Khóa"}</button>
      </div>)}
      <p role="status" aria-live="polite" style={allocationValid ? S.muted : S.error}>{allocationValid ? "Tổng trọng số: 100%" : `Tổng trọng số phải bằng 100%. Hiện tại: ${formatPercent(grainTotal)}%.`}</p>
      {allocationMessage && <p role="alert" style={S.error}>{allocationMessage}</p>}
    </fieldset>
    {!hasReadyProjectEvidence && <p role="alert" style={S.error}>Vui lòng upload bằng chứng trong tab Báo cáo tư vấn chi tiết.</p>}
    {hasReadyProjectEvidence && <p style={S.muted}>{usableEvidence.length} tài liệu sẵn sàng trong dự án sẽ được tự động liên kết khi gửi.</p>}
    <div style={S.row}>
      <button type="button" style={S.secondary} onClick={saveDraft} disabled={!allocationValid}>Lưu bản nháp</button>
      <button type="button" ref={ctaRef} style={S.primary} onClick={reviewAndSubmit}>Xem lại và gửi CEO duyệt</button>
    </div>
    {blockers.length > 0 && <div ref={blockersRef} role="alert" tabIndex={-1} style={S.error}>
      <strong>Không thể gửi:</strong>
      <ul style={S.list}>{blockers.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    </div>}
    {confirmingSubmit && <ConfirmSubmitModal
      byGrain={byGrain}
      selected={selected}
      rationales={rationales}
      grainWeight={grainWeight}
      evidenceCount={usableEvidence.length}
      applicationStatus={proposal.ahp_application_status}
      submitting={submitting}
      submitError={submitError}
      onCancel={() => { setConfirmingSubmit(false); setSubmitError(""); }}
      onConfirm={submit}
    />}
    {message && <p role="status" style={S.muted}>{message}</p>}
  </section>;
}

function ConfirmSubmitModal({
  byGrain, selected, rationales, grainWeight, evidenceCount, applicationStatus, submitting, submitError, onCancel, onConfirm,
}) {
  const dialogRef = useRef(null);
  const headingId = "ahp-confirm-submit-heading";
  useEffect(() => {
    const previousFocus = document.activeElement;
    dialogRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key === "Escape") { event.preventDefault(); onCancel(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previousFocus && previousFocus !== document.body) previousFocus.focus?.();
    };
  }, [onCancel]);
  const selectedCriteria = GRAINS.flatMap((grain) => (byGrain[grain] || [])
    .filter((feature) => feature.feature_key in selected)
    .map((feature) => ({ grain, ...feature })));
  return <div style={S.modalBackdrop} data-testid="ahp-confirm-backdrop" onClick={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={headingId} tabIndex={-1} style={S.modal}>
      <div style={S.modalHeader}>
        <h3 id={headingId} style={S.h3}>Xác nhận gửi CEO duyệt</h3>
        <button type="button" style={S.modalClose} aria-label="Đóng hộp thoại xác nhận" onClick={onCancel} disabled={submitting}>×</button>
      </div>
      <p style={S.muted}>Trọng số, giải thích và {evidenceCount} tài liệu sẵn sàng của dự án sẽ được đóng băng khi gửi.</p>
      {applicationStatus && AHP_STATUS_LABEL[applicationStatus] && (
        <p style={S.muted}>Trạng thái áp dụng hiện tại: {AHP_STATUS_LABEL[applicationStatus]}</p>
      )}
      <section aria-label="Trọng số cấp phân hạng">
        <h4 style={S.h4}>Trọng số cấp phân hạng (grain)</h4>
        <ul style={S.list}>{ALL_GRAINS.map((grain) => <li key={grain}>{GRAIN_LABELS[grain] || "Căn hộ"}: {formatPercent(grainWeight[grain])}%</li>)}</ul>
      </section>
      <section aria-label="Tiêu chí đã chọn">
        <h4 style={S.h4}>Tiêu chí đã chọn</h4>
        {!selectedCriteria.length
          ? <p style={S.muted}>Không có tiêu chí nào được chọn.</p>
          : <ul style={S.list}>{selectedCriteria.map((feature) => <li key={feature.feature_key}>
            <strong>{GRAIN_LABELS[feature.grain]} — {feature.name}</strong>: mức độ quan trọng {selected[feature.feature_key]}
            <br /><span style={S.muted}>Giải thích: {rationales[feature.feature_key]?.trim() || "Không có giải thích"}</span>
          </li>)}</ul>}
      </section>
      {submitError && <p role="alert" style={S.error}>{submitError}</p>}
      <div style={S.row}>
        <button type="button" style={S.secondary} onClick={onCancel} disabled={submitting}>Quay lại</button>
        <button type="button" style={S.primary} onClick={onConfirm} disabled={submitting} aria-busy={submitting}>{submitting ? "Đang gửi…" : "Xác nhận gửi CEO duyệt"}</button>
      </div>
    </section>
  </div>;
}

function PackageSummary({ ahpProposal, proposals, reviews }) {
  const qualitative = proposals.filter((proposal) => proposal.assertion_kind === "value");
  const latestAhpReview = ahpProposal ? (reviews[ahpProposal.id] || []).at(-1) : null;
  return <section style={S.card} aria-label="Tổng hợp và Gửi">
    <h2 style={S.h2}>Tổng hợp và Gửi</h2>
    <p style={S.muted}>Rubrics và AHP nằm trên cùng một trang để soạn thảo, nhưng vẫn giữ hồ sơ, bằng chứng và quyết định CEO riêng biệt.</p>
    <ul style={S.list}>
      <li>Đánh giá rubric: {qualitative.length ? `${qualitative.length} đề xuất riêng` : "chưa có"}.</li>
      <li>Đề xuất AHP: {ahpProposal ? proposalStatusLabel(ahpProposal, latestAhpReview) : "chưa tạo"}.</li>
      <li>Để gửi AHP, lưu hierarchy hợp lệ và có ít nhất một tài liệu sẵn sàng trong Báo cáo tư vấn chi tiết.</li>
    </ul>
  </section>;
}

const AHP_STATUS_LABEL = {
  pending: "CEO đã duyệt — đang áp dụng cấu hình ranking",
  awaiting_prior_run: "CEO đã duyệt — đang chờ phiên ranking trước hoàn tất",
  queued: "CEO đã duyệt — đang chờ xếp hạng",
  running: "Đang cập nhật điểm ranking",
  applied: "Ranking đã cập nhật",
  failed: "Áp dụng cấu hình thất bại — liên hệ quản trị viên",
};

function proposalStatusLabel(proposal, latestReview) {
  if (proposal.proposal_type === "ahp_ranking_proposal") {
    if (proposal.status === "draft") return "Bản nháp — chưa thay đổi ranking";
    if (proposal.status === "submitted" || proposal.status === "under_review") return "Đã gửi CEO duyệt";
    if (proposal.status === "rejected") return `CEO từ chối — ${latestReview?.comment || ""}`;
    if (proposal.status === "approved" || proposal.status === "published") {
      if (AHP_STATUS_LABEL[proposal.ahp_application_status]) return AHP_STATUS_LABEL[proposal.ahp_application_status];
      return "Đang cập nhật điểm ranking";
    }
  }
  return { draft: "Bản nháp", submitted: "Đã gửi", approved: "Đã phê duyệt", rejected: "Cần chỉnh sửa" }[proposal.status] || proposal.status;
}

function Drafts({ proposals, reviews, onChanged }) {
  const [message, setMessage] = useState("");
  async function submit(id) {
    try { await submitGovernanceProposal(id); await onChanged(); setMessage("Đã gửi CEO duyệt. Việc gửi không công bố hoặc chạy lại ranking."); }
    catch (error) { setMessage(error?.message || "Không thể gửi đề xuất."); }
  }
  return <section style={S.card}><h2 style={S.h2}>Bản nháp của tôi</h2>
    {!proposals.length ? <EmptyState compact title="Chưa có bản nháp" /> : <ul style={S.list}>{proposals.map((proposal) => {
      const latest = (reviews[proposal.id] || []).at(-1);
      const title = proposal.proposal_type === "ahp_ranking_proposal" ? "Đề xuất trọng số AHP" : proposal.assertion_kind === "value" ? "Đánh giá định tính" : "Đề xuất";
      return <li key={proposal.id}><strong>{title}</strong> — {proposalStatusLabel(proposal, latest)} {proposal.status === "draft" && proposal.proposal_type !== "ahp_ranking_proposal" && <button style={S.secondary} type="button" onClick={() => submit(proposal.id)}>Gửi CEO duyệt</button>}</li>;
    })}</ul>}
    {message && <p role="status" style={S.muted}>{message}</p>}
  </section>;
}

const S = {
  page: { display: "grid", gap: space(4) }, header: { display: "grid", gap: space(1) }, h1: { margin: 0, fontSize: size.title, color: color.ink }, h2: { margin: 0, fontSize: size.large }, h3: { margin: 0, fontSize: size.h2 }, h4: { margin: 0, fontSize: size.body }, muted: { margin: 0, color: color.muted, fontSize: size.small, lineHeight: 1.55 }, card: { background: color.surface, borderRadius: radius.lg, boxShadow: shadow.card, padding: space(5), display: "grid", gap: space(3) }, primary: { border: 0, borderRadius: radius.md, padding: `${space(2)}px ${space(3)}px`, background: color.accent, color: "#fff", cursor: "pointer", width: "fit-content" }, secondary: { marginLeft: space(2), border: 0, borderRadius: radius.sm, padding: `${space(1)}px ${space(2)}px`, cursor: "pointer" }, retry: { marginLeft: space(2), border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(1)}px ${space(2)}px`, background: color.surface, cursor: "pointer" }, error: { color: color.danger, fontSize: size.small }, row: { display: "flex", gap: space(2), flexWrap: "wrap", alignItems: "center" }, form: { display: "grid", gap: space(3), maxWidth: 640 }, list: { margin: 0, paddingLeft: space(4), display: "grid", gap: space(2) },
  fieldset: { border: `1px solid ${color.borderStrong}`, borderRadius: radius.md, padding: space(3), display: "grid", gap: space(2) }, narrow: { width: 64, marginLeft: space(2) }, rationale: { width: "100%", minWidth: 220, maxWidth: 560, resize: "vertical" }, coverageGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: space(2) }, coverageScope: { display: "grid", gap: space(1), padding: space(2), border: `1px solid ${color.border}`, borderRadius: radius.sm },
  // Fixed-position backdrop + centered dialog, same working pattern already
  // established in OverviewPage.jsx's AttentionReportModal — position:fixed +
  // high z-index escapes this page's own layout/overflow, so the dialog is
  // never clipped by a page container.
  modalBackdrop: { position: "fixed", inset: 0, zIndex: 1000, display: "grid", placeItems: "center", padding: space(3), background: "rgba(15, 23, 42, .46)" },
  modal: { width: "min(560px, 100%)", maxHeight: "calc(100vh - 24px)", overflowY: "auto", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.lg, boxShadow: "0 24px 70px rgba(15, 23, 42, .24)", padding: space(5), display: "grid", gap: space(3), outline: "none" },
  modalHeader: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: space(3) },
  modalClose: { display: "grid", placeItems: "center", width: 36, height: 36, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, color: color.ink, cursor: "pointer", fontFamily: "inherit", fontSize: 24, lineHeight: 1 },
};
