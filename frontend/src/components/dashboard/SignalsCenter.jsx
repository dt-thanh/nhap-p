// frontend/src/components/dashboard/SignalsCenter.jsx
// Signals Center — phạm vi DANH MỤC (trang /overview).
//
// Mọi tín hiệu trả lời đủ BỐN câu hỏi: chuyện gì đã xảy ra, vì sao quan trọng,
// bằng chứng ở đâu, nên làm gì. Luật suy ra nằm ở `src/utils/signals.js`
// (thuần, test riêng); file này chỉ trình bày.
//
// CHỈ ĐỌC: hệ thống chưa có nơi lưu trạng thái tín hiệu, nên không có nút
// Acknowledge/Resolve. Xem SIGNAL_PERSISTENCE_SUPPORTED trong utils/signals.js.
//
// Không dựa vào MÀU để phân biệt mức độ: mỗi tín hiệu có nhãn chữ + ký hiệu
// (▲ ● ■) + aria-label, để đọc được khi mù màu hoặc dùng trình đọc màn hình.
import React, { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  CATEGORIES,
  CATEGORY_LABEL,
  CONFIDENCE_LABEL,
  HOUSEKEEPING_LAYER_LABEL,
  LAYER_LABEL,
  LAYER_POLICY_NOTE,
  LAYER_SHORT_LABEL,
  SEVERITY_LABEL,
  SIGNAL_PERSISTENCE_SUPPORTED,
  STATUS_LABEL,
  THRESHOLD_STATUS_LABEL,
  UNAVAILABLE_RULES,
  filterSignals,
  summarizeSignals,
} from "../../utils/signals";
import { color, radius, size, space } from "../../styles/tokens";
import { EmptyState, ErrorState, Skeleton } from "../ui/States";

const SCOPE_LABEL = { portfolio: "Danh mục", project: "Dự án", area: "Phân khu", unit: "Căn" };

const SEVERITY_STYLE = {
  critical: { mark: "▲", fg: color.danger, bg: color.dangerSoft },
  warning: { mark: "●", fg: color.warn, bg: color.warnSoft },
  info: { mark: "■", fg: color.muted, bg: color.canvas },
};

function severityStyle(severity) {
  return SEVERITY_STYLE[severity] || SEVERITY_STYLE.info;
}

/** Nhãn tầng. `layer === null` là VẬN HÀNH NỘI BỘ, không phải "tầng 0" — không
 *  đặt nó vào thang tầng nghiệp vụ để khỏi ngụ ý một mức ưu tiên không có thật. */
function layerChip(layer) {
  if (layer === 1 || layer === 2 || layer === 3) {
    return { short: LAYER_SHORT_LABEL[layer], full: LAYER_LABEL[layer] };
  }
  return { short: HOUSEKEEPING_LAYER_LABEL, full: HOUSEKEEPING_LAYER_LABEL };
}

