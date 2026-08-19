import React, { useEffect, useRef, useState } from "react";
import { chatWithAgent, getMePermissions } from "../api/endpoints";
import { isAuthError } from "../api/client";
import { useAgentRecommendation } from "../hooks/useAgentRecommendation";
import { useAsync } from "../hooks/useAsync";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { useProjectScope } from "../hooks/useProjectScope";
import ProjectSelector from "../components/ProjectSelector";
import SafeMarkdown from "../components/SafeMarkdown";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

const ROLE_LEVEL = { business_viewer: 0, pipeline_operator: 1, admin: 2 };
const STATUS = {
  pending_approval: { label: "Chờ duyệt", color: color.warn, bg: color.warnSoft },
  approved: { label: "Đã duyệt · Chờ thực thi", color: color.ok, bg: color.okSoft },
  rejected: { label: "Đã từ chối", color: color.danger, bg: color.dangerSoft },
};
const INITIAL_MESSAGE = {
  role: "agent",
  text: "## Chào bạn\n\nMình có thể tra cứu dữ liệu dự án, so sánh phân khu, phân tích hấp thụ và tìm các căn nên ưu tiên. Mọi hành động thay đổi hệ thống đều phải được con người phê duyệt trước khi thực thi.",
};
const SUGGESTIONS = [
  "Có bao nhiêu dự án hiện tại, bạn giúp tôi được gì?",
  "So sánh quy mô và tốc độ bán giữa các phân khu",
  "Top 10 căn nào nên ưu tiên bán?",
];

