// frontend/src/components/ranking/AhpWeightPanel.jsx
// ---------------------------------------------------------------------------
// Suy trọng số xếp hạng bằng SO SÁNH CẶP (AHP) — công thức V2.
//
// Vì sao có panel này: hỏi "unit_available đáng bao nhiêu phần trăm?" là câu hỏi
// không ai trả lời được cho tử tế. Hỏi "giữa hai tiêu chí này, cái nào quan
// trọng hơn, hơn bao nhiêu?" thì trả lời được. Panel chỉ làm đúng việc đổi từ
// câu hỏi thứ hai sang câu hỏi thứ nhất.
//
// Ba điều panel này CỐ Ý không làm:
//
// 1. **Không tự lưu bản nháp.** Nó chỉ điền trọng số vào biểu mẫu đang mở; người
//    dùng vẫn phải bấm "Lưu bản nháp" rồi "Phát hành". Vòng duyệt của người là
//    yêu cầu cứng của AGENTS.md — panel không được rút ngắn nó.
// 2. **Không tự chọn tiêu chí.** Danh sách lấy đúng các đặc trưng ĐANG BẬT trong
//    bản nháp, nên `direction`/`missing_value_policy` gửi lên backend luôn khớp
//    với thứ người dùng nhìn thấy. AHP chỉ cho ĐỘ LỚN, không bao giờ cho chiều.
// 3. **Không tự vượt ngưỡng CR.** Khi backend từ chối vì thiếu nhất quán, panel
//    hiện `hotspots` — cặp nào lệch nhất và các câu trả lời còn lại hàm ý bao
//    nhiêu. "CR = 0.31, mời nhập lại" là lời từ chối không dùng được.
// ---------------------------------------------------------------------------
import React, { useMemo, useState } from "react";
import { computeAhpWeights } from "../../api/endpoints";
import { isAuthError } from "../../api/client";
import { color, font, radius, shadow, size, space } from "../../styles/tokens";

// Thang Saaty 1–9. Chữ mô tả quan trọng hơn con số: người bán hàng chọn theo
// câu chữ, con số chỉ để đối chiếu khi cần.
const SAATY = {
  1: "ngang nhau",
  2: "nhỉnh hơn",
  3: "hơi quan trọng hơn",
  4: "khá quan trọng hơn",
  5: "quan trọng hơn rõ rệt",
  6: "quan trọng hơn nhiều",
  7: "quan trọng hơn rất nhiều",
  8: "gần như vượt trội",
  9: "vượt trội tuyệt đối",
};

// Một ô chọn duy nhất cho mỗi cặp, chạy từ "A vượt trội" qua "ngang nhau" tới
// "B vượt trội". Hai ô riêng cho hai chiều sẽ cho phép người dùng tự mâu thuẫn —
// đúng thứ mà việc chỉ hỏi tam giác trên sinh ra để loại bỏ.
const TOKENS = [
  ...Array.from({ length: 8 }, (_, i) => `a${9 - i}`), // a9 … a2
  "eq",
  ...Array.from({ length: 8 }, (_, i) => `b${i + 2}`), // b2 … b9
];

export function tokenToValue(token) {
  if (token === "eq") return 1;
  const n = Number(token.slice(1));
  return token[0] === "a" ? n : 1 / n;
}

function optionLabel(token, leftName, rightName) {
  if (token === "eq") return "ngang nhau (1)";
  const n = Number(token.slice(1));
  return token[0] === "a"
    ? `${leftName} ${SAATY[n]} (${n})`
    : `${rightName} ${SAATY[n]} (1/${n})`;
}

const pairKey = (a, b) => `${a}|${b}`;
const short = (label) => label.split(" (")[0];

