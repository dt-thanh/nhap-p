import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  addMarketPhase,
  changeMarketPhase,
  chatWithAgent,
  decideMarketProposal,
  generateMarketProposal,
  getMarketAudit,
  getMarketDashboard,
  getMarketPolicies,
  getMarketProposals,
  getMarketScenarios,
  getMarketUnits,
  runMarketScenario,
} from '../api/endpoints';
import './MarketPrototypePage.css';
import './MarketPrototypeEnhancements.css';

const STATES = ['Còn trống', 'Booking', 'Đã giao dịch', 'Tạm khóa'];
const cls = { 'Còn trống': 'free', Booking: 'book', 'Đã giao dịch': 'sold', 'Tạm khóa': 'locked' };
const PAGE_SIZE = 12;
const aiStore = { question: '', answer: '', error: '', thinking: false, promise: null };
const seed = () => [];

const Icon = ({ children }) => <span className="p-icon">{children}</span>;
const Pill = ({ status }) => <span className={`p-pill ${cls[status]}`}>{status}</span>;
const Score = ({ n, t }) => <span className={`p-score ${n >= 75 ? 'hot' : n < 40 ? 'low' : ''}`}><b>{n}</b><small>{t > 0 ? '↗' : '↘'} {Math.abs(t)}</small></span>;
const stat = (items, status) => items.filter((u) => u.status === status).length;

function mergeUnits(current, activeUnits) {
  const byId = new Map(activeUnits.map((unit) => [unit.id, unit]));
  return current.map((unit) => byId.get(unit.id) || unit);
}

function markdownLite(text) {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, i) => {
      if (line.startsWith('### ')) return <h3 key={i}>{line.slice(4)}</h3>;
      if (line.startsWith('## ')) return <h3 key={i}>{line.slice(3)}</h3>;
      if (/^[-*]\s+/.test(line)) return <li key={i}>{line.replace(/^[-*]\s+/, '')}</li>;
      if (/^\d+[.)]\s+/.test(line)) return <p key={i}><b>{line.match(/^\d+/)?.[0]}.</b> {line.replace(/^\d+[.)]\s+/, '')}</p>;
      return <p key={i}>{line}</p>;
    });
}