export default function AgentPage() {
  const scope = useProjectScope();
  const rec = useAgentRecommendation();
  const me = useAsync(() => getMePermissions(), []);
  const { isMobile } = useBreakpoint();
  const [messages, setMessages] = useState([INITIAL_MESSAGE]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [recNotice, setRecNotice] = useState("");
  const [recNoticeKind, setRecNoticeKind] = useState("info");
  const [lastResolvedProjectId, setLastResolvedProjectId] = useState(null);
  const chatBody = useRef(null);
  const canApprove = me.data && ROLE_LEVEL[me.data.role] >= ROLE_LEVEL.pipeline_operator;
  const canExecute = me.data?.role === "admin";
  const proposalProjectId = scope.projectExternalId || lastResolvedProjectId;

  useEffect(() => {
    const node = chatBody.current;
    if (!node) return;
    if (typeof node.scrollTo === "function") node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    else node.scrollTop = node.scrollHeight;
  }, [messages, sending]);

  async function send(text) {
    const message = (text ?? input).trim();
    if (!message || sending) return;
    setMessages((items) => [...items, { role: "user", text: message }]);
    setInput(""); setSending(true);
    try {
      const result = await chatWithAgent(message, scope.projectExternalId);
      if (result.resolved_project_id) {
        setLastResolvedProjectId(result.resolved_project_id);
        if (!scope.projectExternalId) {
          scope.setProjectExternalId(result.resolved_project_id);
          setRecNoticeKind("info");
          setRecNotice(`Đã nhận diện dự án ${result.resolved_project_id}. Bạn có thể bấm Tạo đề xuất.`);
        }
      }
      setMessages((items) => [...items, {
        role: "agent", text: result.response || "Không có nội dung trả lời.",
        sources: result.sources || [], toolCalls: result.tool_calls || [], resolvedProjectId: result.resolved_project_id || null,
      }]);
    } catch (error) {
      setMessages((items) => [...items, { role: "agent", error: true, text: error.message || "Không thể gọi AI tư vấn." }]);
    } finally { setSending(false); }
  }

  async function generate() {
    if (!proposalProjectId) {
      setRecNoticeKind("error");
      setRecNotice("Hãy chọn một dự án, hoặc hỏi rõ tên dự án trong chat để AI nhận diện trước khi tạo đề xuất.");
      return;
    }
    setRecNoticeKind("info");
    setRecNotice("Đang tạo đề xuất từ ranking và dữ liệu tồn kho hiện tại…");
    try {
      const areaId = scope.projectExternalId === proposalProjectId ? scope.areaExternalId || undefined : undefined;
      const data = await rec.generate(proposalProjectId, areaId);
      const unitCount = data.action_payload?.unit_ids?.length ?? data.recommended_actions?.length ?? 0;
      setRecNoticeKind("info");
      setRecNotice(`Đã tạo đề xuất chờ duyệt${unitCount ? ` cho ${unitCount} căn` : ""}.`);
    } catch (error) {
      setRecNoticeKind("error");
      setRecNotice(errorMessage(error));
    }
  }
  async function decide(kind) {
    if (!rec.data || !actor.trim()) return;
    try {
      await (kind === "approve" ? rec.approve : rec.reject)(rec.data.recommendation_id, reason, actor.trim());
    } catch { /* hook owns error */ }
  }
  async function execute() {
    if (!rec.data || !actor.trim()) return;
    try { await rec.execute(rec.data.recommendation_id, actor.trim()); } catch { /* hook owns error */ }
  }

  return (
    <>
      <GlobalKeyframes />
      <header style={S.pageHead}>
        <div><h1 style={S.h1}>AI tư vấn bán hàng</h1><p style={S.sub}>Phân tích dữ liệu thật · Đề xuất có bằng chứng · Con người phê duyệt trước khi thực thi</p></div>
        <span style={S.safeBadge}>● Dữ liệu nội bộ</span>
      </header>

      <section style={S.scopeBar} aria-label="Phạm vi phân tích">
        <ProjectSelector projects={scope.projects} value={scope.projectExternalId} onChange={scope.setProjectExternalId}
          loading={scope.loadingProjects} status={scope.projectsStatus === "unauthorized" ? "unauthorized" : scope.projectsStatus === "error" ? "error" : undefined} />
        {scope.projectExternalId && <label style={S.label}>Phân khu
          <select style={S.select} value={scope.areaExternalId ?? "all"} onChange={(event) => scope.setAreaExternalId(event.target.value === "all" ? null : event.target.value)}>
            <option value="all">Toàn dự án</option>
            {(scope.areas || []).filter((area) => area.external_id).map((area) => <option key={area.external_id} value={area.external_id}>{area.area_name} · {area.unit_type}</option>)}
          </select>
        </label>}
        <div style={S.freshness}><span style={S.greenDot} /> PostgreSQL · theo phạm vi tài khoản</div>
      </section>

      <div style={{ ...S.workspace, gridTemplateColumns: isMobile ? "1fr" : "minmax(0, 1.65fr) minmax(320px, .85fr)" }}>
        <section style={S.chatCard} aria-label="Chat AI tư vấn">
          <div style={S.cardHead}><div><h2 style={S.h2}>Trò chuyện với AI tư vấn</h2><p style={S.cardSub}>Agent tự chọn tool phù hợp với câu hỏi và ghi rõ nguồn dữ liệu.</p></div></div>
          <div style={S.chatBody} ref={chatBody}>
            {messages.map((message, index) => <Message key={index} message={message} />)}
            {sending && <div style={{ ...S.bubble, ...S.agentBubble, color: color.muted }}>Đang tra cứu dữ liệu…</div>}
            {messages.length === 1 && <div style={S.suggestions}>{SUGGESTIONS.map((text) => <button key={text} style={S.suggestion} onClick={() => send(text)}>{text}</button>)}</div>}
          </div>
          <form style={S.composer} onSubmit={(event) => { event.preventDefault(); send(); }}>
            <textarea style={S.textarea} value={input} onChange={(event) => setInput(event.target.value)} placeholder="Hỏi về dự án, phân khu, tồn kho, xu hướng hoặc căn ưu tiên…" rows={2}
              onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} />
            <button style={S.sendButton} disabled={!input.trim() || sending} aria-label="Gửi câu hỏi">Gửi</button>
          </form>
        </section>

        <aside style={S.proposalCard}>
          <div style={S.cardHead}><div><h2 style={S.h2}>Đề xuất hành động</h2><p style={S.cardSub}>Tạo từ ranking hiện tại, không tự động thay đổi dữ liệu.</p></div></div>
          {!rec.data && <div style={S.emptyProposal}>
            <div style={S.emptyIcon}>✦</div><b>Chưa có đề xuất</b>
            <p>{proposalProjectId ? `Đề xuất sẽ tạo cho dự án ${proposalProjectId}.` : "Chọn dự án hoặc hỏi rõ tên dự án trong chat để AI nhận diện trước."}</p>
            <button style={S.primary} onClick={generate} disabled={rec.loading}>{rec.loading ? "Đang tạo…" : "Tạo đề xuất"}</button>
          </div>}
          {recNotice && <div style={recNoticeKind === "error" ? S.error : S.notice}>{recNotice}</div>}
          {rec.error && errorMessage(rec.error) !== recNotice && <div style={S.error}>{errorMessage(rec.error)}</div>}
          {rec.data && <RecommendationCard data={rec.data} loading={rec.loading} me={me} canApprove={canApprove} canExecute={canExecute}
            actor={actor} setActor={setActor} reason={reason} setReason={setReason} decide={decide} execute={execute} regenerate={generate} />}
        </aside>
      </div>
    </>
  );
}