export default function AhpWeightPanel({ features, publishedWeights = {}, onApply, disabled = false }) {
  const [open, setOpen] = useState(false);
  const [judgments, setJudgments] = useState({});
  const [result, setResult] = useState(null);
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [override, setOverride] = useState(false);
  const [reason, setReason] = useState("");

  const pairs = useMemo(() => {
    const out = [];
    for (let i = 0; i < features.length; i += 1) {
      for (let j = i + 1; j < features.length; j += 1) out.push([features[i], features[j]]);
    }
    return out;
  }, [features]);

  // Đổi tập tiêu chí thì mọi phán đoán cũ không còn dựng được ma trận đầy đủ nữa.
  const signature = features.map((f) => f.key).join(",");
  const [seenSignature, setSeenSignature] = useState(signature);
  if (signature !== seenSignature) {
    setSeenSignature(signature);
    setJudgments({});
    setResult(null);
    setFailure(null);
  }

  async function compute() {
    setBusy(true);
    setFailure(null);
    setResult(null);
    try {
      const response = await computeAhpWeights({
        criteria: features.map((f) => f.key),
        judgments: pairs.map(([a, b]) => ({
          a: a.key,
          b: b.key,
          value: tokenToValue(judgments[pairKey(a.key, b.key)] ?? "eq"),
        })),
        feature_specs: Object.fromEntries(
          features.map((f) => [
            f.key,
            {
              direction: f.direction,
              missing_value_policy: f.missing_value_policy,
              min_confidence: Number(f.min_confidence) || 0,
            },
          ]),
        ),
        override,
        override_reason: reason,
      });
      setResult(response);
      setOverride(false);
      setReason("");
    } catch (error) {
      const detail = error?.data?.detail;
      setFailure({
        code: detail?.error_code ?? null,
        // 401/403 đã có thông báo thân thiện từ tầng client — không diễn giải lại.
        text: isAuthError(error)
          ? error.message
          : detail?.message || error?.message || "Không tính được trọng số.",
        hotspots: detail?.hotspots ?? [],
        ratio: detail?.consistency_ratio ?? null,
        threshold: detail?.threshold ?? null,
      });
    } finally {
      setBusy(false);
    }
  }

  if (features.length < 2) {
    return (
      <div style={S.wrap}>
        <p style={S.emptyHint}>
          Bật ít nhất <b>2 đặc trưng</b> ở dưới rồi quay lại đây — so sánh cặp cần tối thiểu hai tiêu chí.
        </p>
      </div>
    );
  }

  // `override ||` KHÔNG thừa: `compute()` xoá `failure` ngay khi bắt đầu gửi, nên
  // nếu chỉ dựa vào `failure` thì ô nhập lý do biến mất đúng lúc người dùng vừa
  // bấm Tính — họ tưởng mình bấm nhầm gì đó. Đã tick override thì giữ ô lại.
  const canOverride =
    override || failure?.code === "CR_ABOVE_THRESHOLD" || failure?.code === "OVERRIDE_REASON_REQUIRED";

  return (
    <div style={S.wrap}>
      <div style={S.head}>
        <div>
          <h3 style={S.title}>Suy trọng số bằng so sánh cặp (AHP)</h3>
          <p style={S.sub}>
            {pairs.length} câu hỏi cho {features.length} tiêu chí · kết quả vẫn phải qua “Lưu bản nháp”
          </p>
        </div>
        <button type="button" style={S.ghost} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? "Thu gọn" : "Mở"}
        </button>
      </div>

      {open && (
        <>
          <div style={S.pairList}>
            {pairs.map(([a, b]) => {
              const key = pairKey(a.key, b.key);
              const value = judgments[key] ?? "eq";
              return (
                <label key={key} style={S.pairRow}>
                  <span style={S.pairSide}>{short(a.label)}</span>
                  <select
                    style={S.pairSelect}
                    value={value}
                    disabled={disabled || busy}
                    aria-label={`So sánh ${short(a.label)} với ${short(b.label)}`}
                    onChange={(event) =>
                      setJudgments((current) => ({ ...current, [key]: event.target.value }))
                    }
                  >
                    {TOKENS.map((token) => (
                      <option key={token} value={token}>
                        {optionLabel(token, short(a.label), short(b.label))}
                      </option>
                    ))}
                  </select>
                  <span style={{ ...S.pairSide, textAlign: "right" }}>{short(b.label)}</span>
                </label>
              );
            })}
          </div>

          {canOverride && (
            <div style={S.overrideBox}>
              <label style={S.overrideCheck}>
                <input
                  type="checkbox"
                  checked={override}
                  onChange={(event) => setOverride(event.target.checked)}
                />
                Vẫn chấp nhận bộ phán đoán này dù vượt ngưỡng
              </label>
              {override && (
                <input
                  style={S.input}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Lý do — sẽ được ghi vào ghi chú của config"
                />
              )}
            </div>
          )}

          <div style={S.actions}>
            <button
              type="button"
              style={{ ...S.primary, ...(disabled || busy ? S.disabled : null) }}
              disabled={disabled || busy}
              onClick={compute}
            >
              {busy ? "Đang tính…" : "Tính trọng số"}
            </button>
          </div>

          {failure && (
            <div style={S.failure}>
              <b>{failure.text}</b>
              {failure.hotspots.length > 0 && (
                <>
                  <p style={S.hotspotIntro}>Các so sánh lệch nhiều nhất so với phần còn lại:</p>
                  <ul style={S.hotspotList}>
                    {failure.hotspots.map((spot) => (
                      <li key={`${spot.a}|${spot.b}`} style={S.hotspotItem}>
                        <b>{short(labelOf(features, spot.a))}</b> vs{" "}
                        <b>{short(labelOf(features, spot.b))}</b> — bạn chấm{" "}
                        <code style={S.code}>{spot.judged}</code>, các câu trả lời còn lại hàm ý{" "}
                        <code style={S.code}>{spot.implied}</code>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}

          {result && (
            <div style={S.result}>
              <div style={S.resultHead}>
                <span style={{ ...S.crPill, ...(result.consistent ? S.crOk : S.crWarn) }}>
                  CR {Number(result.consistency_ratio).toFixed(4)}
                  {result.consistent ? " ✓ nhất quán" : " — đã chấp nhận vượt ngưỡng"}
                </span>
                <span style={S.crThreshold}>ngưỡng {result.threshold}</span>
              </div>

              <table style={S.table}>
                <thead>
                  <tr>
                    <th style={S.th}>Tiêu chí</th>
                    <th style={{ ...S.th, textAlign: "right" }}>Đang dùng</th>
                    <th style={{ ...S.th, textAlign: "right" }}>AHP đề xuất</th>
                  </tr>
                </thead>
                <tbody>
                  {features.map((feature) => {
                    const next = Number(result.weights[feature.key]?.weight ?? 0);
                    const current = Number(publishedWeights[feature.key]?.weight ?? 0);
                    const delta = next - current;
                    return (
                      <tr key={feature.key}>
                        <td style={S.td}>{short(feature.label)}</td>
                        <td style={{ ...S.td, ...S.numCell, color: color.muted }}>
                          {current ? current.toFixed(4) : "—"}
                        </td>
                        <td style={{ ...S.td, ...S.numCell }}>
                          <b>{next.toFixed(4)}</b>
                          {current > 0 && Math.abs(delta) >= 0.0001 && (
                            <span style={delta > 0 ? S.up : S.down}>
                              {delta > 0 ? " ▲" : " ▼"}
                              {Math.abs(delta).toFixed(4)}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              <div style={S.actions}>
                <button
                  type="button"
                  style={S.primary}
                  disabled={disabled}
                  onClick={() => onApply(result.weights, result.note)}
                >
                  Điền vào bản nháp
                </button>
                <span style={S.applyHint}>Điền xong vẫn phải bấm “Lưu bản nháp” rồi “Phát hành”.</span>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function labelOf(features, key) {
  return features.find((f) => f.key === key)?.label ?? key;
}

const S = {
  wrap: {
    border: `1px solid ${color.border}`,
    borderRadius: radius.md,
    background: color.canvas,
    padding: space(4),
    display: "grid",
    gap: space(3),
  },
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(3), flexWrap: "wrap" },
  title: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2, letterSpacing: "-.02em" },
  sub: { margin: "4px 0 0", color: color.muted, fontSize: size.tiny },
  emptyHint: { margin: 0, color: color.muted, fontSize: size.tiny, lineHeight: 1.6 },

  pairList: { display: "grid", gap: space(2) },
  pairRow: {
    display: "grid",
    gridTemplateColumns: "minmax(0,1fr) minmax(0,1.6fr) minmax(0,1fr)",
    alignItems: "center",
    gap: space(2),
    background: color.surface,
    border: `1px solid ${color.border}`,
    borderRadius: radius.sm,
    padding: `${space(2)}px ${space(3)}px`,
    fontSize: size.tiny,
  },
  pairSide: { color: color.ink, fontWeight: 600, overflowWrap: "anywhere" },
  pairSelect: {
    padding: "7px 9px",
    border: `1px solid ${color.borderStrong}`,
    borderRadius: radius.sm,
    fontFamily: "inherit",
    fontSize: size.tiny,
    background: color.surface,
    width: "100%",
  },

  overrideBox: { display: "grid", gap: space(2), background: color.warnSoft, borderRadius: radius.sm, padding: space(3) },
  overrideCheck: { display: "flex", alignItems: "center", gap: space(2), color: color.body, fontSize: size.tiny, cursor: "pointer" },
  input: { padding: "8px 10px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontFamily: "inherit", fontSize: size.tiny },

  actions: { display: "flex", alignItems: "center", gap: space(3), flexWrap: "wrap" },
  primary: { background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "9px 15px", fontWeight: 700, cursor: "pointer", fontFamily: "inherit", fontSize: size.small },
  ghost: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "7px 12px", cursor: "pointer", fontFamily: "inherit", fontSize: size.tiny },
  disabled: { background: color.borderStrong, cursor: "not-allowed" },
  applyHint: { color: color.muted, fontSize: size.tiny },

  failure: { background: color.dangerSoft, color: color.danger, borderRadius: radius.sm, padding: space(3), fontSize: size.tiny, lineHeight: 1.6 },
  hotspotIntro: { margin: `${space(2)}px 0 ${space(1)}px`, color: color.body },
  hotspotList: { margin: 0, paddingLeft: space(4), color: color.body },
  hotspotItem: { marginBottom: 3 },
  code: { fontFamily: font.mono, background: color.surface, borderRadius: 3, padding: "1px 5px", color: color.ink },

  result: { display: "grid", gap: space(3), background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: space(3), boxShadow: shadow },
  resultHead: { display: "flex", alignItems: "center", gap: space(3), flexWrap: "wrap" },
  crPill: { borderRadius: radius.pill, padding: "5px 12px", fontSize: size.tiny, fontWeight: 700, fontVariantNumeric: "tabular-nums" },
  crOk: { color: color.ok, background: color.okSoft },
  crWarn: { color: color.warn, background: color.warnSoft },
  crThreshold: { color: color.muted, fontSize: size.tiny, fontVariantNumeric: "tabular-nums" },

  table: { width: "100%", borderCollapse: "collapse", fontSize: size.tiny },
  th: { textAlign: "left", padding: `${space(2)}px ${space(2)}px`, color: color.muted, fontWeight: 700, borderBottom: `1px solid ${color.border}` },
  td: { padding: `${space(2)}px ${space(2)}px`, borderBottom: `1px solid ${color.border}`, color: color.body },
  numCell: { textAlign: "right", fontVariantNumeric: "tabular-nums", fontFamily: font.mono, color: color.ink, whiteSpace: "nowrap" },
  up: { color: color.ok, fontWeight: 700 },
  down: { color: color.danger, fontWeight: 700 },
};
