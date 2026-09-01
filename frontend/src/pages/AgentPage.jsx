import React, { useEffect, useRef, useState } from "react";
import { chatWithAgent, getAgentSession } from "../api/endpoints";
import SafeMarkdown from "../components/SafeMarkdown";
import { Logomark } from "../components/Brand";

const suggestions = ["5 căn nào nên tập trung ở La Pura?", "Khu vực nào đang có tín hiệu tốt nhất?", "Vì sao căn đầu danh sách được ưu tiên?", "Tôi nên giao việc gì cho sales hôm nay?"];
const SESSION_KEY = "absorpiq-agent-session";

export default function AgentPage() {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState(() => { try { return window.localStorage.getItem(SESSION_KEY); } catch { return null; } });
  const endRef = useRef(null);
  useEffect(() => {
    if (!sessionId) return undefined;
    let active = true;
    getAgentSession(sessionId).then((result) => { if (active && result?.messages?.length) setMessages(result.messages); }).catch(() => {});
    return () => { active = false; };
  }, [sessionId]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  async function send(value = text) {
    const message = value.trim(); if (!message || busy) return;
    setText(""); setBusy(true); setMessages((items) => [...items, { role: "user", content: message }]);
    try { const result = await chatWithAgent(message, "P-0001", sessionId); if (result.session_id) { setSessionId(result.session_id); try { window.localStorage.setItem(SESSION_KEY, result.session_id); } catch {} } setMessages((items) => [...items, { role: "assistant", content: result.response || "Agent chưa có câu trả lời." }]); }
    catch (error) { setMessages((items) => [...items, { role: "assistant", error: true, content: error.message || "Không thể kết nối Agent." }]); }
    finally { setBusy(false); }
  }
  function reset() { setMessages([]); setSessionId(null); try { window.localStorage.removeItem(SESSION_KEY); } catch {} }
  return <main style={S.page}><header style={S.header}><div><div style={S.eyebrow}>ABSORPIQ · TRỢ LÝ KINH DOANH</div><h1 style={S.title}>Trợ lý kinh doanh</h1><p style={S.subtitle}>Hỗ trợ đội ngũ xác định căn hộ và khu vực nên tập trung.</p></div><button onClick={reset} style={S.newChat}>＋ Cuộc trò chuyện mới</button></header>
    <section style={S.card}><div style={S.messages}>{!messages.length && <div style={S.welcome}><div style={S.logo}><Logomark size={54} /></div><h2>Anh/chị muốn tìm hiểu điều gì?</h2><p>Hỏi về căn hộ, phân khu, lượng hàng còn lại hoặc việc nên ưu tiên cho đội kinh doanh.</p><div style={S.suggestions}>{suggestions.map((item) => <button key={item} onClick={() => send(item)} style={S.suggestion}>{item}</button>)}</div></div>}
      {messages.map((item, index) => <div key={index} style={{ ...S.row, justifyContent: item.role === "user" ? "flex-end" : "flex-start" }}><div style={item.role === "user" ? S.user : { ...S.agent, ...(item.error ? S.error : {}) }}>{item.role === "assistant" ? <SafeMarkdown>{item.content}</SafeMarkdown> : item.content}</div></div>)}
      {busy && <div style={S.row}><div style={S.thinking}>Đang suy nghĩ…</div></div>}<div ref={endRef} /></div>
      <form style={S.composer} onSubmit={(event) => { event.preventDefault(); send(); }}><textarea style={S.input} value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} placeholder="Anh/chị muốn hỏi điều gì?" rows={2} /><button disabled={busy || !text.trim()} style={S.send}>Gửi</button></form><div style={S.note}>Thông tin mang tính tham khảo; mọi đề xuất cần được người phụ trách xem xét trước khi triển khai.</div></section></main>;
}
const S = { page:{maxWidth:1040,margin:"0 auto",padding:"8px 0 48px"},header:{display:"flex",justifyContent:"space-between",alignItems:"flex-start",gap:18,marginBottom:22},eyebrow:{fontSize:11,letterSpacing:1.4,color:"#a07b1d",fontWeight:800},title:{margin:"7px 0",fontSize:34,color:"#1f1f1f"},subtitle:{margin:0,color:"#747474"},newChat:{border:"1px solid #ded9c9",background:"#fff",borderRadius:12,padding:"11px 15px",cursor:"pointer",color:"#5f4d18"},card:{background:"#fff",border:"1px solid #e7e3d9",borderRadius:22,boxShadow:"0 14px 45px rgba(48,39,15,.08)",overflow:"hidden"},messages:{minHeight:"calc(100vh - 390px)",padding:"28px 30px"},welcome:{maxWidth:700,margin:"80px auto",textAlign:"center",color:"#656565"},logo:{width:54,height:54,margin:"0 auto 18px",display:"grid",placeItems:"center",borderRadius:17,background:"#f5ebc7",color:"#ae8425",fontSize:30},suggestions:{display:"flex",flexWrap:"wrap",justifyContent:"center",gap:9,marginTop:26},suggestion:{border:"1px solid #eadcae",background:"#fffdf5",borderRadius:999,padding:"10px 14px",color:"#6b571d",cursor:"pointer"},row:{display:"flex",marginBottom:18},user:{maxWidth:"78%",padding:"12px 17px",background:"#1d1d1d",color:"white",borderRadius:"18px 18px 4px 18px",lineHeight:1.5},agent:{maxWidth:"88%",padding:"12px 18px",background:"#f7f7f4",color:"#292929",borderRadius:"18px 18px 18px 4px"},thinking:{display:"flex",gap:10,alignItems:"center",padding:"11px 16px",color:"#806b2d",background:"#fffaf0",border:"1px solid #f0e4bf",borderRadius:"18px 18px 18px 4px"},spark:{color:"#b78928"},dots:{letterSpacing:3,color:"#b78928"},error:{background:"#fff0f0",color:"#a33"},composer:{display:"flex",alignItems:"stretch",gap:12,padding:18,borderTop:"1px solid #eeeae1"},input:{flex:1,minWidth:0,resize:"none",border:"1px solid #d9d4c8",borderRadius:13,padding:"13px 15px",font:"inherit",lineHeight:1.45,outline:"none"},send:{border:0,borderRadius:13,background:"#b78928",color:"white",fontWeight:700,padding:"0 25px",cursor:"pointer"},note:{textAlign:"center",fontSize:12,color:"#888",padding:"0 16px 18px"}};