function Message({ message }) {
  const user = message.role === "user";
  return <div style={{ display: "flex", justifyContent: user ? "flex-end" : "flex-start" }}>
    <div style={{ ...S.bubble, ...(user ? S.userBubble : S.agentBubble), ...(message.error ? S.errorBubble : {}) }}>
      {user ? message.text : <SafeMarkdown>{message.text}</SafeMarkdown>}
      {!user && message.toolCalls?.length > 0 && <details style={S.trace}><summary>Đã dùng {message.toolCalls.length} công cụ dữ liệu</summary>
        <div style={S.traceBody}>{message.toolCalls.join(" · ")}</div>
      </details>}
      {!user && message.sources?.length > 0 && <div style={S.source}>Nguồn: PostgreSQL · {new Date(message.sources[0].as_of).toLocaleString("vi-VN")}</div>}
    </div>
  </div>;
}

function RecommendationCard({ data, loading, me, canApprove, canExecute, actor, setActor, reason, setReason, decide, execute, regenerate }) {
  const badge = STATUS[data.status] || { label: data.status, color: color.muted, bg: color.canvas };
  return <div>
    <div style={S.recTop}><span style={{ ...S.status, color: badge.color, background: badge.bg }}>{badge.label}</span><button style={S.linkButton} onClick={regenerate} disabled={loading}>Tạo lại</button></div>
    <SafeMarkdown>{data.summary}</SafeMarkdown>
    <div style={S.metaGrid}><Meta label="Rủi ro" value={data.risk_level === "low" ? "Thấp" : data.risk_level} /><Meta label="Độ tin cậy" value={data.confidence != null ? `${Math.round(data.confidence * 100)}%` : "—"} /><Meta label="Hành động" value="Tạo chiến dịch ưu tiên" /><Meta label="Số căn" value={data.action_payload?.unit_ids?.length ?? data.recommended_actions?.length ?? 0} /></div>
    {data.recommended_actions?.length > 0 && <div style={S.actionBox}><b>Căn được đề xuất</b><ol style={S.actionList}>{data.recommended_actions.slice(0, 10).map((item, index) => <li key={index}><strong>{item.unit_id}</strong> — {item.action}<small style={S.reason}>{item.reason}</small></li>)}</ol></div>}

    {data.status === "pending_approval" && <div style={S.decision}>
      {!canApprove ? <div style={S.notice}>Vai trò <b>{me.data?.role || "chưa xác định"}</b> không đủ để duyệt/từ chối. Cần pipeline_operator trở lên.</div> : <>
        <label style={S.label}>Người duyệt (bắt buộc)<input style={S.input} value={actor} onChange={(event) => setActor(event.target.value)} placeholder="Tên người duyệt" /></label>
        <label style={S.label}>Lý do<input style={S.input} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Ghi chú quyết định" /></label>
        <div style={S.row}><button style={S.approve} disabled={!actor.trim() || loading} onClick={() => decide("approve")}>Duyệt</button><button style={S.reject} disabled={!actor.trim() || loading} onClick={() => decide("reject")}>Từ chối</button></div>
      </>}
    </div>}

    {data.status === "approved" && data.execution_status !== "executed" && <div style={S.decision}>
      {!canExecute ? <div style={S.notice}>Đề xuất đã duyệt. Chỉ <b>admin</b> mới được thực thi thay đổi database.</div> : <>
        <div style={S.warning}><b>Thay đổi sẽ thực hiện:</b> tạo một chiến dịch ưu tiên mới; không sửa trạng thái hoặc giao dịch trong Mini CRM.</div>
        <label style={S.label}>Người thực thi<input style={S.input} value={actor} onChange={(event) => setActor(event.target.value)} placeholder="Tên người thực thi" /></label>
        <button style={S.execute} disabled={!actor.trim() || loading} onClick={execute}>{loading ? "Đang thực thi…" : "Thực thi"}</button>
      </>}
    </div>}
    {data.execution_status === "executed" && <div style={S.success}><b>Đã thực thi thành công</b><span>Campaign: {data.execution_result?.campaign_id}</span><span>{data.execution_result?.unit_count} căn được đưa vào chiến dịch.</span></div>}
    {data.status !== "pending_approval" && <p style={S.decided}>{data.status === "approved" ? "Đã duyệt bởi" : "Đã từ chối bởi"} <b>{data.decided_by || "—"}</b>{data.decision_reason ? ` — ${data.decision_reason}` : ""}</p>}
  </div>;
}