export default function MarketPrototypePage({ view = 'home' }) {
  const navigate = useNavigate();
  const tab = view;
  const [units, setUnits] = useState(seed);
  const [filter, setFilter] = useState('Tất cả');
  const [search, setSearch] = useState('');
  const [modal, setModal] = useState(false);
  const [phaseIndex, setPhaseIndex] = useState(0);
  const [phases, setPhases] = useState([]);
  const [scenarios, setScenarios] = useState([]);
  const [scenarioId, setScenarioId] = useState('buying_wave');
  const [policy, setPolicy] = useState(null);
  const [proposals, setProposals] = useState([]);
  const [selectedProposalId, setSelectedProposalId] = useState('');
  const [selectedUnits, setSelectedUnits] = useState([]);
  const [proposalPrompt, setProposalPrompt] = useState('Phân tích thời điểm hiện tại và đề xuất danh sách căn nên đưa vào đợt mở bán kế tiếp.');
  const [proposalLoading, setProposalLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [aiState, setAiState] = useState({ ...aiStore });
  const [logs, setLogs] = useState([]);

  const currentPhase = phases[phaseIndex] || { id: 'not_started', label: 'Chưa triển khai', kind: 'idle', release: null };
  const active = currentPhase.kind === 'idle' ? units : units.filter((u) => u.release === currentPhase.release);
  const activeDone = stat(active, 'Đã giao dịch');
  const activeBooking = stat(active, 'Booking');
  const activeLocked = stat(active, 'Tạm khóa');
  const activeAvailable = stat(active, 'Còn trống');
  const rate = active.length ? Math.round(activeDone / active.length * 100) : 0;
  const bookingRate = active.length ? Math.round(activeBooking / active.length * 100) : 0;
  const avgSignal = active.length ? Math.round(active.reduce((sum, unit) => sum + Number(unit.score || 0), 0) / active.length) : 0;
  const count = useMemo(() => Object.fromEntries(STATES.map((s) => [s, units.filter((u) => u.status === s).length])), [units]);
  const shown = units.filter((u) => (filter === 'Tất cả' || u.status === filter) && (!search || `${u.id} ${u.tower}`.toLowerCase().includes(search.toLowerCase())));
  const allowedScenarios = scenarios.filter((s) => s.phase === currentPhase.kind);
  const pageTotal = Math.max(1, Math.ceil(active.length / PAGE_SIZE));
  const activePage = active.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const selectedProposal = proposals.find((p) => p.id === selectedProposalId) || proposals[0];

  useEffect(() => {
    Promise.all([getMarketDashboard(), getMarketUnits(), getMarketScenarios(), getMarketPolicies(), getMarketProposals()])
      .then(([d, u, s, p, pr]) => {
        setPhases(d.phases);
        setPhaseIndex(d.phase_index);
        setUnits(u.items);
        setScenarios(s.items);
        setPolicy(p);
        setProposals(pr.items || []);
        const first = pr.items?.[0];
        if (first) {
          setSelectedProposalId(first.id);
          setSelectedUnits(first.recommended_unit_ids || []);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => setPage(1), [phaseIndex]);
  useEffect(() => {
    if (allowedScenarios.length && !allowedScenarios.some((s) => s.id === scenarioId)) setScenarioId(allowedScenarios[0].id);
  }, [allowedScenarios, scenarioId]);
  useEffect(() => {
    const timer = setInterval(() => setAiState({ ...aiStore }), 500);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (tab === 'log') getMarketAudit().then((r) => setLogs((r.items || []).map((x) => [new Date(x.created_at).toLocaleString('vi-VN'), x.actor, x.action, x.source]))).catch(() => {});
  }, [tab]);

  const refreshMarket = async () => {
    const [d, u, p, pr] = await Promise.all([getMarketDashboard(), getMarketUnits(), getMarketPolicies(), getMarketProposals()]);
    setPhases(d.phases);
    setPhaseIndex(d.phase_index);
    setUnits(u.items);
    setPolicy(p);
    setProposals(pr.items || []);
  };

  const moveNextPhase = async () => {
    const target = phaseIndex + 1;
    if (target >= phases.length) return;
    if (!window.confirm(`Xác nhận chuyển dự án sang “${phases[target].label}”?`)) return;
    const d = await changeMarketPhase('next', true);
    setPhaseIndex(d.phase_index);
    setUnits((value) => mergeUnits(value, d.active_units));
  };

  const addPhase = async (kind) => {
    if (!window.confirm(`Thêm ${kind === 'booking' ? 'đợt đặt chỗ' : 'đợt mở bán'} mới vào thanh trạng thái?`)) return;
    const r = await addMarketPhase(kind, true);
    setPhases(r.phases);
  };

  const run = async () => {
    if (!window.confirm('Xác nhận chạy kịch bản mô phỏng trên dữ liệu demo?')) return;
    try {
      const r = await runMarketScenario(scenarioId, 40, true);
      setUnits((value) => mergeUnits(value, r.snapshot.active_units));
      setModal(false);
    } catch (e) {
      alert(typeof e.message === 'string' ? e.message : 'Không thể chạy kịch bản');
    }
  };

  const generateProposal = async () => {
    if (!proposalPrompt.trim()) return;
    setProposalLoading(true);
    try {
      const proposal = await generateMarketProposal(proposalPrompt);
      setProposals((value) => [proposal, ...value]);
      setSelectedProposalId(proposal.id);
      setSelectedUnits(proposal.recommended_unit_ids || []);
    } finally {
      setProposalLoading(false);
    }
  };

  const approveProposal = async () => {
    if (!selectedProposal) return;
    if (!window.confirm('Phê duyệt đề xuất này và cập nhật database mô phỏng?')) return;
    await decideMarketProposal(selectedProposal.id, 'approved', 'Admin phê duyệt sau khi custom danh sách căn', true, selectedUnits);
    await refreshMarket();
  };

  const ask = async () => {
    if (!aiState.question.trim() || aiStore.thinking) return;
    aiStore.thinking = true;
    aiStore.error = '';
    aiStore.answer = '';
    aiStore.promise = chatWithAgent(aiStore.question)
      .then((r) => { aiStore.answer = r.response; })
      .catch((e) => { aiStore.error = e.body?.detail?.message || 'Không thể kết nối AI lúc này.'; })
      .finally(() => { aiStore.thinking = false; aiStore.promise = null; setAiState({ ...aiStore }); });
    setAiState({ ...aiStore });
  };

  const setQuestion = (value) => {
    aiStore.question = value;
    setAiState({ ...aiStore });
  };

  const toggleUnit = (id) => setSelectedUnits((value) => value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);

  return <div className="prototype">
    <header className="p-head"><div><span className="p-overline"><i /> DỮ LIỆU MÔ PHỎNG · CẬP NHẬT GẦN THỜI GIAN THỰC</span><h1>{tab === 'stock' ? 'Giỏ hàng toàn dự án' : tab === 'ai' ? 'AI Agent' : 'Saigon Riverside'}</h1><p>{tab === 'home' ? 'Trung tâm điều hành mở bán và phân tích sức hấp thụ' : tab === 'stock' ? '100 căn hộ · dữ liệu mô phỏng' : 'Gemini phân tích dữ liệu, đề xuất và chờ Admin phê duyệt'}</p></div>{tab === 'home' && <span className="phase-badge">{currentPhase.label}</span>}</header>

    {tab === 'home' && <section className="phase-control next-only"><div className="phase-track">{phases.map((p, i) => <div key={p.id} className={`${i === phaseIndex ? 'current ' : ''}${i < phaseIndex ? 'done' : ''}`}><i>{i < phaseIndex ? '✓' : i + 1}</i><span>{p.label}</span></div>)}</div><button title="Chuyển sang sự kiện tiếp theo" disabled={phaseIndex === phases.length - 1} onClick={moveNextPhase}>✓</button></section>}

    {tab === 'home' && <>
      <section className="p-hero"><div><small>GIAI ĐOẠN HIỆN TẠI</small><h2>{currentPhase.label}</h2><p>{active.length} căn thuộc giỏ của giai đoạn này</p></div><div className="p-progress"><b>{rate}% <small>Tỷ lệ giao dịch</small></b><i><span style={{ width: `${rate}%` }} /></i><small>Booking hiện tại: {bookingRate}%</small></div><button disabled={!allowedScenarios.length} onClick={() => setModal(true)}>▶ Mô phỏng kịch bản</button></section>
      <section className="p-columns dashboard-grid">
        <article className="p-card"><div className="p-cardhead"><div><small>GIỎ GIAI ĐOẠN HIỆN TẠI</small><h3>{currentPhase.label} <i>{active.length} căn</i></h3></div><button onClick={() => navigate('/inventory')}>Toàn bộ giỏ →</button></div><Table units={activePage} full /><Pager page={page} total={pageTotal} setPage={setPage} /></article>
        <article className="p-card signals"><div className="p-cardhead"><div><small>TÍN HIỆU VẬN HÀNH</small><h3>Thống kê trong đợt</h3></div><em>Mô phỏng</em></div><p><span><b>Booking rate</b><small>Tỷ lệ căn đang có khách giữ chỗ</small></span><strong>{bookingRate}%</strong></p><p><span><b>Conversion rate</b><small>Tỷ lệ đã giao dịch trên giỏ đợt này</small></span><strong>{rate}%</strong></p><p><span><b>Còn trống</b><small>Sản phẩm có thể phân bổ chính sách</small></span><strong>{activeAvailable}</strong></p><p className={activeLocked ? 'danger' : ''}><span><b>Tạm khóa</b><small>Chưa đưa vào vận hành ở giai đoạn này</small></span><strong>{activeLocked}</strong></p><p><span><b>Tín hiệu quan tâm TB</b><small>Dựa trên score mô phỏng từng căn</small></span><strong>{avgSignal}</strong></p><button className="p-ai-link" onClick={() => navigate('/ai-agent')}>✦ Mở AI Agent phân tích</button></article>
      </section>
    </>}

    {tab === 'stock' && <section className="p-card stock"><div className="p-cardhead"><div><small>GIỎ HÀNG DỰ ÁN</small><h3>Danh sách căn hộ <i>{shown.length}</i></h3></div><input placeholder="Tìm mã căn, tòa..." value={search} onChange={(e) => setSearch(e.target.value)} /></div><div className="p-filters">{['Tất cả', ...STATES].map((s) => <button className={filter === s ? 'active' : ''} onClick={() => setFilter(s)} key={s}>{s}{s !== 'Tất cả' && ` · ${count[s]}`}</button>)}</div><Table units={shown.slice(0,18)} full /><footer>Hiển thị {Math.min(18, shown.length)} / {shown.length} căn · Dữ liệu mô phỏng</footer></section>}

    {tab === 'ai' && <section className="p-ai-grid ai-wide"><article className="p-card assistant"><div className="p-orb">✦</div><small>GEMINI MARKET AGENT</small><h2>Hỏi AI về thị trường</h2><div className="prompts"><button onClick={() => setQuestion('Tóm tắt tình hình phase hiện tại và đề xuất hành động')}>Tóm tắt phase</button><button onClick={() => setQuestion('Nhóm căn nào nên ưu tiên mở bán?')}>Ưu tiên mở bán</button><button onClick={() => setQuestion('Chính sách chiết khấu hiện tại có điểm nào cần chỉnh?')}>Rà chính sách</button></div><div className="ask"><input placeholder="Hỏi về thị trường hoặc giỏ hàng..." value={aiState.question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ask()} /><button disabled={aiState.thinking} onClick={ask}>{aiState.thinking ? 'Đang nghĩ…' : 'Gửi ↑'}</button></div>{aiState.thinking && <div className="thinking"><i /><span>Đang đọc dữ liệu · Phân tích tín hiệu · Soạn câu trả lời</span></div>}{aiState.answer && <div className="ai-answer formatted">{markdownLite(aiState.answer)}</div>}{aiState.error && <div className="ai-error">{aiState.error}</div>}<small>AI không tự thực thi thay đổi giá, chiết khấu hoặc chính sách</small></article>
        <article className="p-card proposal"><div className="proposal-top"><em>ADMIN ĐỀ XUẤT / AI PHÂN TÍCH</em><small>Policy v{policy?.version || 1}</small></div><h2>Tạo đề xuất vận hành</h2><textarea className="proposal-input" value={proposalPrompt} onChange={(e) => setProposalPrompt(e.target.value)} /><div className="actions"><button onClick={() => addPhase('booking')}>Thêm đặt chỗ</button><button onClick={() => addPhase('sale')}>Thêm mở bán</button><button className="primary" disabled={proposalLoading} onClick={generateProposal}>{proposalLoading ? 'Đang phân tích…' : 'AI phân tích đề xuất'}</button></div>{selectedProposal && <ProposalEditor proposal={selectedProposal} units={units} selected={selectedUnits} toggleUnit={toggleUnit} approve={approveProposal} />}</article></section>}

    {tab === 'log' && <section className="p-card audit"><div className="p-cardhead"><div><small>AUDIT TRAIL · APPROVED ONLY</small><h3>Nhật ký phê duyệt Admin</h3></div><b>Có thể truy vết</b></div>{logs.length ? logs.map((l, i) => <article key={i}><i /><time>{l[0]}</time><span><b>{l[2]}</b><small>{l[1]} · {l[3]}</small></span></article>) : <p className="empty-log">Chưa có quyết định phê duyệt nào.</p>}</section>}

    {modal && <div className="p-backdrop" onMouseDown={() => setModal(false)}><section className="p-modal" onMouseDown={(e) => e.stopPropagation()}><div className="p-orb">▶</div><small>REGISTRY KỊCH BẢN · {currentPhase.label}</small><h2>Chọn tình huống mô phỏng</h2><p>Chỉ các kịch bản phù hợp giai đoạn hiện tại được phép chạy.</p><div className="scenario-list">{allowedScenarios.map((s) => <button key={s.id} className={scenarioId === s.id ? 'active' : ''} onClick={() => setScenarioId(s.id)}><b>{s.name}</b><span>{s.description}</span></button>)}</div><div className="modal-data"><span>Giỏ hiện tại<b>{active.length} căn</b></span><span>Cường độ<b>40%</b></span><span>Chế độ<b>Demo an toàn</b></span></div><div className="actions"><button onClick={() => setModal(false)}>Hủy</button><button className="primary" disabled={!allowedScenarios.length} onClick={run}>▶ Chạy mô phỏng</button></div></section></div>}
  </div>;
}

function ProposalEditor({ proposal, units, selected, toggleUnit, approve }) {
  const recommended = units.filter((u) => (proposal.recommended_unit_ids || []).includes(u.id));
  return <div className="proposal-editor"><h3>{proposal.title}</h3><p>{proposal.summary}</p><div className="evidence">{(proposal.evidence || []).map((item) => <span key={item}>{item}<b>{proposal.discount_percent || 1}%</b><small>Chiết khấu đề xuất</small></span>)}</div><div className="unit-picker"><div><b>Admin custom danh sách căn</b><small>{selected.length} căn sẽ được áp dụng khi phê duyệt</small></div>{recommended.map((u) => <label key={u.id}><input type="checkbox" checked={selected.includes(u.id)} onChange={() => toggleUnit(u.id)} /><span><b>{u.id}</b>{u.type} · {u.view} · {u.price} tỷ · {u.status}</span></label>)}</div><div className="actions"><button className="primary" onClick={approve}>Phê duyệt & cập nhật database</button></div></div>;
}

function Pager({ page, total, setPage }) {
  if (total <= 1) return null;
  return <div className="pager"><button disabled={page === 1} onClick={() => setPage(page - 1)}>‹</button>{Array.from({ length: total }, (_, i) => i + 1).map((n) => <button key={n} className={n === page ? 'active' : ''} onClick={() => setPage(n)}>{n}</button>)}<button disabled={page === total} onClick={() => setPage(page + 1)}>›</button></div>;
}

function Table({ units, title, full }) {
  return <div className={title ? 'p-card p-table' : 'p-table'}>{title && <div className="p-cardhead"><div><small>{title}</small><h3>Biến động giỏ hàng</h3></div><b className="live">● Gần thời gian thực</b></div>}<div className="table-scroll"><table><thead><tr><th>Căn</th><th>Sản phẩm</th>{full && <th>Tòa / Tầng</th>}<th>Giá</th><th>Interest score</th><th>Trạng thái</th></tr></thead><tbody>{units.map((u) => <tr key={u.id}><td><b>{u.id}</b></td><td>{u.type} · {u.area}m² · {u.view}</td>{full && <td>{u.tower} · T{u.floor}</td>}<td><b>{u.price} tỷ</b></td><td><Score n={u.score} t={u.trend} /></td><td><Pill status={u.status} /></td></tr>)}</tbody></table></div></div>;
}
