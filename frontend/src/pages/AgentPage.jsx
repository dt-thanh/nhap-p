import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
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
import { areaLabel } from "../utils/areaLabel";

const ROLE_LEVEL = { business_viewer: 0, pipeline_operator: 1, admin: 2 };
const STATUS = {
  pending_approval: { label: "Chờ duyệt", color: color.warn, bg: color.warnSoft },
  approved: { label: "Đã duyệt · Chờ thực thi", color: color.ok, bg: color.okSoft },
  rejected: { label: "Đã từ chối", color: color.danger, bg: color.dangerSoft },
};
const INITIAL_MESSAGE = {
  role: "agent",
  text: "## Xin chào!\n\nMình là Trợ lý AI hỗ trợ nhà đầu tư trong việc phân tích dự án BDS và giải thích các thông tin, số liệu, đề xuất tới nhà đầu tư và đưa ra đề xuất hợp lý",
};
const SUGGESTIONS = [
  "Đội bán hàng nên gọi tư vấn những căn nào trước trong tuần này?",
  "Phân khu nào đang có nhiều hàng nhưng cần tập trung nguồn lực bán hơn?",
  "Quỹ căn nào vừa còn bán được vừa có tín hiệu khách hàng quan tâm tốt?",
];

// Sàn cứng của khung chat. Đo từ chính nó: phần đầu thẻ (~60) + khối soạn tin
// (~94) = ~154 px khung cố định, cộng ~86 px để còn thấy được vài dòng tin nhắn.
// Thấp hơn nữa thì thẻ không dùng được; cao hơn nữa thì trên màn hình 600 px
// khung soạn tin lại bị đẩy xuống dưới mép — đúng lỗi đang sửa.
const MIN_CHAT_HEIGHT = 240;
// Trên mobile hai thẻ xếp chồng và trang cuộn bình thường, nên khung chat không
// được chiếm trọn màn hình đầu tiên — nhưng vẫn phải để lộ khối soạn tin.
const MOBILE_MAX_CHAT_HEIGHT = 520;

/**
 * Chiều cao còn lại từ đỉnh của `ref` tới đáy cửa sổ, ĐO thật thay vì trừ một
 * hằng số. Lý do: chiều cao phần đầu trang không cố định — `scopeBar` dùng
 * `flexWrap` nên nó cao thêm một dòng khi cửa sổ hẹp, và ô chọn "Phân khu" chỉ
 * xuất hiện sau khi đã chọn dự án. Mọi hằng số viết tay đều sai ở một trong
 * những trạng thái đó.
 *
 * Trả `null` trước lần đo đầu tiên.
 */
