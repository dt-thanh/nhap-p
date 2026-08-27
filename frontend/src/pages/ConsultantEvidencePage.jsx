import React, { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  createGovernanceProposal,
  createRankingConfigDraft,
  getExpert,
  listJustifications,
  listRankingConfigs,
  registerExpert,
  submitGovernanceProposal,
  setGovernanceProposalConfig,
  upsertJustification,
} from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { useProjectScope } from "../hooks/useProjectScope";
import EvidenceUploader from "../components/EvidenceUploader";
import FeatureWeightSlider from "../components/FeatureWeightSlider";
import ChunkViewer from "../components/ChunkViewer";
import { ErrorState, EmptyState } from "../components/ui/States";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

export default function ConsultantEvidencePage() {
  const { consultantId = "new" } = useParams();
  const scope = useProjectScope();
  const configs = useAsync(() => listRankingConfigs(), []);
  const expertQuery = useAsync(
    () => consultantId === "new" ? Promise.resolve(null) : getExpert(consultantId),
    [consultantId],
  );
  const [expert, setExpert] = useState(null);
  const [expertForm, setExpertForm] = useState({ identity_subject: consultantId === "new" ? "" : consultantId, organization: "", title: "", expertise_summary: "" });
  const [projectId, setProjectId] = useState("");
  const [proposal, setProposal] = useState(null);
  const [weights, setWeights] = useState({});
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [featureIds, setFeatureIds] = useState({});
  const [savedJustifications, setSavedJustifications] = useState({});
  const [selectedJustificationId, setSelectedJustificationId] = useState("");
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [justificationForm, setJustificationForm] = useState({
    rationale: "",
    methodology: "",
    evidence_summary: "",
    expected_effect: "",
    confidence: "",
    limitations: "",
  });

  const published = useMemo(() => (configs.data || []).find((config) => config.status === "published"), [configs.data]);
  const justifications = useAsync(() => proposal ? listJustifications(proposal.id) : Promise.resolve([]), [proposal?.id]);

  useEffect(() => {
    if (expertQuery.data) setExpert(expertQuery.data);
  }, [expertQuery.data]);
  useEffect(() => {
    if (!projectId && scope.projects[0]?.project_id) setProjectId(scope.projects[0].project_id);
  }, [projectId, scope.projects]);
  useEffect(() => {
    if (published && !Object.keys(weights).length) setWeights(published.weights || {});
  }, [published, weights]);
  useEffect(() => {
    const next = {};
    for (const item of justifications.data || []) next[item.feature_definition_id] = item.id;
    setSavedJustifications(next);
  }, [justifications.data]);

  async function createExpert(event) {
    event.preventDefault();
    setBusy(true); setNotice(null);
    try {
      const result = await registerExpert(expertForm);
      setExpert(result);
      setNotice({ kind: "ok", text: "Đã lưu hồ sơ chuyên gia." });
    } catch (error) {
      setNotice({ kind: "error", text: error?.message || "Không thể lưu hồ sơ chuyên gia." });
    } finally { setBusy(false); }
  }

  async function createProposal() {
    if (!expert?.id || !published?.id || !projectId) return;
    setBusy(true); setNotice(null);
    try {
      const result = await createGovernanceProposal({ base_config_id: published.id, project_id: projectId, created_by_expert_id: expert.id });
      setProposal(result);
      setNotice({ kind: "ok", text: "Đã tạo đề xuất nháp." });
    } catch (error) {
      setNotice({ kind: "error", text: error?.message || "Không thể tạo đề xuất." });
    } finally { setBusy(false); }
  }

  async function saveWeights() {
    if (!proposal || !expert?.id) return;
    setBusy(true); setNotice(null);
    try {
      const draft = await createRankingConfigDraft({ weights, min_weight_coverage: Number(published?.min_weight_coverage || 0.5), note: `Đề xuất governance ${proposal.id}`, created_by: expert.id, copied_from_version: published?.version });
      const linked = await setGovernanceProposalConfig(proposal.id, { proposed_config_id: draft.id, actor_expert_id: expert.id });
      setProposal(linked);
      setNotice({ kind: "ok", text: `Đã tạo config nháp v${draft.version} và gắn vào đề xuất.` });
    } catch (error) {
      setNotice({ kind: "error", text: error?.message || "Không thể lưu trọng số. Thao tác này cần quyền admin." });
    } finally { setBusy(false); }
  }

  async function saveJustification(featureKey) {
    const featureDefinitionId = featureIds[featureKey];
    if (!proposal || !expert?.id || !featureDefinitionId) return;
    setBusy(true); setNotice(null);
    try {
      const result = await upsertJustification(proposal.id, {
        feature_definition_id: featureDefinitionId,
        previous_weight: published?.weights?.[featureKey]?.weight == null ? null : String(published.weights[featureKey].weight),
        proposed_weight: String(weights[featureKey]?.weight ?? 0),
        ...justificationForm,
        created_by_expert_id: expert.id,
      });
      setSavedJustifications((current) => ({ ...current, [featureDefinitionId]: result.id }));
      setSelectedJustificationId(result.id);
      setNotice({ kind: "ok", text: `Đã lưu justification cho ${featureKey}.` });
    } catch (error) {
      setNotice({ kind: "error", text: error?.message || "Không thể lưu justification." });
    } finally { setBusy(false); }
  }

  async function submitProposal() {
    if (!proposal || !expert?.id) return;
    setBusy(true); setNotice(null);
    try {
      const result = await submitGovernanceProposal(proposal.id, { actor_expert_id: expert.id });
      setProposal(result);
      setNotice({ kind: "ok", text: "Đã gửi đề xuất sang bước review." });
    } catch (error) {
      setNotice({ kind: "error", text: error?.message || "Không thể submit đề xuất." });
    } finally { setBusy(false); }
  }

  const featureEntries = Object.entries(weights);
  return (
    <>
      <GlobalKeyframes />
      <header style={S.pageHead}><div><h1 style={S.h1}>Bằng chứng và trọng số</h1><p style={S.sub}>Luồng governance cho chuyên gia · mọi đề xuất vẫn cần reviewer phê duyệt.</p></div><span style={S.badge}>{proposal?.status || "Chưa có đề xuất"}</span></header>
      {notice && <div role="status" style={notice.kind === "error" ? S.error : S.success}>{notice.text}</div>}
      <section style={S.card}>
        <h2 style={S.h2}>Hồ sơ chuyên gia</h2>
        {expert ? <p style={S.muted}>Đang dùng <b>{expert.identity_subject}</b> · {expert.title || "Chưa có chức danh"}</p> : <form onSubmit={createExpert} style={S.form}>
          {Object.entries(expertForm).map(([key, value]) => <label key={key} style={S.label}>{key}<input style={S.input} value={value} onChange={(event) => setExpertForm((current) => ({ ...current, [key]: event.target.value }))} required={key === "identity_subject"} /></label>)}
          <button type="submit" style={S.primary} disabled={busy || expertQuery.loading}>{busy ? "Đang lưu…" : "Lưu hồ sơ"}</button>
        </form>}
      </section>
      <section style={S.card}>
        <div style={S.cardHead}><div><h2 style={S.h2}>Đề xuất trọng số</h2><p style={S.muted}>Các feature hiển thị đúng theo config backend; không có feature definition thì không thể lưu justification.</p></div><select style={S.input} value={projectId} onChange={(event) => setProjectId(event.target.value)} aria-label="Chọn dự án">
          <option value="">Chọn dự án</option>{scope.projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.name}</option>)}
        </select></div>
        {!proposal ? <button type="button" style={S.primary} onClick={createProposal} disabled={busy || !expert?.id || !published?.id || !projectId}>Tạo đề xuất từ config v{published?.version || "—"}</button> : <>
          <div style={S.form}>{Object.entries(justificationForm).map(([key, value]) => <label key={key} style={S.label}>{key}{key === "expected_effect" ? <select style={S.input} value={value} onChange={(event) => setJustificationForm((current) => ({ ...current, [key]: event.target.value }))} required><option value="">Chọn tác động</option><option value="increase">increase</option><option value="decrease">decrease</option><option value="neutral">neutral</option><option value="context_dependent">context_dependent</option></select> : key === "confidence" ? <select style={S.input} value={value} onChange={(event) => setJustificationForm((current) => ({ ...current, [key]: event.target.value }))} required><option value="">Chọn độ tin cậy</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select> : <textarea style={S.textarea} value={value} onChange={(event) => setJustificationForm((current) => ({ ...current, [key]: event.target.value }))} required />}</label>)}</div>
          <div style={S.featureList}>{featureEntries.map(([key, spec]) => <div key={key} style={S.featureBlock}><FeatureWeightSlider featureKey={key} spec={spec} onChange={(weight) => setWeights((current) => ({ ...current, [key]: { ...current[key], weight } }))} /><div style={S.justifyRow}><input style={S.input} value={featureIds[key] || ""} onChange={(event) => setFeatureIds((current) => ({ ...current, [key]: event.target.value }))} placeholder="feature_definition UUID" aria-label={`Feature definition ${key}`} /><button type="button" style={S.secondary} onClick={() => saveJustification(key)} disabled={busy || !featureIds[key] || Object.values(justificationForm).some((value) => !value.trim())}>Lưu justification</button>{savedJustifications[featureIds[key]] && <span style={S.saved}>Đã lưu</span>}</div></div>)}</div>
          <div style={S.actions}><button type="button" style={S.primary} onClick={saveWeights} disabled={busy}>Lưu config nháp</button><button type="button" style={S.secondary} onClick={submitProposal} disabled={busy || proposal.status !== "draft"}>Gửi review</button></div>
        </>}
        {!published && !configs.loading && <EmptyState compact title="Chưa có published ranking config" />}
        {configs.error && <ErrorState error={configs.error} onRetry={configs.reload} compact />}
      </section>
      <section style={S.twoCol}>
        <section style={S.card}><h2 style={S.h2}>Nhập bằng chứng</h2><EvidenceUploader proposalId={proposal?.id} justificationId={selectedJustificationId} expertId={expert?.id} onRegistered={(document) => setSelectedDocumentId(document.id)} /><select style={S.input} value={selectedJustificationId} onChange={(event) => setSelectedJustificationId(event.target.value)} aria-label="Chọn justification để liên kết"><option value="">Chọn justification</option>{(justifications.data || []).map((item) => <option key={item.id} value={item.id}>{item.feature_definition_id}</option>)}</select></section>
        <ChunkViewer documentId={selectedDocumentId} />
      </section>
    </>
  );
}

