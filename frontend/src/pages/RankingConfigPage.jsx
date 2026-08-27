// frontend/src/pages/RankingConfigPage.jsx
// Quản trị bộ trọng số xếp hạng — trước đợt này chỉ đổi được bằng migration.
//
// Ba điều trang này phải nói rõ, vì hiểu sai bất kỳ điều nào đều dẫn tới quyết
// định sai:
//
// 1. **Bộ trọng số là TOÀN CỤC.** Một config áp cho MỌI dự án. Không có bộ chọn
//    dự án ở đây, và publish sẽ xếp hàng tính lại toàn bộ.
// 2. **Lịch sử không bao giờ bị sửa.** Sửa trọng số của một version đã phát hành
//    sẽ khiến mọi điểm cũ trỏ tới một config đã đổi nghĩa. Nên "rollback" tạo
//    một version MỚI chép từ version cũ, và trang này gọi thẳng nó vậy.
// 3. **Tổng trọng số phải bằng 1.0.** Hiện tổng ngay lúc gõ, chứ không để backend
//    từ chối sau khi người dùng đã điền xong cả biểu mẫu.
import React, { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  createRankingConfigDraft,
  listRankingConfigs,
  publishRankingConfig,
  rollbackRankingConfig,
} from "../api/endpoints";
import { isAuthError } from "../api/client";
import AhpWeightPanel from "../components/ranking/AhpWeightPanel";
import { useAsync } from "../hooks/useAsync";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { SectionState } from "../components/ui/States";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

const STATUS_STYLE = {
  published: { label: "Đang phát hành", fg: color.ok, bg: color.okSoft },
  draft: { label: "Bản nháp", fg: color.warn, bg: color.warnSoft },
  archived: { label: "Đã lưu trữ", fg: color.muted, bg: color.canvas },
};

// Nhãn tiếng Việt cho từng đặc trưng, và nhóm của nó. Nhóm quan trọng: đặc trưng
// KHẢO SÁT chỉ có giá trị sau khi bộ tổng hợp ngoài đã nạp dữ liệu, nên bật một
// cái lên mà chưa có dữ liệu là cách nhanh nhất tạo ra bảng xếp hạng rỗng.
const FEATURES = {
  unit_available: { label: "Căn còn trống", group: "Vận hành" },
  unit_demand_norm: { label: "Nhu cầu trên căn (deal đang trong phễu)", group: "Vận hành" },
  has_active_deal: { label: "Đang có giao dịch giữ căn", group: "Vận hành" },
  area_velocity_norm: { label: "Tốc độ bán của phân khu (30 ngày)", group: "Vận hành" },
  area_conversion_norm: { label: "Tỉ lệ chốt của phân khu", group: "Vận hành" },
  view_quality: { label: "Chất lượng tầm nhìn", group: "Khảo sát" },
  natural_light: { label: "Ánh sáng tự nhiên", group: "Khảo sát" },
  privacy: { label: "Riêng tư", group: "Khảo sát" },
  noise_level: { label: "Độ ồn", group: "Khảo sát" },
};

const DEFAULT_SPEC = { weight: 0, direction: "positive", missing_value_policy: "zero", min_confidence: 0 };

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString("vi-VN") : "—");
const sumWeights = (weights) =>
  Object.values(weights).reduce((total, spec) => total + (Number(spec.weight) || 0), 0);

