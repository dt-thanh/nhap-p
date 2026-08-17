// frontend/src/pages/ImportSelectPage.jsx
// Luồng CHỌN NGỮ CẢNH trước khi nạp dữ liệu:
//   Bước 1: chọn Dự án  →  Bước 2: chọn Phân khu  →  sang màn Upload.
//
// Vì một chủ đầu tư có nhiều dự án, mỗi dự án nhiều phân khu, nên phải xác định
// "dữ liệu này thuộc đâu" trước khi tải file. Ngữ cảnh (project, zone) được
// truyền sang UploadPage qua router state.
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listProjects, listProjectZones } from "../api/endpoints";
import { color, size, radius, shadow, space, font, layout } from "../styles/tokens";
import { useBreakpoint, pick } from "../hooks/useBreakpoint";

// Nhãn + màu theo mức độ bán của phân khu
const ZONE_STATUS = {
  hot:    { label: "Bán chạy",  fg: () => color.danger, bg: () => color.dangerSoft },
  normal: { label: "Ổn định",   fg: () => color.ok,     bg: () => color.okSoft },
  slow:   { label: "Bán chậm",  fg: () => color.muted,  bg: () => color.canvas },
};

export default function ImportSelectPage() {
  const navigate = useNavigate();
  const { bp } = useBreakpoint();
  const gutter = pick(bp, layout.gutter);

  const [stage, setStage] = useState("project"); // project | zone
  const [projects, setProjects] = useState([]);
  const [zones, setZones] = useState([]);
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listProjects().then((p) => setProjects(p || [])).finally(() => setLoading(false));
  }, []);

  function chooseProject(p) {
    setProject(p);
    setStage("zone");
    setLoading(true);
    setZones([]);
    listProjectZones(p.id).then((z) => setZones(z || [])).finally(() => setLoading(false));
  }

  function chooseZone(zone) {
    // Sang màn Upload, mang theo ngữ cảnh đã chọn
    navigate("/import/upload", {
      state: { project, zone },
    });
  }

  const cols = pick(bp, { mobile: 1, tablet: 2, laptop: 3, desktop: 3 });

  return (
    <div style={{ ...S.wrap, padding: `${space(7)}px ${gutter}px ${space(16)}px` }}>
      <header style={S.head}>
        <h1 style={S.h1}>Nạp dữ liệu</h1>
        <p style={S.sub}>Chọn dự án và phân khu trước khi tải file bán hàng &amp; tồn kho.</p>
      </header>

      {/* thanh tiến trình 3 bước */}
      <Stepper stage={stage} project={project} />

      {stage === "project" && (
        <>
          <SectionTitle n="1" text="Chọn dự án" />
          {loading ? (
            <SkeletonGrid cols={cols} />
          ) : (
            <div style={{ ...S.grid, gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
              {projects.map((p) => (
                <ProjectCard key={p.id} p={p} onClick={() => chooseProject(p)} />
              ))}
            </div>
          )}
        </>
      )}

      {stage === "zone" && (
        <>
          <button style={S.back} onClick={() => { setStage("project"); setProject(null); }}>
            ‹ Đổi dự án
          </button>
          <SectionTitle n="2" text={`Chọn phân khu · ${project?.name}`} />
          {loading ? (
            <SkeletonGrid cols={cols} />
          ) : (
            <div style={{ ...S.grid, gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
              {/* Tất cả phân khu — khi file đã chứa cột phân khu bên trong */}
              <button style={{ ...S.card, ...S.allCard }} onClick={() => chooseZone({ id: "all", name: "Tất cả phân khu" })}>
                <div style={S.allIcon} aria-hidden="true">▦</div>
                <div style={S.zoneName}>Tất cả phân khu</div>
                <div style={S.zoneMeta}>File đã có sẵn cột phân khu</div>
              </button>

              {zones.map((z) => (
                <ZoneCard key={z.id} z={z} onClick={() => chooseZone(z)} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function Stepper({ stage, project }) {
  const steps = [
    { key: "project", label: "Chọn dự án", done: stage !== "project" },
    { key: "zone", label: project ? project.name : "Chọn phân khu", active: stage === "zone" },
    { key: "upload", label: "Nạp file" },
  ];
  const activeIdx = stage === "project" ? 0 : 1;
  return (
    <div style={S.stepper}>
      {steps.map((s, i) => (
        <React.Fragment key={s.key}>
          <div style={S.step}>
            <span style={{
              ...S.stepDot,
              background: i < activeIdx ? color.accent : i === activeIdx ? color.accent : color.surface,
              borderColor: i <= activeIdx ? color.accent : color.borderStrong,
              color: i <= activeIdx ? "#fff" : color.muted,
            }}>
              {i < activeIdx ? "✓" : i + 1}
            </span>
            <span style={{ ...S.stepLabel, color: i <= activeIdx ? color.ink : color.muted, fontWeight: i === activeIdx ? 700 : 500 }}>
              {s.label}
            </span>
          </div>
          {i < steps.length - 1 && <span style={S.stepLine} />}
        </React.Fragment>
      ))}
    </div>
  );
}

function ProjectCard({ p, onClick }) {
  return (
    <button style={S.card} onClick={onClick}>
      <div style={S.cardTop}>
        <span style={S.projName}>{p.name}</span>
        <span style={S.chevron} aria-hidden="true">›</span>
      </div>
      <div style={S.projLoc}>{p.location}</div>

      <div style={S.projStats}>
        <span>{p.zone_count} phân khu</span>
        <span style={S.dotSep}>·</span>
        <span>{p.total_units.toLocaleString("vi-VN")} căn</span>
      </div>

      {/* thanh tiến độ bán */}
      <div style={S.barWrap}>
        <div style={S.barTrack}>
          <div style={{ ...S.barFill, width: `${p.sold_pct}%` }} />
        </div>
        <span style={S.barLabel}>{p.sold_pct}% đã bán</span>
      </div>
    </button>
  );
}

function ZoneCard({ z, onClick }) {
  const st = ZONE_STATUS[z.status] || ZONE_STATUS.normal;
  const soldPct = Math.round(((z.total_units - z.units_remaining) / z.total_units) * 100);
  return (
    <button style={S.card} onClick={onClick}>
      <div style={S.cardTop}>
        <span style={S.zoneName}>{z.name}</span>
        <span style={{ ...S.statusChip, color: st.fg(), background: st.bg() }}>{st.label}</span>
      </div>
      <div style={S.zoneMeta}>
        Còn <b style={{ color: color.ink }}>{z.units_remaining}</b> / {z.total_units} căn
      </div>
      <div style={S.barWrap}>
        <div style={S.barTrack}>
          <div style={{ ...S.barFill, width: `${soldPct}%` }} />
        </div>
        <span style={S.barLabel}>{soldPct}% đã bán</span>
      </div>
    </button>
  );
}

function SectionTitle({ n, text }) {
  return (
    <div style={S.sectionTitle}>
      <span style={S.sectionNum}>{n}</span>
      <span>{text}</span>
    </div>
  );
}

function SkeletonGrid({ cols }) {
  return (
    <div style={{ ...S.grid, gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
      {Array.from({ length: cols }).map((_, i) => (
        <div key={i} style={{ ...S.card, height: 128, opacity: 0.5 }} />
      ))}
    </div>
  );
}

const S = {
  wrap: { maxWidth: layout.maxWidth, margin: "0 auto" },
  head: { marginBottom: space(5) },
  h1: { fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.01em" },
  sub: { fontSize: size.small, color: color.muted, margin: "4px 0 0" },

  stepper: { display: "flex", alignItems: "center", gap: space(2), margin: `${space(2)}px 0 ${space(7)}px`, flexWrap: "wrap" },
  step: { display: "flex", alignItems: "center", gap: space(2) },
  stepDot: {
    width: 26, height: 26, borderRadius: "50%", border: "1.5px solid",
    display: "grid", placeItems: "center", fontSize: size.tiny, fontWeight: 700, flex: "none",
  },
  stepLabel: { fontSize: size.small },
  stepLine: { width: 32, height: 1.5, background: color.border, flex: "none" },

  sectionTitle: { display: "flex", alignItems: "center", gap: space(2), fontSize: size.h2, fontWeight: 700, color: color.ink, margin: `${space(2)}px 0 ${space(4)}px` },
  sectionNum: {
    width: 22, height: 22, borderRadius: "50%", background: color.accentSoft, color: color.accent,
    display: "grid", placeItems: "center", fontSize: size.tiny, fontWeight: 700,
  },

  grid: { display: "grid", gap: space(3) },
  card: {
    textAlign: "left", background: color.surface, border: `1px solid ${color.border}`,
    borderRadius: radius.md, padding: space(4), cursor: "pointer", fontFamily: "inherit",
    boxShadow: shadow, transition: "border-color .12s, transform .08s, box-shadow .12s",
    display: "flex", flexDirection: "column", gap: space(2),
  },
  allCard: { alignItems: "flex-start", justifyContent: "center", borderStyle: "dashed", background: color.canvas },
  allIcon: { fontSize: 20, color: color.accent },

  cardTop: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(2) },
  projName: { fontSize: size.body, fontWeight: 700, color: color.ink, lineHeight: 1.3 },
  chevron: { fontSize: 20, color: color.muted, flex: "none", lineHeight: 1 },
  projLoc: { fontSize: size.tiny, color: color.muted },
  projStats: { display: "flex", gap: space(2), fontSize: size.small, color: color.body, alignItems: "center" },
  dotSep: { color: color.muted },

  zoneName: { fontSize: size.body, fontWeight: 700, color: color.ink },
  zoneMeta: { fontSize: size.small, color: color.muted },
  statusChip: { fontSize: 11, fontWeight: 600, padding: "3px 9px", borderRadius: radius.pill, flex: "none", whiteSpace: "nowrap" },

  barWrap: { display: "flex", alignItems: "center", gap: space(2), marginTop: space(1) },
  barTrack: { flex: 1, height: 6, background: color.canvas, borderRadius: radius.pill, overflow: "hidden" },
  barFill: { height: "100%", background: color.accent, borderRadius: radius.pill },
  barLabel: { fontSize: size.tiny, color: color.muted, flex: "none", fontVariantNumeric: "tabular-nums" },

  back: {
    background: "transparent", border: "none", color: color.accent, cursor: "pointer",
    fontSize: size.small, fontWeight: 600, fontFamily: "inherit", padding: 0, marginBottom: space(3),
  },
};