const S = {
  pageHead: { display: "flex", justifyContent: "space-between", gap: space(4), alignItems: "flex-start", marginBottom: space(5) },
  h1: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h1, letterSpacing: "-.03em" },
  sub: { margin: "5px 0 0", color: color.muted, fontSize: size.small },
  badge: { padding: "7px 11px", borderRadius: radius.pill, background: color.canvas, color: color.muted, fontSize: size.tiny, fontWeight: 700 },
  card: { display: "grid", gap: space(4), marginBottom: space(4), padding: space(4), background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow },
  twoCol: { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: space(4), alignItems: "start" },
  cardHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4) },
  h2: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2 },
  muted: { margin: 0, color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  form: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: space(3) },
  label: { display: "grid", gap: 5, color: color.ink, fontSize: size.tiny, fontWeight: 700 },
  input: { minWidth: 0, padding: "9px 10px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, fontFamily: "inherit" },
  textarea: { minWidth: 0, minHeight: 74, padding: "9px 10px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, fontFamily: "inherit", resize: "vertical" },
  primary: { justifySelf: "start", border: 0, borderRadius: radius.sm, padding: "10px 14px", background: color.accent, color: "#fff", fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  secondary: { border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "9px 12px", background: color.surface, color: color.body, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  featureList: { display: "grid" },
  featureBlock: { borderBottom: `1px solid ${color.border}`, paddingBottom: space(2) },
  justifyRow: { display: "flex", alignItems: "center", gap: space(2), flexWrap: "wrap" },
  saved: { color: color.ok, fontSize: size.tiny },
  actions: { display: "flex", gap: space(2), flexWrap: "wrap" },
  success: { padding: space(3), marginBottom: space(4), borderRadius: radius.sm, background: color.okSoft, color: color.ok, fontSize: size.small },
  error: { padding: space(3), marginBottom: space(4), borderRadius: radius.sm, background: color.dangerSoft, color: color.danger, fontSize: size.small },
};