function Meta({ label, value }) { return <div style={S.meta}><span>{label}</span><b>{value}</b></div>; }
function errorMessage(error) {
  if (isAuthError(error)) return error.message;
  if (error?.status === 409) return `Đề xuất đã được quyết định trước đó: ${error.message}`;
  if (error?.status === 503) return "Chưa có cấu hình xếp hạng đang hoạt động.";
  return error?.message || "Đã xảy ra lỗi.";
}

const S = {
  pageHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4), marginBottom: space(5) },
  h1: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h1, letterSpacing: "-.03em" },
  sub: { margin: "5px 0 0", color: color.muted, fontSize: size.small },
  safeBadge: { flex: "none", color: color.ok, background: color.okSoft, borderRadius: radius.pill, padding: "7px 12px", fontSize: size.tiny, fontWeight: 700 },
  scopeBar: { display: "flex", alignItems: "flex-end", gap: space(4), flexWrap: "wrap", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), marginBottom: space(4), boxShadow: shadow },
  label: { display: "flex", flexDirection: "column", gap: 5, color: color.ink, fontSize: size.tiny, fontWeight: 700 },
  select: { minWidth: 190, padding: "9px 11px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, fontFamily: "inherit" },
  freshness: { marginLeft: "auto", alignSelf: "center", color: color.muted, fontSize: size.tiny }, greenDot: { display: "inline-block", width: 7, height: 7, borderRadius: "50%", background: color.ok, marginRight: 6 },
  workspace: { display: "grid", gap: space(4), alignItems: "start" },
  chatCard: { minHeight: 650, height: "calc(100vh - 245px)", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, display: "flex", flexDirection: "column", overflow: "hidden" },
  proposalCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, padding: space(4), maxHeight: "calc(100vh - 245px)", overflowY: "auto" },
  cardHead: { padding: `${space(1)}px 0 ${space(3)}px`, borderBottom: `1px solid ${color.border}` }, h2: { margin: 0, color: color.ink, fontSize: size.h2, fontFamily: font.display }, cardSub: { margin: "3px 0 0", color: color.muted, fontSize: size.tiny },
  chatCardHead: {}, chatBody: { flex: 1, overflowY: "auto", padding: space(4), display: "flex", flexDirection: "column", gap: space(3), background: color.canvas },
  bubble: { maxWidth: "90%", padding: `${space(3)}px ${space(4)}px`, borderRadius: radius.md, fontSize: size.small, wordBreak: "break-word" },
  agentBubble: { background: color.surface, color: color.body, border: `1px solid ${color.border}`, borderBottomLeftRadius: 4 }, userBubble: { background: color.accent, color: "#fff", borderBottomRightRadius: 4 }, errorBubble: { background: color.dangerSoft, color: color.danger },
  suggestions: { display: "flex", flexWrap: "wrap", gap: space(2) }, suggestion: { background: color.surface, color: color.accent, border: `1px solid ${color.borderStrong}`, borderRadius: radius.pill, padding: "8px 12px", fontFamily: "inherit", fontSize: size.tiny, cursor: "pointer" },
  trace: { marginTop: space(2), paddingTop: space(2), borderTop: `1px solid ${color.border}`, color: color.muted, fontSize: size.tiny }, traceBody: { marginTop: 4, fontFamily: font.mono }, source: { marginTop: space(2), color: color.muted, fontSize: 10 },
  composer: { display: "flex", gap: space(2), padding: space(3), borderTop: `1px solid ${color.border}` }, textarea: { flex: 1, resize: "none", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "10px 12px", fontFamily: "inherit", fontSize: size.small, outline: "none" }, sendButton: { alignSelf: "stretch", background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "0 18px", fontWeight: 700, cursor: "pointer" },
  emptyProposal: { minHeight: 360, display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center", flexDirection: "column", color: color.muted }, emptyIcon: { color: color.accent, background: color.accentSoft, borderRadius: "50%", width: 54, height: 54, display: "grid", placeItems: "center", fontSize: 24, marginBottom: space(3) },
  primary: { background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "10px 16px", fontWeight: 700, cursor: "pointer" }, error: { background: color.dangerSoft, color: color.danger, borderRadius: radius.sm, padding: space(3), marginTop: space(3) },
  recTop: { display: "flex", justifyContent: "space-between", alignItems: "center", margin: `${space(3)}px 0` }, status: { borderRadius: radius.pill, padding: "5px 10px", fontSize: size.tiny, fontWeight: 700 }, linkButton: { border: 0, background: "transparent", color: color.accent, cursor: "pointer", fontFamily: "inherit" },
  metaGrid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: space(2), margin: `${space(3)}px 0` }, meta: { background: color.canvas, borderRadius: radius.sm, padding: space(2), display: "flex", flexDirection: "column", gap: 2, fontSize: size.tiny, color: color.muted },
  actionBox: { border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: space(3), fontSize: size.small }, actionList: { paddingLeft: space(5), display: "grid", gap: space(2), marginBottom: 0 }, reason: { display: "block", color: color.muted, marginTop: 2 },
  decision: { display: "grid", gap: space(3), borderTop: `1px solid ${color.border}`, paddingTop: space(4), marginTop: space(4) }, input: { padding: "9px 10px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontFamily: "inherit" }, row: { display: "flex", gap: space(2) }, approve: { background: color.ok, color: "#fff", border: 0, borderRadius: radius.sm, padding: "9px 15px", fontWeight: 700 }, reject: { background: color.surface, color: color.danger, border: `1px solid ${color.danger}`, borderRadius: radius.sm, padding: "9px 15px", fontWeight: 700 }, execute: { background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "11px 16px", fontWeight: 700 },
  notice: { background: color.canvas, color: color.muted, borderRadius: radius.sm, padding: space(3), fontSize: size.small }, warning: { background: color.warnSoft, color: color.body, borderRadius: radius.sm, padding: space(3), fontSize: size.small }, success: { display: "grid", gap: 4, marginTop: space(4), background: color.okSoft, color: color.ok, borderRadius: radius.sm, padding: space(3), fontSize: size.small },
  decided: { color: color.muted, fontSize: size.small, borderTop: `1px solid ${color.border}`, paddingTop: space(3) },
};
