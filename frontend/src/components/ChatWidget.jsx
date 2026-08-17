// frontend/src/components/ChatWidget.jsx
// Nút chat nổi ở góc phải dưới + khung hội thoại với AI agent.
//
// ĐIỂM ĐÁNG CHÚ Ý: widget này gọi POST /api/v1/chat — endpoint backend ĐÃ CHẠY
// THẬT (xem Swagger). Nên dù toàn app đang chạy trên mock, phần chat này nói
// chuyện với LangGraph agent thật.
//
// Response schema: { response: string, analysis: string }
import React, { useState, useRef, useEffect } from "react";
import { chatWithAgent } from "../api/endpoints";
import { color, size, radius, space, font } from "../styles/tokens";
import { useBreakpoint } from "../hooks/useBreakpoint";

const GREETING = {
  role: "agent",
  text: "Chào bạn. Mình có thể giúp phân tích tốc độ hấp thụ, giải thích dự báo hoặc gợi ý hành động cho từng phân khu. Bạn muốn hỏi gì?",
};

// Câu hỏi mồi — giúp người dùng biết hỏi gì, giảm "màn hình trắng"
const SUGGESTIONS = [
  "Phân khu nào sắp cạn hàng?",
  "Vì sao Phân khu A · 3PN bán chậm?",
  "Nên đẩy loại căn nào trước?",
];

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([GREETING]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bodyRef = useRef(null);
  const { isMobile } = useBreakpoint();

  // Tự cuộn xuống tin mới nhất
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, open]);

  async function send(text) {
    const msg = (text ?? input).trim();
    if (!msg || sending) return;

    setMessages((m) => [...m, { role: "user", text: msg }]);
    setInput("");
    setSending(true);

    try {
      const res = await chatWithAgent(msg);
      setMessages((m) => [
        ...m,
        { role: "agent", text: res?.response || "(agent không trả lời)", analysis: res?.analysis },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: "agent",
          error: true,
          text:
            e.status === 404
              ? "Chưa kết nối được AI agent. Kiểm tra service api đã chạy chưa."
              : `Lỗi khi gọi agent: ${e.message}`,
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      {/* ---- Khung hội thoại ---- */}
      {open && (
        <section
          style={{
            ...S.panel,
            width: isMobile ? "calc(100vw - 24px)" : 380,
            height: isMobile ? "70vh" : 520,
          }}
          role="dialog"
          aria-label="Trò chuyện với AI agent"
        >
          <header style={S.head}>
            <div>
              <div style={S.headTitle}>Trợ lý AbsorptionForecast</div>
              <div style={S.headSub}>
                <span style={S.dot} aria-hidden="true" /> AI agent · LangGraph
              </div>
            </div>
            <button onClick={() => setOpen(false)} style={S.close} aria-label="Đóng">✕</button>
          </header>

          <div style={S.body} ref={bodyRef}>
            {messages.map((m, i) => (
              <Bubble key={i} msg={m} />
            ))}
            {sending && (
              <div style={{ ...S.bubble, ...S.agentBubble, color: color.muted }}>
                Đang suy nghĩ…
              </div>
            )}

            {messages.length === 1 && (
              <div style={S.suggestWrap}>
                {SUGGESTIONS.map((s) => (
                  <button key={s} style={S.suggest} onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={S.inputRow}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Nhập câu hỏi…"
              maxLength={5000}
              style={S.input}
              disabled={sending}
            />
            <button
              onClick={() => send()}
              disabled={sending || !input.trim()}
              style={{ ...S.sendBtn, opacity: sending || !input.trim() ? 0.45 : 1 }}
              aria-label="Gửi"
            >
              ➤
            </button>
          </div>
        </section>
      )}

      {/* ---- Nút nổi ---- */}
      <button
        onClick={() => setOpen((o) => !o)}
        style={S.fab}
        aria-label={open ? "Đóng trò chuyện" : "Mở trò chuyện với AI"}
      >
        {open ? "✕" : (
          <>
            <span style={S.fabIcon} aria-hidden="true">💬</span>
            {!isMobile && <span style={S.fabText}>Hỏi AI</span>}
          </>
        )}
      </button>
    </>
  );
}

function Bubble({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div
        style={{
          ...S.bubble,
          ...(isUser ? S.userBubble : S.agentBubble),
          ...(msg.error ? S.errorBubble : null),
        }}
      >
        {msg.text}
        {msg.analysis && (
          <details style={S.analysis}>
            <summary style={S.analysisSum}>Xem phân tích</summary>
            <div style={S.analysisBody}>{msg.analysis}</div>
          </details>
        )}
      </div>
    </div>
  );
}

const S = {
  fab: {
    position: "fixed", right: 20, bottom: 20, zIndex: 60,
    display: "inline-flex", alignItems: "center", gap: space(2),
    background: color.accent, color: "#fff",
    border: "none", borderRadius: radius.pill,
    padding: "12px 18px", fontSize: size.small, fontWeight: 600,
    fontFamily: "inherit", cursor: "pointer",
    boxShadow: "0 6px 20px rgba(31,95,168,.32)",
  },
  fabIcon: { fontSize: 16, lineHeight: 1 },
  fabText: { whiteSpace: "nowrap" },

  panel: {
    position: "fixed", right: 20, bottom: 84, zIndex: 60,
    background: color.surface, border: `1px solid ${color.border}`,
    borderRadius: radius.lg, boxShadow: "0 12px 40px rgba(16,24,32,.18)",
    display: "flex", flexDirection: "column", overflow: "hidden",
  },
  head: {
    display: "flex", justifyContent: "space-between", alignItems: "flex-start",
    padding: `${space(3)}px ${space(4)}px`, borderBottom: `1px solid ${color.border}`,
    background: color.canvas,
  },
  headTitle: { fontSize: size.small, fontWeight: 700, color: color.ink },
  headSub: { fontSize: 10, color: color.muted, display: "flex", alignItems: "center", gap: 5, marginTop: 2 },
  dot: { width: 6, height: 6, borderRadius: "50%", background: color.ok, display: "inline-block" },
  close: { background: "transparent", border: "none", color: color.muted, cursor: "pointer", fontSize: 14, padding: 2 },

  body: {
    flex: 1, overflowY: "auto", padding: space(4),
    display: "flex", flexDirection: "column", gap: space(3),
  },
  bubble: {
    maxWidth: "85%", padding: `${space(2)}px ${space(3)}px`,
    borderRadius: radius.md, fontSize: size.small, lineHeight: 1.55,
    whiteSpace: "pre-wrap", wordBreak: "break-word",
  },
  userBubble: { background: color.accent, color: "#fff", borderBottomRightRadius: 3 },
  agentBubble: { background: color.canvas, color: color.body, borderBottomLeftRadius: 3 },
  errorBubble: { background: color.dangerSoft, color: color.danger },

  analysis: { marginTop: space(2), borderTop: `1px solid ${color.border}`, paddingTop: space(2) },
  analysisSum: { fontSize: size.tiny, color: color.muted, cursor: "pointer", fontWeight: 600 },
  analysisBody: { fontSize: size.tiny, color: color.muted, marginTop: space(1), fontFamily: font.mono, lineHeight: 1.5 },

  suggestWrap: { display: "flex", flexDirection: "column", gap: space(2), marginTop: space(2) },
  suggest: {
    textAlign: "left", background: color.surface, border: `1px solid ${color.borderStrong}`,
    borderRadius: radius.sm, padding: `${space(2)}px ${space(3)}px`,
    fontSize: size.tiny, color: color.accent, cursor: "pointer", fontFamily: "inherit",
  },

  inputRow: {
    display: "flex", gap: space(2), padding: space(3),
    borderTop: `1px solid ${color.border}`, background: color.surface,
  },
  input: {
    flex: 1, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm,
    padding: `${space(2)}px ${space(3)}px`, fontSize: size.small,
    fontFamily: "inherit", color: color.ink, outline: "none",
  },
  sendBtn: {
    background: color.accent, color: "#fff", border: "none",
    borderRadius: radius.sm, width: 38, cursor: "pointer",
    fontSize: 13, fontFamily: "inherit",
  },
};