function useFillViewportHeight(ref) {
  const [height, setHeight] = useState(null);

  const measure = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    // Khoảng thở dưới đáy = padding-bottom THẬT của khung cuộn trong AppLayout,
    // đọc ra chứ không viết cứng. Trừ một hằng số nhỏ hơn nó sẽ để trang thừa ra
    // vài chục pixel và sinh thanh cuộn cho một vùng trống — đúng cái kiểu lệch
    // đã tạo ra lỗi này ngay từ đầu.
    const shell = node.parentElement;
    const trailing = shell ? parseFloat(window.getComputedStyle(shell).paddingBottom) || 0 : 0;
    const available = window.innerHeight - node.getBoundingClientRect().top - trailing;
    const next = Math.round(available);
    // Chỉ ghi khi lệch thật: đặt chiều cao làm layout đổi, layout đổi lại kích
    // hoạt ResizeObserver — không có chốt này thì hai bên gọi nhau vô tận.
    setHeight((current) => (current !== null && Math.abs(current - next) <= 1 ? current : next));
  }, [ref]);

  useLayoutEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    // Theo dõi cả phần đầu trang: `scopeBar` xuống dòng, hoặc ô "Phân khu" hiện
    // ra, đều làm đỉnh của workspace tụt xuống mà không có sự kiện `resize` nào.
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if (observer) observer.observe(document.body);
    return () => {
      window.removeEventListener("resize", measure);
      if (observer) observer.disconnect();
    };
  }, [measure]);

  return height;
}

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
  const [composerFocused, setComposerFocused] = useState(false);
  const [recNotice, setRecNotice] = useState("");
  const [recNoticeKind, setRecNoticeKind] = useState("info");
  const [lastResolvedProjectId, setLastResolvedProjectId] = useState(null);
  const chatBody = useRef(null);
  const workspace = useRef(null);
  const available = useFillViewportHeight(workspace);
  // Desktop: workspace lấp trọn phần còn lại, hai thẻ cao bằng nhau.
  // Mobile: workspace tự do (thẻ đề xuất xếp dưới), chỉ khung chat bị chặn trần.
  const workspaceHeight = !isMobile && available !== null ? Math.max(MIN_CHAT_HEIGHT, available) : undefined;
  const chatHeight = available === null
    ? undefined
    : Math.max(MIN_CHAT_HEIGHT, isMobile ? Math.min(available, MOBILE_MAX_CHAT_HEIGHT) : available);
  const canApprove = me.data && ROLE_LEVEL[me.data.role] >= ROLE_LEVEL.pipeline_operator;
  const canExecute = me.data?.role === "admin";
  const proposalProjectId = scope.projectExternalId || lastResolvedProjectId;
  const canSend = Boolean(input.trim()) && !sending;

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
            {(scope.areas || []).filter((area) => area.external_id).map((area) => <option key={area.external_id} value={area.external_id}>{areaLabel(area)}</option>)}
          </select>
        </label>}
        <div style={S.freshness}><span style={S.greenDot} /> PostgreSQL · theo phạm vi tài khoản</div>
      </section>

      <div
        ref={workspace}
        style={{
          ...S.workspace,
          gridTemplateColumns: isMobile ? "1fr" : "minmax(0, 1.65fr) minmax(320px, .85fr)",
          // Desktop: hai thẻ cùng cao, lấp đúng phần màn hình còn lại, và
          // `alignItems: stretch` để thẻ con nhận trọn chiều cao đó.
          // Mobile: bỏ ràng buộc, trang cuộn như mọi trang khác.
          height: workspaceHeight,
          alignItems: workspaceHeight ? "stretch" : "start",
        }}
      >
        <section
          style={{ ...S.chatCard, height: workspaceHeight ? "100%" : chatHeight }}
          aria-label="Chat AI tư vấn"
        >

          <div style={S.chatBody} ref={chatBody}>
            {messages.map((message, index) => <Message key={index} message={message} />)}
            {sending && <div style={{ ...S.bubble, ...S.agentBubble, color: color.muted }}>Đang tra cứu dữ liệu…</div>}
            {messages.length === 1 && <div style={S.suggestions}>{SUGGESTIONS.map((text) => <button key={text} style={S.suggestion} onClick={() => send(text)}>{text}</button>)}</div>}
          </div>
          <div style={S.composerWrap}>
            <form style={S.composer} onSubmit={(event) => { event.preventDefault(); send(); }}>
              <textarea
                style={{ ...S.textarea, ...(composerFocused ? S.textareaFocused : {}) }}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onFocus={() => setComposerFocused(true)}
                onBlur={() => setComposerFocused(false)}
                placeholder="Hỏi về dự án, phân khu, tồn kho, xu hướng hoặc căn ưu tiên…"
                rows={2}
                disabled={sending}
                aria-label="Câu hỏi gửi cho AI tư vấn"
                onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }}
              />
              <button
                type="submit"
                style={{ ...S.sendButton, ...(canSend ? {} : S.sendButtonDisabled) }}
                disabled={!canSend}
                aria-label="Gửi câu hỏi"
              >
                {sending ? "Đang gửi…" : "Gửi"}
              </button>
            </form>
          </div>
        </section>

        <aside style={{ ...S.proposalCard, maxHeight: workspaceHeight ? "100%" : undefined }}>
          <div style={S.cardHead}><div><h2 style={S.h2}>Đề xuất hành động</h2></div></div>
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
  const riskLabel = { low: "Thấp", medium: "Trung bình", high: "Cao" }[data.risk_level] || "Chưa đánh giá";
  return <div>
    <div style={S.recTop}><span style={{ ...S.status, color: badge.color, background: badge.bg }}>{badge.label}</span><button style={S.linkButton} onClick={regenerate} disabled={loading}>Tạo lại</button></div>
    <SafeMarkdown>{data.summary}</SafeMarkdown>
    <div style={S.metaGrid}><Meta label="Rủi ro sử dụng đề xuất" value={riskLabel} /><Meta label="Độ phủ tín hiệu đầu vào" value={data.confidence != null ? `${Math.round(data.confidence * 100)}%` : "—"} /><Meta label="Hành động sau duyệt" value="Tạo danh sách ưu tiên" /><Meta label="Số căn" value={data.action_payload?.unit_ids?.length ?? data.recommended_actions?.length ?? 0} /></div>
    {data.recommended_actions?.length > 0 && <div style={S.actionBox}><b>Căn cần ưu tiên tiếp cận</b><ol style={S.actionList}>{data.recommended_actions.slice(0, 10).map((item, index) => <li key={index}><strong>{item.unit_id}</strong> — {item.action}<small style={S.reason}>{item.reason}</small></li>)}</ol></div>}

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
  // KHÔNG đặt chiều cao bằng `calc(100vh - <hằng số>)`: hằng số đó phải khớp
  // tổng chiều cao của thanh điều hướng + padding của <main> + pageHead +
  // scopeBar, tức là năm giá trị nằm ở ba file khác nhau. Nó ĐÃ lệch (245 so
  // với ~334 thực tế) và đẩy khung soạn tin xuống dưới mép màn hình. Chiều cao
  // nay do `useFillViewportHeight` ĐO tại runtime — xem hàm đó.
  //
  // `minHeight: 0` là bắt buộc, không phải trang trí: mặc định `min-height` của
  // một flex item là `auto`, nghĩa là nó KHÔNG co nhỏ hơn nội dung — vùng tin
  // nhắn sẽ đẩy phồng thẻ ra thay vì tự cuộn bên trong.
  chatCard: { minHeight: 0, background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, display: "flex", flexDirection: "column", overflow: "hidden" },
  proposalCard: { minHeight: 0, background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, padding: space(4), overflowY: "auto" },
  cardHead: { padding: `${space(1)}px 0 ${space(3)}px`, borderBottom: `1px solid ${color.border}` }, h2: { margin: 0, color: color.ink, fontSize: size.h2, fontFamily: font.display }, cardSub: { margin: "3px 0 0", color: color.muted, fontSize: size.tiny },
  chatCardHead: {}, chatBody: { flex: 1, overflowY: "auto", padding: space(4), display: "flex", flexDirection: "column", gap: space(3), background: color.canvas },
  bubble: { maxWidth: "90%", padding: `${space(3)}px ${space(4)}px`, borderRadius: radius.md, fontSize: size.small, wordBreak: "break-word" },
  agentBubble: { background: color.surface, color: color.body, border: `1px solid ${color.border}`, borderBottomLeftRadius: 4 }, userBubble: { background: color.accent, color: "#fff", borderBottomRightRadius: 4 }, errorBubble: { background: color.dangerSoft, color: color.danger },
  suggestions: { display: "flex", flexWrap: "wrap", gap: space(2) }, suggestion: { background: color.surface, color: color.accent, border: `1px solid ${color.borderStrong}`, borderRadius: radius.pill, padding: "8px 12px", fontFamily: "inherit", fontSize: size.tiny, cursor: "pointer" },
  trace: { marginTop: space(2), paddingTop: space(2), borderTop: `1px solid ${color.border}`, color: color.muted, fontSize: size.tiny }, traceBody: { marginTop: 4, fontFamily: font.mono }, source: { marginTop: space(2), color: color.muted, fontSize: 10 },
  // `flex: none` — khối soạn tin KHÔNG được co lại khi danh sách tin nhắn dài.
  // Nó là thứ duy nhất trên trang này người dùng bắt buộc phải chạm tới.
  composerWrap: { flex: "none", borderTop: `1px solid ${color.border}`, background: color.surface, padding: `${space(3)}px ${space(3)}px ${space(2)}px` },
  composer: { display: "flex", gap: space(2), alignItems: "stretch" },
  textarea: { flex: 1, minWidth: 0, resize: "none", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "10px 12px", fontFamily: "inherit", fontSize: size.small, outline: "none", color: color.ink, background: color.surface, transition: "border-color .15s, box-shadow .15s" },
  // Vòng focus hiện bằng state chứ không bằng `:focus` — trang này tô kiểu bằng
  // style nội tuyến, không có stylesheet để gắn pseudo-class vào. Bỏ hẳn vòng
  // focus (`outline: none` ở trên) mà không thay bằng gì khác là làm mất dấu
  // con trỏ của người dùng bàn phím.
  textareaFocused: { borderColor: color.accent, boxShadow: `0 0 0 3px ${color.accentSoft}` },
  sendButton: { alignSelf: "stretch", background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "0 18px", fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap", fontFamily: "inherit", fontSize: size.small },
  // Nút bị vô hiệu phải TRÔNG như bị vô hiệu. Giữ nguyên màu nhấn khi không bấm
  // được là nói dối người dùng bằng giao diện.
  sendButtonDisabled: { background: color.borderStrong, color: color.surface, cursor: "not-allowed" },
  composerHint: { margin: `${space(2)}px 0 0`, color: color.muted, fontSize: size.tiny },
  emptyProposal: { minHeight: 360, display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center", flexDirection: "column", color: color.muted }, emptyIcon: { color: color.accent, background: color.accentSoft, borderRadius: "50%", width: 54, height: 54, display: "grid", placeItems: "center", fontSize: 24, marginBottom: space(3) },
  primary: { background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "10px 16px", fontWeight: 700, cursor: "pointer" }, error: { background: color.dangerSoft, color: color.danger, borderRadius: radius.sm, padding: space(3), marginTop: space(3) },
  recTop: { display: "flex", justifyContent: "space-between", alignItems: "center", margin: `${space(3)}px 0` }, status: { borderRadius: radius.pill, padding: "5px 10px", fontSize: size.tiny, fontWeight: 700 }, linkButton: { border: 0, background: "transparent", color: color.accent, cursor: "pointer", fontFamily: "inherit" },
  metaGrid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: space(2), margin: `${space(3)}px 0` }, meta: { background: color.canvas, borderRadius: radius.sm, padding: space(2), display: "flex", flexDirection: "column", gap: 2, fontSize: size.tiny, color: color.muted },
  actionBox: { border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: space(3), fontSize: size.small }, actionList: { paddingLeft: space(5), display: "grid", gap: space(2), marginBottom: 0 }, reason: { display: "block", color: color.muted, marginTop: 2 },
  decision: { display: "grid", gap: space(3), borderTop: `1px solid ${color.border}`, paddingTop: space(4), marginTop: space(4) }, input: { padding: "9px 10px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontFamily: "inherit" }, row: { display: "flex", gap: space(2) }, approve: { background: color.ok, color: "#fff", border: 0, borderRadius: radius.sm, padding: "9px 15px", fontWeight: 700 }, reject: { background: color.surface, color: color.danger, border: `1px solid ${color.danger}`, borderRadius: radius.sm, padding: "9px 15px", fontWeight: 700 }, execute: { background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "11px 16px", fontWeight: 700 },
  notice: { background: color.canvas, color: color.muted, borderRadius: radius.sm, padding: space(3), fontSize: size.small }, warning: { background: color.warnSoft, color: color.body, borderRadius: radius.sm, padding: space(3), fontSize: size.small }, success: { display: "grid", gap: 4, marginTop: space(4), background: color.okSoft, color: color.ok, borderRadius: radius.sm, padding: space(3), fontSize: size.small },
  decided: { color: color.muted, fontSize: size.small, borderTop: `1px solid ${color.border}`, paddingTop: space(3) },
};