export default function RankingConfigPage() {
  const navigate = useNavigate();
  const configs = useAsync(() => listRankingConfigs(), []);
  const [actor, setActor] = useState("");
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null);

  const rows = configs.data ?? [];
  const published = rows.find((c) => c.status === "published");

  const total = useMemo(() => (editing ? sumWeights(editing.weights) : 0), [editing]);
  const totalOk = Math.abs(total - 1) < 1e-9;

  const act = useCallback(
    async (fn, successMessage) => {
      if (!actor.trim()) {
        setNotice({ kind: "error", text: "Điền tên người thực hiện trước — nó được ghi vào lịch sử config." });
        return;
      }
      setBusy(true);
      setNotice(null);
      try {
        const result = await fn(actor.trim());
        const reranked = result?.reranked;
        setNotice({
          kind: "ok",
          text:
            successMessage +
            (reranked ? ` Đã xếp hàng tính lại ${reranked.enqueued}/${reranked.projects} dự án.` : ""),
        });
        setEditing(null);
        configs.reload();
      } catch (error) {
        setNotice({
          kind: "error",
          text: isAuthError(error)
            ? error.message
            : error?.status === 403
              ? "Cần vai trò admin để đổi bộ trọng số."
              : error?.message || "Thao tác không thành công.",
        });
      } finally {
        setBusy(false);
      }
    },
    [actor, configs],
  );

  function startDraftFrom(source) {
    const base = source ? { ...source.weights } : {};
    for (const key of Object.keys(FEATURES)) {
      if (!base[key]) continue;
      base[key] = { ...DEFAULT_SPEC, ...base[key] };
    }
    setEditing({
      weights: base,
      min_weight_coverage: source ? Number(source.min_weight_coverage) : 0.5,
      note: source ? `Chép từ v${source.version}.` : "",
      copied_from_version: source ? source.version : null,
    });
    setNotice(null);
  }

  function setSpec(key, patch) {
    setEditing((current) => ({
      ...current,
      weights: { ...current.weights, [key]: { ...DEFAULT_SPEC, ...current.weights[key], ...patch } },
    }));
  }

  // Đặc trưng ĐANG BẬT trong bản nháp chính là tập tiêu chí AHP sẽ hỏi. Lấy từ
  // đây thay vì hardcode nghĩa là `direction`/`missing_value_policy` gửi lên
  // backend luôn khớp với thứ người dùng đang nhìn thấy ngay bên dưới.
  const draftFeatures = useMemo(
    () =>
      editing
        ? Object.entries(editing.weights).map(([key, spec]) => ({
            key,
            label: FEATURES[key]?.label ?? key,
            direction: spec.direction,
            missing_value_policy: spec.missing_value_policy,
            min_confidence: spec.min_confidence,
          }))
        : [],
    [editing],
  );

  // AHP chỉ ĐIỀN vào biểu mẫu. Không lưu, không phát hành — người dùng vẫn phải
  // bấm hai nút đó, vì vòng duyệt của người là yêu cầu cứng của AGENTS.md.
  function applyAhpWeights(weights, note) {
    setEditing((current) => ({
      ...current,
      weights: Object.fromEntries(
        Object.entries(weights).map(([key, spec]) => [key, { ...DEFAULT_SPEC, ...spec }]),
      ),
      note: note || current.note,
    }));
    setNotice({ kind: "ok", text: "Đã điền trọng số AHP vào bản nháp. Kiểm lại rồi bấm Lưu bản nháp." });
  }

  function toggleFeature(key, on) {
    setEditing((current) => {
      const weights = { ...current.weights };
      if (on) weights[key] = { ...DEFAULT_SPEC };
      else delete weights[key];
      return { ...current, weights };
    });
  }

  return (
    <>
      <GlobalKeyframes />
      <header style={S.pageHead}>
        <div>
          <h1 style={S.h1}>Bộ trọng số xếp hạng</h1>
          <p style={S.sub}>
            Áp cho <b>mọi dự án</b> · Lịch sử chỉ thêm, không sửa · Phát hành sẽ tính lại toàn bộ
          </p>
        </div>
        <button style={S.ghost} onClick={() => navigate("/ranking")}>
          ← Về bảng xếp hạng
        </button>
      </header>

      <section style={S.actorBar}>
        <label style={S.label}>
          Người thực hiện (ghi vào lịch sử)
          <input
            style={S.input}
            value={actor}
            onChange={(event) => setActor(event.target.value)}
            placeholder="Tên người phát hành"
          />
        </label>
        <button style={S.primary} disabled={busy || !!editing} onClick={() => startDraftFrom(published)}>
          Soạn bản nháp từ config đang dùng
        </button>
      </section>

      {notice && <div style={notice.kind === "error" ? S.error : S.ok}>{notice.text}</div>}

      {editing && (
        <section style={S.editor}>
          <div style={S.editorHead}>
            <h2 style={S.h2}>Bản nháp mới</h2>
            <span style={{ ...S.totalPill, ...(totalOk ? S.totalOk : S.totalBad) }}>
              tổng trọng số {total.toFixed(4)}
              {totalOk ? " ✓" : " — phải bằng 1.0"}
            </span>
          </div>

          <AhpWeightPanel
            features={draftFeatures}
            publishedWeights={published?.weights ?? {}}
            onApply={applyAhpWeights}
            disabled={busy}
          />

          <div style={S.featureList}>
            {Object.entries(FEATURES).map(([key, meta]) => {
              const spec = editing.weights[key];
              const on = Boolean(spec);
              return (
                <div key={key} style={{ ...S.feature, opacity: on ? 1 : 0.55 }}>
                  <label style={S.featureHead}>
                    <input type="checkbox" checked={on} onChange={(e) => toggleFeature(key, e.target.checked)} />
                    <span style={S.featureName}>{meta.label}</span>
                    <span style={meta.group === "Khảo sát" ? S.tagSurvey : S.tagOps}>{meta.group}</span>
                  </label>
                  {on && (
                    <div style={S.specRow}>
                      <label style={S.specField}>
                        trọng số
                        <input
                          style={S.specInput}
                          type="number"
                          step="0.0001"
                          min="0"
                          max="1"
                          value={spec.weight}
                          onChange={(e) => setSpec(key, { weight: Number(e.target.value) })}
                        />
                      </label>
                      <label style={S.specField}>
                        chiều
                        <select
                          style={S.specInput}
                          value={spec.direction}
                          onChange={(e) => setSpec(key, { direction: e.target.value })}
                        >
                          <option value="positive">thuận</option>
                          <option value="negative">ngược</option>
                        </select>
                      </label>
                      <label style={S.specField}>
                        khi thiếu
                        <select
                          style={S.specInput}
                          value={spec.missing_value_policy}
                          onChange={(e) => setSpec(key, { missing_value_policy: e.target.value })}
                        >
                          <option value="zero">= 0</option>
                          <option value="neutral">= 0.5</option>
                          <option value="skip">bỏ khỏi mẫu số</option>
                        </select>
                      </label>
                    </div>
                  )}
                  {on && meta.group === "Khảo sát" && spec.missing_value_policy === "skip" && (
                    <p style={S.warn}>
                      Với <b>bỏ khỏi mẫu số</b>, đặc trưng này phải đã có dữ liệu khảo sát — nếu chưa,
                      phát hành sẽ khiến mọi căn tụt dưới ngưỡng phủ và bảng xếp hạng rỗng. Backend sẽ
                      từ chối, nhưng đổi sang <b>= 0.5</b> là cách an toàn khi chưa có dữ liệu.
                    </p>
                  )}
                </div>
              );
            })}
          </div>

          <label style={S.label}>
            Ghi chú (vì sao đổi)
            <input
              style={S.input}
              value={editing.note}
              onChange={(e) => setEditing({ ...editing, note: e.target.value })}
              placeholder="Ví dụ: tăng trọng số nhu cầu cho đợt mở bán quý 4"
            />
          </label>

          <div style={S.row}>
            <button
              style={{ ...S.primary, ...(totalOk ? null : S.disabled) }}
              disabled={busy || !totalOk}
              onClick={() =>
                act(async (by) => {
                  const draft = await createRankingConfigDraft({
                    weights: editing.weights,
                    min_weight_coverage: editing.min_weight_coverage,
                    note: editing.note,
                    created_by: by,
                    copied_from_version: editing.copied_from_version,
                  });
                  return { config: draft };
                }, "Đã lưu bản nháp. Bấm Phát hành ở dòng tương ứng khi sẵn sàng.")
              }
            >
              Lưu bản nháp
            </button>
            <button style={S.ghost} disabled={busy} onClick={() => setEditing(null)}>
              Huỷ
            </button>
          </div>
        </section>
      )}

      <SectionState
        loading={configs.loading}
        error={configs.error}
        empty={!configs.loading && !configs.error && rows.length === 0}
        onRetry={configs.reload}
      >
        <div style={S.tableCard}>
          <div style={S.scroll}>
            <table style={S.table}>
              <thead>
                <tr>
                  <th style={S.th}>v</th>
                  <th style={S.th}>Trạng thái</th>
                  <th style={S.th}>Đặc trưng</th>
                  <th style={S.th}>Ngưỡng phủ</th>
                  <th style={S.th}>Người tạo</th>
                  <th style={S.th}>Phát hành lúc</th>
                  <th style={S.th}>Ghi chú</th>
                  <th style={S.th} />
                </tr>
              </thead>
              <tbody>
                {rows.map((config) => {
                  const badge = STATUS_STYLE[config.status] || STATUS_STYLE.archived;
                  const keys = Object.keys(config.weights || {});
                  return (
                    <tr key={config.id} style={S.tr}>
                      <td style={{ ...S.td, fontFamily: font.mono, color: color.ink }}>{config.version}</td>
                      <td style={S.td}>
                        <span style={{ ...S.badge, color: badge.fg, background: badge.bg }}>{badge.label}</span>
                      </td>
                      <td style={S.td}>
                        <div style={S.weightList}>
                          {keys.map((key) => (
                            <span key={key} style={S.weightChip}>
                              {(FEATURES[key]?.label ?? key).split(" (")[0]}
                              <b style={S.weightValue}>{Number(config.weights[key].weight).toFixed(2)}</b>
                            </span>
                          ))}
                        </div>
                      </td>
                      <td style={{ ...S.td, fontVariantNumeric: "tabular-nums" }}>
                        {Number(config.min_weight_coverage).toFixed(2)}
                      </td>
                      <td style={S.td}>{config.created_by}</td>
                      <td style={{ ...S.td, whiteSpace: "nowrap" }}>{fmtDate(config.published_at)}</td>
                      <td style={{ ...S.td, color: color.muted, maxWidth: 260 }}>{config.note}</td>
                      <td style={{ ...S.td, whiteSpace: "nowrap" }}>
                        {config.status === "draft" && (
                          <button
                            style={S.small}
                            disabled={busy}
                            onClick={() =>
                              act(
                                (by) => publishRankingConfig(config.version, by),
                                `Đã phát hành v${config.version}.`,
                              )
                            }
                          >
                            Phát hành
                          </button>
                        )}
                        {config.status === "archived" && (
                          <button
                            style={S.smallGhost}
                            disabled={busy}
                            onClick={() =>
                              act(
                                (by) => rollbackRankingConfig(config.version, by),
                                `Đã quay lại trọng số của v${config.version} (chép sang version mới).`,
                              )
                            }
                          >
                            Quay lại bản này
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </SectionState>

      <p style={S.footnote}>
        “Quay lại bản này” <b>không</b> sửa lịch sử: nó chép trọng số của version cũ sang một version
        mới rồi phát hành version đó. Mọi điểm đã tính vẫn trỏ đúng về config đã sinh ra chúng.
      </p>
    </>
  );
}

const S = {
  pageHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4), marginBottom: space(5) },
  h1: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h1, letterSpacing: "-.03em" },
  h2: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2 },
  sub: { margin: "5px 0 0", color: color.muted, fontSize: size.small },

  actorBar: { display: "flex", alignItems: "flex-end", gap: space(4), flexWrap: "wrap", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), marginBottom: space(4), boxShadow: shadow },
  label: { display: "flex", flexDirection: "column", gap: 5, color: color.ink, fontSize: size.tiny, fontWeight: 700, flex: 1, minWidth: 240 },
  input: { padding: "9px 11px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontFamily: "inherit", fontSize: size.small },

  editor: { background: color.surface, border: `1px solid ${color.accent}`, borderRadius: radius.md, padding: space(4), marginBottom: space(4), display: "grid", gap: space(4), boxShadow: shadow },
  editorHead: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: space(3), flexWrap: "wrap" },
  totalPill: { borderRadius: radius.pill, padding: "6px 12px", fontSize: size.tiny, fontWeight: 700, fontVariantNumeric: "tabular-nums" },
  totalOk: { color: color.ok, background: color.okSoft },
  totalBad: { color: color.danger, background: color.dangerSoft },

  featureList: { display: "grid", gap: space(2) },
  feature: { border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: space(3) },
  featureHead: { display: "flex", alignItems: "center", gap: space(2), cursor: "pointer", fontSize: size.small },
  featureName: { color: color.ink, fontWeight: 600 },
  tagOps: { marginLeft: "auto", color: color.accent, background: color.accentSoft, borderRadius: radius.pill, padding: "2px 9px", fontSize: 10.5, fontWeight: 700 },
  tagSurvey: { marginLeft: "auto", color: color.warn, background: color.warnSoft, borderRadius: radius.pill, padding: "2px 9px", fontSize: 10.5, fontWeight: 700 },
  specRow: { display: "flex", gap: space(3), flexWrap: "wrap", marginTop: space(3) },
  specField: { display: "flex", flexDirection: "column", gap: 4, color: color.muted, fontSize: size.tiny },
  specInput: { padding: "7px 9px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontFamily: "inherit", fontSize: size.tiny, minWidth: 130 },
  warn: { margin: `${space(2)}px 0 0`, background: color.warnSoft, color: color.body, borderRadius: radius.sm, padding: space(2), fontSize: size.tiny, lineHeight: 1.5 },

  row: { display: "flex", gap: space(2) },
  primary: { background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "10px 16px", fontWeight: 700, cursor: "pointer", fontFamily: "inherit", fontSize: size.small },
  disabled: { background: color.borderStrong, cursor: "not-allowed" },
  ghost: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "10px 14px", cursor: "pointer", fontFamily: "inherit", fontSize: size.small },
  small: { background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "7px 12px", fontWeight: 700, cursor: "pointer", fontFamily: "inherit", fontSize: size.tiny },
  smallGhost: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "7px 12px", cursor: "pointer", fontFamily: "inherit", fontSize: size.tiny },

  ok: { background: color.okSoft, color: color.ok, borderRadius: radius.sm, padding: space(3), marginBottom: space(4), fontSize: size.small },
  error: { background: color.dangerSoft, color: color.danger, borderRadius: radius.sm, padding: space(3), marginBottom: space(4), fontSize: size.small },

  tableCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflow: "hidden" },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", fontSize: size.small },
  th: { textAlign: "left", padding: `${space(3)}px ${space(4)}px`, color: color.muted, fontSize: size.tiny, fontWeight: 700, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  tr: { borderBottom: `1px solid ${color.border}` },
  td: { padding: `${space(3)}px ${space(4)}px`, verticalAlign: "top" },
  badge: { borderRadius: radius.pill, padding: "4px 10px", fontSize: size.tiny, fontWeight: 700, whiteSpace: "nowrap" },
  weightList: { display: "flex", flexWrap: "wrap", gap: 5, maxWidth: 380 },
  weightChip: { display: "inline-flex", alignItems: "center", gap: 5, background: color.canvas, borderRadius: radius.pill, padding: "3px 9px", fontSize: 11.5, color: color.body },
  weightValue: { fontVariantNumeric: "tabular-nums", color: color.ink },

  footnote: { marginTop: space(5), color: color.muted, fontSize: size.tiny, lineHeight: 1.6 },
};