export default function SignalsCenter({ signals = [], loading = false, error = null, onRetry }) {
  const [severity, setSeverity] = useState("all");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [expanded, setExpanded] = useState(null);

  const summary = useMemo(() => summarizeSignals(signals), [signals]);
  const visible = useMemo(() => filterSignals(signals, { severity, status, category }), [signals, severity, status, category]);

  return (
    <section style={S.card} aria-labelledby="signals-center-title">
      <div style={S.header}>
        <div>
          <div style={S.eyebrow}>CROSS-PROJECT SIGNALS</div>
          <h2 id="signals-center-title" style={S.title}>Tín hiệu cần chú ý</h2>
        </div>
        <span style={S.scopeBadge}>Phạm vi danh mục</span>
      </div>

      {loading ? (
        <div data-testid="signals-loading" style={S.loading} aria-busy="true" aria-live="polite">
          <Skeleton width="82%" height={18} />
          <Skeleton width="64%" height={18} />
          <Skeleton width="72%" height={18} />
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={onRetry} compact />
      ) : (
        <>
          <dl style={S.counts} aria-label="Tổng hợp tín hiệu">
            <Count label="Nghiêm trọng" value={summary.critical} tone={color.danger} testid="count-critical" />
            <Count label="Cảnh báo" value={summary.warning} tone={color.warn} testid="count-warning" />
            <Count label="Đang mở" value={summary.open} tone={color.accent} testid="count-open" />
            <Count label={LAYER_SHORT_LABEL[1]} value={summary.layer1} tone={color.muted} testid="count-layer-1" />
            <Count label={LAYER_SHORT_LABEL[2]} value={summary.layer2} tone={color.muted} testid="count-layer-2" />
            {CATEGORIES.map((c) => (
              <Count key={c} label={CATEGORY_LABEL[c]} value={summary.byCategory[c] || 0}
                tone={color.muted} testid={`count-category-${c}`} />
            ))}
          </dl>

          <div style={S.filters}>
            <Select id="signal-filter-severity" label="Mức độ" value={severity} onChange={setSeverity}
              options={[["all", "Tất cả mức độ"], ["critical", SEVERITY_LABEL.critical], ["warning", SEVERITY_LABEL.warning], ["info", SEVERITY_LABEL.info]]} />
            <Select id="signal-filter-status" label="Trạng thái" value={status} onChange={setStatus}
              options={[["all", "Tất cả trạng thái"], ...Object.entries(STATUS_LABEL)]} />
            <Select id="signal-filter-category" label="Nhóm tín hiệu" value={category} onChange={setCategory}
              options={[["all", "Tất cả nhóm"], ...CATEGORIES.map((c) => [c, CATEGORY_LABEL[c]])]} />
          </div>

          <p style={S.policy} data-testid="signals-priority-policy">
            <strong>Thứ tự ưu tiên:</strong> {LAYER_POLICY_NOTE}
          </p>

          {visible.length === 0 ? (
            signals.length === 0 ? (
              <EmptyState title="Không có tín hiệu nào" hint="Các nguồn được theo dõi hiện không phát hiện vấn đề nào trong phạm vi này." compact />
            ) : (
              <p style={S.muted} data-testid="signals-filtered-empty">
                Không có tín hiệu nào khớp bộ lọc đang chọn.
              </p>
            )
          ) : (
            <ul style={S.list} data-testid="signals-list">
              {visible.map((sig) => (
                <SignalRow key={sig.id} signal={sig} expanded={expanded === sig.id}
                  onToggle={() => setExpanded(expanded === sig.id ? null : sig.id)} />
              ))}
            </ul>
          )}

          {!SIGNAL_PERSISTENCE_SUPPORTED && (
            <p style={S.notice} data-testid="signals-readonly-notice">
              Chỉ đọc: hệ thống chưa có nơi lưu trạng thái tín hiệu, nên chưa có thao tác ghi nhận / xử lý / bỏ qua.
            </p>
          )}

          <details style={S.limits}>
            <summary style={S.limitsSummary}>Luật chưa dựng được ({UNAVAILABLE_RULES.length})</summary>
            <ul style={S.limitsList}>
              {UNAVAILABLE_RULES.map((item) => (
                <li key={item.rule} style={S.limitsItem}>
                  <strong>{item.rule}:</strong> {item.reason}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </section>
  );
}

function Count({ label, value, tone, testid }) {
  return (
    <div style={S.count}>
      <dt style={S.countLabel}>{label}</dt>
      <dd style={{ ...S.countValue, color: tone }} data-testid={testid}>{value}</dd>
    </div>
  );
}

function Select({ id, label, value, onChange, options }) {
  return (
    <span style={S.filter}>
      <label htmlFor={id} style={S.filterLabel}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} style={S.select}>
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </span>
  );
}

function SignalRow({ signal, expanded, onToggle, nested = false }) {
  const tone = severityStyle(signal.severity);
  const sevText = SEVERITY_LABEL[signal.severity] || signal.severity;
  const statusText = STATUS_LABEL[signal.status] || signal.status;
  const confText = CONFIDENCE_LABEL[signal.confidence] || signal.confidence;
  const layer = layerChip(signal.layer);
  const ev = signal.evidence || {};
  const children = Array.isArray(signal.children) ? signal.children : [];
  const isParent = children.length > 0;
  const thresholdText = ev.thresholdStatus ? THRESHOLD_STATUS_LABEL[ev.thresholdStatus] || ev.thresholdStatus : null;

  // Nhãn cho trình đọc màn hình: gói TOÀN BỘ trục phân loại vào một câu, để
  // người dùng không phải suy ra mức độ từ màu hay từ vị trí trong danh sách.
  const srSummary =
    `Mức độ ${sevText}. Độ tin cậy ${confText}. ${layer.full}. ` +
    `Phạm vi ${SCOPE_LABEL[signal.scope] || signal.scope}. Trạng thái ${statusText}.` +
    (thresholdText ? ` ${thresholdText}.` : "") +
    (isParent ? ` Tín hiệu gộp: ${signal.affectedProjectCount} dự án bị ảnh hưởng.` : "");

  return (
    <li
      style={nested ? { ...S.row, ...S.childRow } : S.row}
      data-testid={`signal-${signal.id}`}
      data-severity={signal.severity}
      data-status={signal.status}
      data-category={signal.category}
      data-layer={signal.layer === null ? "none" : String(signal.layer)}
      data-attention-score={String(signal.attentionScore)}
      data-aggregate={isParent ? "true" : "false"}
    >
      <p style={S.srOnly} data-testid={`signal-a11y-${signal.id}`}>{srSummary}</p>

      <div style={S.rowHead}>
        <span style={{ ...S.badge, color: tone.fg, background: tone.bg }} aria-label={`Mức độ: ${sevText}`}>
          <span aria-hidden="true">{tone.mark}</span> {sevText}
        </span>
        {/* Độ tin cậy phải nằm NGAY ĐÂY, không chỉ trong phần chi tiết: nó là
            một trục phân loại ngang hàng với mức độ, không phải chú thích. */}
        <span style={S.confidenceChip} aria-label={`Độ tin cậy: ${confText}`} data-testid={`signal-confidence-${signal.id}`}>
          Tin cậy: {confText}
        </span>
        <span style={S.layerChip} aria-label={layer.full} data-testid={`signal-layer-${signal.id}`} title={layer.full}>
          {layer.short}
        </span>
        <span style={S.statusChip} aria-label={`Trạng thái: ${statusText}`}>{statusText}</span>
        <span style={S.typeChip} aria-label={`Nhóm: ${CATEGORY_LABEL[signal.category] || signal.category}`}>
          {CATEGORY_LABEL[signal.category] || signal.category}
        </span>
        <span style={S.typeChip}>{SCOPE_LABEL[signal.scope] || signal.scope}</span>
        {thresholdText && (
          <span
            style={{ ...S.typeChip, color: ev.thresholdStatus === "VERIFIED" ? color.muted : color.warn, fontWeight: 700 }}
            data-testid={`signal-threshold-chip-${signal.id}`}
          >
            {thresholdText}
          </span>
        )}
        {isParent && (
          <span style={S.aggregateChip} data-testid={`signal-aggregate-${signal.id}`}>
            {signal.affectedProjectCount} dự án bị ảnh hưởng
          </span>
        )}
        <button type="button" onClick={onToggle} aria-expanded={expanded}
          aria-controls={`signal-detail-${signal.id}`} style={S.toggle}>
          {signal.title}
        </button>
      </div>

      <p style={S.what}>{signal.whatHappened}</p>

      {expanded && (
        <div id={`signal-detail-${signal.id}`} style={S.detail}>
          <Question label="Chuyện gì đã xảy ra" text={signal.whatHappened} />
          <Question label="Vì sao quan trọng" text={signal.whyItMatters} />

          <div style={S.block}>
            <span style={S.qLabel}>Bằng chứng</span>
            <dl style={S.evidence}>
              <Fact label="Giá trị hiện tại" value={ev.currentValue} />
              <Fact label="Giá trị nền" value={ev.baselineValue} />
              <Fact label="Chênh lệch" value={ev.delta} />
              <Fact label="Mức" value={ev.band} />
              <Fact label="Ngưỡng" value={ev.threshold} />
              <Fact label="Điểm chú ý" value={ev.attentionScore === undefined ? null : `${ev.attentionScore} / 100`} />
              <Fact label="Số căn ảnh hưởng" value={signal.affectedUnits} />
              <Fact label="Dự án bị ảnh hưởng" value={isParent ? signal.affectedProjects.join(", ") : null} />
              <Fact label="Nguồn" value={ev.source} />
              <Fact label="Đường dẫn mã" value={ev.sourcePath} />
              <Fact label="Phát hiện lúc" value={ev.detectedAt} />
              <Fact label="Độ mới dữ liệu" value={ev.dataFreshness} />
              <Fact label="Mã dự án" value={ev.externalId || ev.projectId} />
              <Fact label="Mã phân khu" value={ev.areaId} />
              <Fact label="Mã căn" value={ev.unitId} />
            </dl>
            {ev.thresholdStatus && (
              <p
                style={{ ...S.provisional, color: ev.thresholdStatus === "VERIFIED" ? color.muted : color.warn }}
                data-testid={`signal-threshold-${signal.id}`}
              >
                {thresholdText} ({ev.thresholdStatus})
              </p>
            )}
            {Array.isArray(ev.details) && ev.details.length > 0 && (
              <ul style={S.details}>{ev.details.map((d, i) => <li key={i} style={S.detailItem}>{d}</li>)}</ul>
            )}
          </div>

          <Question label="Nên làm gì" text={signal.recommendedAction} />

          <div style={S.footer}>
            <span style={S.confidence}>Độ tin cậy: {confText}</span>
            <span style={S.confidence}>{layer.full}</span>
            {(signal.links || []).map((link) => (
              <Link key={link.href} to={link.href} style={S.link}>{link.label} →</Link>
            ))}
          </div>

          {/* Bằng chứng của từng dự án KHÔNG bị nén mất khi gộp: mỗi tín hiệu
              con vẫn là một hàng đầy đủ, mở rộng được, ngay trong phần chi
              tiết của tín hiệu cha. */}
          {isParent && (
            <div style={S.block} data-testid={`signal-children-${signal.id}`}>
              <span style={S.qLabel}>Bằng chứng theo từng dự án ({children.length})</span>
              <ul style={S.childList}>
                {children.map((child) => (
                  <ChildRow key={child.id} signal={child} />
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** Tín hiệu con có trạng thái mở/đóng RIÊNG, để mở cha không bung một bức tường
 *  bằng chứng của mọi dự án cùng lúc. */
function ChildRow({ signal }) {
  const [open, setOpen] = useState(false);
  return <SignalRow signal={signal} expanded={open} onToggle={() => setOpen(!open)} nested />;
}

function Question({ label, text }) {
  return (
    <div style={S.block}>
      <span style={S.qLabel}>{label}</span>
      <p style={S.qText}>{text}</p>
    </div>
  );
}

/** Giá trị vắng mặt hiển thị NGUYÊN TRẠNG là "Không có" — không đổi thành 0. */
function Fact({ label, value }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div style={S.fact}>
      <dt style={S.factLabel}>{label}</dt>
      <dd style={S.factValue}>{String(value)}</dd>
    </div>
  );
}

const S = {
  card: { minWidth: 0, background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(5), marginBottom: space(5) },
  header: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(3), marginBottom: space(4) },
  eyebrow: { color: color.muted, fontSize: 11, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", marginBottom: space(1) },
  title: { margin: 0, color: color.ink, fontSize: size.h2 },
  scopeBadge: { color: color.muted, background: color.canvas, borderRadius: radius.pill, padding: "4px 10px", fontSize: size.tiny, fontWeight: 700 },
  loading: { display: "grid", gap: space(3) },
  counts: { display: "flex", gap: space(4), margin: `0 0 ${space(4)}px`, flexWrap: "wrap" },
  count: { display: "flex", flexDirection: "column", gap: 2, minWidth: 96 },
  countLabel: { color: color.muted, fontSize: size.tiny, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".04em" },
  countValue: { margin: 0, fontSize: 26, fontWeight: 700, lineHeight: 1.1 },
  filters: { display: "flex", gap: space(3), flexWrap: "wrap", marginBottom: space(4) },
  filter: { display: "flex", flexDirection: "column", gap: 4 },
  filterLabel: { color: color.muted, fontSize: size.tiny, fontWeight: 700 },
  select: { border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "6px 10px", fontSize: size.tiny, background: color.surface, color: color.ink },
  list: { listStyle: "none", margin: 0, padding: 0, display: "grid", gap: space(2) },
  row: { borderTop: `1px solid ${color.border}`, padding: `${space(3)}px 0` },
  rowHead: { display: "flex", alignItems: "center", gap: space(2), flexWrap: "wrap" },
  badge: { flex: "none", padding: "3px 9px", borderRadius: radius.pill, fontSize: size.tiny, fontWeight: 700 },
  statusChip: { flex: "none", padding: "3px 9px", borderRadius: radius.pill, fontSize: size.tiny, fontWeight: 700, color: color.muted, background: color.canvas },
  typeChip: { flex: "none", fontSize: size.tiny, color: color.muted },
  toggle: { flex: 1, minWidth: 200, textAlign: "left", background: "transparent", border: "none", padding: 0, cursor: "pointer", color: color.ink, fontSize: size.small, fontWeight: 700, fontFamily: "inherit" },
  what: { margin: `${space(2)}px 0 0`, color: color.body, fontSize: size.small, lineHeight: 1.5 },
  detail: { marginTop: space(3), padding: space(4), background: color.canvas, borderRadius: radius.sm, display: "grid", gap: space(3) },
  block: { display: "grid", gap: 4 },
  qLabel: { color: color.muted, fontSize: size.tiny, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".04em" },
  qText: { margin: 0, color: color.body, fontSize: size.small, lineHeight: 1.55 },
  evidence: { margin: 0, display: "grid", gap: 4 },
  fact: { display: "flex", gap: space(2), fontSize: size.tiny, lineHeight: 1.5 },
  factLabel: { color: color.muted, minWidth: 132, flex: "none" },
  factValue: { margin: 0, color: color.body, wordBreak: "break-word" },
  provisional: { margin: `${space(2)}px 0 0`, color: color.warn, fontSize: size.tiny, fontWeight: 700 },
  details: { margin: `${space(2)}px 0 0`, paddingLeft: 18, display: "grid", gap: 3 },
  detailItem: { color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  footer: { display: "flex", gap: space(3), alignItems: "center", flexWrap: "wrap", borderTop: `1px solid ${color.border}`, paddingTop: space(3) },
  confidence: { color: color.muted, fontSize: size.tiny, fontWeight: 700 },
  confidenceChip: { flex: "none", padding: "3px 9px", borderRadius: radius.pill, fontSize: size.tiny, fontWeight: 700, color: color.body, background: color.canvas, border: `1px solid ${color.border}` },
  layerChip: { flex: "none", padding: "3px 9px", borderRadius: radius.pill, fontSize: size.tiny, fontWeight: 700, color: color.muted, background: color.canvas, border: `1px dashed ${color.borderStrong}` },
  aggregateChip: { flex: "none", padding: "3px 9px", borderRadius: radius.pill, fontSize: size.tiny, fontWeight: 700, color: color.accent, background: color.canvas, border: `1px solid ${color.accent}` },
  childRow: { borderTop: `1px dashed ${color.border}`, paddingLeft: space(3), marginLeft: space(2) },
  childList: { listStyle: "none", margin: 0, padding: 0, display: "grid", gap: space(1) },
  policy: { margin: `0 0 ${space(4)}px`, color: color.muted, fontSize: size.tiny, lineHeight: 1.55 },
  srOnly: { position: "absolute", width: 1, height: 1, padding: 0, margin: -1, overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0 },
  link: { color: color.accent, fontSize: size.tiny, fontWeight: 700, textDecoration: "none" },
  muted: { color: color.muted, fontSize: size.small, lineHeight: 1.5 },
  notice: { margin: `${space(4)}px 0 0`, color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  limits: { marginTop: space(3) },
  limitsSummary: { color: color.muted, fontSize: size.tiny, fontWeight: 700, cursor: "pointer" },
  limitsList: { margin: `${space(2)}px 0 0`, paddingLeft: 18, display: "grid", gap: space(2) },
  limitsItem: { color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
};
