// frontend/src/pages/CatalogPage.jsx
// TRANG SỬA DANH MỤC: đổi thông tin dự án và phân khu.
//
// Đặt riêng một trang thay vì nhét form vào Dashboard: Dashboard là màn hình
// theo dõi số liệu, trộn thao tác ghi vào đó sẽ làm rối mục đích của nó.
//
// Phase F/G: phạm vi Project/Area sống trong URL (`useProjectScope`, deep-link
// phục hồi đúng ngữ cảnh). Phase D xoá POST /projects, POST /areas ở backend —
// TẠO/SỬA trường CANONICAL (tên, ngày mở bán, loại căn, diện tích, số căn) giờ
// đi qua Mini CRM (`*InMiniCrm`, xem api/endpoints.js); backend chỉ còn nhận
// PATCH cho `headline`/`introduce` — nội dung hiển thị nó TỰ SỞ HỮU (§A2.3).
//
// Ghi Mini CRM xong KHÔNG có nghĩa backend đã thấy: Mini CRM chỉ LƯU rồi vòng
// relay tự động (Phase C.5) mới gửi sang backend sau đó, thường trong vài giây
// nhưng không tức thời. `writeThenWatchSync()` bên dưới phản ánh đúng điều đó —
// không giả vờ ghi xong là đã đồng bộ.
import React, { useEffect, useRef, useState } from "react";
import { useProjectScope } from "../hooks/useProjectScope";
import {
  createAreaInMiniCrm,
  createImage,
  createProjectInMiniCrm,
  deleteImage,
  getAreaByExternalId,
  getProjectByExternalId,
  replaceImage,
  updateArea,
  updateAreaInMiniCrm,
  updateProject,
  updateProjectInMiniCrm,
} from "../api/endpoints";
import { isAuthError } from "../api/client";
import ProjectSelector from "../components/ProjectSelector";
import { color, radius, space, size, layout, shadow } from "../styles/tokens";
import { useBreakpoint, pick } from "../hooks/useBreakpoint";

const EMPTY_PROJECT = { name: "", launch_date: "", headline: "", introduce: "" };
const EMPTY_AREA = {
  area_name: "",
  unit_type: "",
  bedrooms: "",
  area_sqm: "",
  total_units: "",
  headline: "",
  introduce: "",
};

/** Lấy câu lỗi backend/Mini CRM trả về; ApiError giữ nguyên body của response. */
function errorText(e) {
  const detail = e?.body?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  // 422 của FastAPI trả mảng lỗi theo từng trường.
  if (Array.isArray(detail) && detail.length) {
    const field = detail[0].loc?.slice(-1)[0];
    return `Trường "${field}" không hợp lệ: ${detail[0].msg}`;
  }
  return e?.message || "Có lỗi xảy ra";
}

/** Ghi qua Mini CRM rồi CHỜ backend chiếu xong — không giả định tức thời.
 *  `check()` đọc lại backend; trả `true` khi đã thấy `source_revision` mới.
 *  Bỏ cuộc sau vài lượt: relay tự động (mặc định mỗi vài giây) có thể chưa kịp
 *  chạy — trạng thái "pending" khi đó là SỰ THẬT, không phải một lỗi hiển thị. */
async function watchSync(check, { attempts = 6, delayMs = 1500 } = {}) {
  for (let i = 0; i < attempts; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    const seen = await check().catch(() => false);
    if (seen) return true;
    // eslint-disable-next-line no-await-in-loop
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  return false;
}

export default function CatalogPage() {
  const bp = useBreakpoint();
  const scope = useProjectScope();
  const { projects, areas, projectExternalId, areaExternalId } = scope;

  const [projectForm, setProjectForm] = useState(EMPTY_PROJECT);
  const [areaForm, setAreaForm] = useState(EMPTY_AREA);

  const [savingProject, setSavingProject] = useState(false);
  const [savingArea, setSavingArea] = useState(false);
  const [newProject, setNewProject] = useState(EMPTY_PROJECT);
  const [newArea, setNewArea] = useState(EMPTY_AREA);
  const [creatingProject, setCreatingProject] = useState(false);
  const [creatingArea, setCreatingArea] = useState(false);
  const [newProjectImage, setNewProjectImage] = useState(null);
  const [newAreaImage, setNewAreaImage] = useState(null);
  const [notice, setNotice] = useState(null); // { type: "ok" | "error" | "pending", text }

  const currentProjectRow = projects.find((p) => p.external_id === projectExternalId) || null;
  const currentAreaRow = areas.find((a) => a.external_id === areaExternalId) || null;
  const projectRevisionRef = useRef(null);
  const areaRevisionRef = useRef(null);

  // Đổ dữ liệu dự án/phân khu đang chọn vào form.
  useEffect(() => {
    setProjectForm(
      currentProjectRow
        ? {
            name: currentProjectRow.name,
            launch_date: currentProjectRow.launch_date,
            headline: currentProjectRow.headline ?? "",
            introduce: currentProjectRow.introduce ?? "",
          }
        : EMPTY_PROJECT,
    );
    projectRevisionRef.current = currentProjectRow?.source_revision ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectExternalId, currentProjectRow?.source_revision]);

  useEffect(() => {
    setAreaForm(
      currentAreaRow
        ? {
            area_name: currentAreaRow.area_name,
            unit_type: currentAreaRow.unit_type,
            bedrooms: String(currentAreaRow.bedrooms),
            area_sqm: String(currentAreaRow.area_sqm),
            total_units: String(currentAreaRow.total_units),
            headline: currentAreaRow.headline ?? "",
            introduce: currentAreaRow.introduce ?? "",
          }
        : EMPTY_AREA,
    );
    areaRevisionRef.current = currentAreaRow?.source_revision ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [areaExternalId, currentAreaRow?.source_revision]);

  // --- lưu -----------------------------------------------------------------

  async function attachImage(kind, ownerId, file) {
    if (!file) return "";
    try {
      await createImage(kind, ownerId, file);
      return "";
    } catch (e) {
      return ` Ảnh chưa đính được: ${errorText(e)}`;
    }
  }

  async function addProject(e) {
    e.preventDefault();
    setCreatingProject(true);
    setNotice({ type: "pending", text: "Đang gửi tới Mini CRM…" });
    try {
      const created = await createProjectInMiniCrm({
        name: newProject.name,
        launch_date: newProject.launch_date,
      });
      const externalId = created.record.external_id;
      setNotice({ type: "pending", text: `Mini CRM đã nhận "${created.record.name}" — đang chờ backend đồng bộ…` });

      const synced = await watchSync(async () => {
        const row = await getProjectByExternalId(externalId).catch(() => null);
        return !!row;
      });

      setNewProject(EMPTY_PROJECT);
      setNewProjectImage(null);
      e.target.reset();
      scope.setProjectExternalId(externalId);
      setNotice(
        synced
          ? { type: "ok", text: `Đã tạo và đồng bộ dự án "${created.record.name}".` }
          : {
              type: "pending",
              text: `Đã tạo dự án "${created.record.name}" ở Mini CRM — backend chưa kịp đồng bộ, tải lại trang sau ít phút.`,
            },
      );
    } catch (e2) {
      setNotice({
        type: "error",
        text: isAuthError(e2)
          ? `Không tạo được dự án: ${errorText(e2)} (cần token Mini CRM vai trò admin).`
          : errorText(e2),
      });
    } finally {
      setCreatingProject(false);
    }
  }

  async function addArea(e) {
    e.preventDefault();
    if (!projectExternalId) return;
    setCreatingArea(true);
    setNotice({ type: "pending", text: "Đang gửi tới Mini CRM…" });
    try {
      const createdArea = await createAreaInMiniCrm({
        external_project_id: projectExternalId,
        area_name: newArea.area_name,
        unit_type: newArea.unit_type,
        bedrooms: Number(newArea.bedrooms),
        area_sqm: Number(newArea.area_sqm),
        total_units: Number(newArea.total_units),
      });
      const externalId = createdArea.record.external_id;

      const synced = await watchSync(async () => {
        const row = await getAreaByExternalId(externalId).catch(() => null);
        return !!row;
      });

      await attachImage("area", externalId, newAreaImage).catch(() => "");
      setNewArea(EMPTY_AREA);
      setNewAreaImage(null);
      e.target.reset();
      setNotice(
        synced
          ? { type: "ok", text: "Đã tạo và đồng bộ phân khu." }
          : { type: "pending", text: "Đã tạo phân khu ở Mini CRM — backend chưa kịp đồng bộ, tải lại trang sau ít phút." },
      );
    } catch (e2) {
      setNotice({
        type: "error",
        text: isAuthError(e2)
          ? `Không tạo được phân khu: ${errorText(e2)} (kiểm tra vai trò và phạm vi dự án của token Mini CRM).`
          : errorText(e2),
      });
    } finally {
      setCreatingArea(false);
    }
  }

  async function saveProject(e) {
    e.preventDefault();
    if (!projectExternalId) return;
    setSavingProject(true);
    setNotice({ type: "pending", text: "Đang lưu…" });
    try {
      // Trường CANONICAL (name/launch_date) -> Mini CRM. Trường hiển thị
      // (headline/introduce) -> backend. Hai lời gọi độc lập, cả hai đều
      // "chỉ những gì đổi", không gộp làm một request.
      await Promise.all([
        updateProjectInMiniCrm(projectExternalId, {
          name: projectForm.name,
          launch_date: projectForm.launch_date,
        }),
        updateProject(currentProjectRow.project_id, {
          headline: projectForm.headline,
          introduce: projectForm.introduce,
        }),
      ]);
      setNotice({ type: "ok", text: "Đã lưu — trường canonical đang chờ backend đồng bộ, headline/introduce đã lưu ngay." });
    } catch (e2) {
      setNotice({ type: "error", text: errorText(e2) });
    } finally {
      setSavingProject(false);
    }
  }

  async function saveArea(e) {
    e.preventDefault();
    if (!areaExternalId) return;
    setSavingArea(true);
    setNotice({ type: "pending", text: "Đang lưu…" });
    try {
      await Promise.all([
        updateAreaInMiniCrm(areaExternalId, {
          area_name: areaForm.area_name,
          unit_type: areaForm.unit_type,
          bedrooms: Number(areaForm.bedrooms),
          area_sqm: Number(areaForm.area_sqm),
          total_units: Number(areaForm.total_units),
        }),
        updateArea(currentAreaRow.area_id, {
          headline: areaForm.headline,
          introduce: areaForm.introduce,
        }),
      ]);
      setNotice({ type: "ok", text: "Đã lưu — trường canonical đang chờ backend đồng bộ, headline/introduce đã lưu ngay." });
    } catch (e2) {
      setNotice({ type: "error", text: errorText(e2) });
    } finally {
      setSavingArea(false);
    }
  }

  // --- giao diện -----------------------------------------------------------

  if (scope.loadingProjects) return <div style={S.wrap}>Đang tải danh mục…</div>;

  return (
    <div style={{ ...S.wrap, padding: `${space(7)}px ${pick(bp, layout.gutter)}px ${space(16)}px` }}>
      <header style={S.head}>
        <h1 style={S.h1}>Danh mục</h1>
        <p style={S.sub}>
          Sửa thông tin dự án và phân khu. Trường nghiệp vụ ghi qua Mini CRM; nội dung hiển thị ghi thẳng backend.
        </p>
      </header>

      {notice && <div style={{ ...S.notice, ...noticeStyle(notice.type) }}>{notice.text}</div>}

      {/* --- Tạo dự án --- */}
      <section style={S.block}>
        <h2 style={S.h2}>Tạo dự án</h2>
        <p style={S.sub}>Cần token Mini CRM vai trò admin — tạo dự án mở rộng phạm vi, không nằm trong phạm vi có sẵn của bất kỳ token nào.</p>
        <form onSubmit={addProject}>
          <label style={S.label}>
            Tên dự án
            <input
              style={S.input}
              value={newProject.name}
              onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
              placeholder="Ví dụ: Khu đô thị Xanh"
            />
          </label>
          <label style={S.label}>
            Ngày mở bán
            <input
              style={S.input}
              type="date"
              value={newProject.launch_date}
              onChange={(e) => setNewProject({ ...newProject, launch_date: e.target.value })}
            />
          </label>
          <ImagePicker label="Ảnh bìa (không bắt buộc, đính sau khi đã đồng bộ)" disabled={creatingProject} onPick={setNewProjectImage} />
          <button style={S.button} type="submit" disabled={creatingProject}>
            {creatingProject ? "Đang tạo…" : "Tạo dự án"}
          </button>
        </form>
      </section>

      {projects.length === 0 && (
        <p style={S.sub}>Chưa thấy dự án nào trong phạm vi của bạn. Tạo dự án đầu tiên ở khối bên trên, hoặc kết nối bằng token có phạm vi rộng hơn.</p>
      )}

      {/* --- Chọn + sửa dự án --- */}
      {projects.length > 0 && (
      <section style={S.block}>
        <h2 style={S.h2}>Sửa dự án</h2>
        <ProjectSelector
          projects={projects}
          value={projectExternalId}
          onChange={scope.setProjectExternalId}
          loading={scope.loadingProjects}
          status={scope.projectsStatus}
        />

        {currentProjectRow && (
        <form onSubmit={saveProject} style={{ marginTop: space(4) }}>
          <label style={S.label}>
            Tên dự án
            <input
              style={S.input}
              value={projectForm.name}
              onChange={(e) => setProjectForm({ ...projectForm, name: e.target.value })}
            />
          </label>
          <label style={S.label}>
            Ngày mở bán
            <input
              style={S.input}
              type="date"
              value={projectForm.launch_date}
              onChange={(e) => setProjectForm({ ...projectForm, launch_date: e.target.value })}
            />
          </label>
          <ContentFields value={projectForm} onChange={setProjectForm} disabled={savingProject} />
          <button style={S.button} type="submit" disabled={savingProject || !projectExternalId}>
            {savingProject ? "Đang lưu…" : "Lưu dự án"}
          </button>
        </form>
        )}

        {currentProjectRow && (
          <ImageManager
            kind="project"
            ownerId={currentProjectRow.project_id}
            currentUrl={currentProjectRow.cover_image_url}
            onChanged={() => {}}
          />
        )}
      </section>
      )}

      {/* --- Tạo phân khu --- */}
      {projectExternalId && (
      <section style={S.block}>
        <h2 style={S.h2}>Tạo phân khu</h2>
        <form onSubmit={addArea}>
          <label style={S.label}>
            Tên phân khu
            <input
              style={S.input}
              value={newArea.area_name}
              onChange={(e) => setNewArea({ ...newArea, area_name: e.target.value })}
              placeholder="Ví dụ: Tower A"
            />
          </label>
          <label style={S.label}>
            Loại căn
            <input
              style={S.input}
              value={newArea.unit_type}
              onChange={(e) => setNewArea({ ...newArea, unit_type: e.target.value })}
              placeholder="Ví dụ: 2PN"
            />
          </label>
          <label style={S.label}>
            Số phòng ngủ
            <input
              style={S.input}
              type="number"
              min="0"
              value={newArea.bedrooms}
              onChange={(e) => setNewArea({ ...newArea, bedrooms: e.target.value })}
            />
          </label>
          <label style={S.label}>
            Diện tích (m²)
            <input
              style={S.input}
              type="number"
              min="0"
              step="0.1"
              value={newArea.area_sqm}
              onChange={(e) => setNewArea({ ...newArea, area_sqm: e.target.value })}
            />
          </label>
          <label style={S.label}>
            Tổng số căn
            <input
              style={S.input}
              type="number"
              min="0"
              value={newArea.total_units}
              onChange={(e) => setNewArea({ ...newArea, total_units: e.target.value })}
            />
          </label>
          <ImagePicker label="Ảnh bìa (không bắt buộc)" disabled={creatingArea} onPick={setNewAreaImage} />
          <button style={S.button} type="submit" disabled={creatingArea}>
            {creatingArea ? "Đang tạo…" : "Tạo phân khu"}
          </button>
        </form>
      </section>
      )}

      {/* --- Sửa phân khu --- */}
      {projectExternalId && (
      <section style={S.block}>
        <h2 style={S.h2}>Sửa phân khu</h2>
        {scope.loadingAreas ? (
          <p style={S.sub}>Đang tải phân khu…</p>
        ) : areas.length === 0 ? (
          <p style={S.sub}>Dự án này chưa có phân khu nào.</p>
        ) : (
          <>
            <label style={S.label}>
              Chọn phân khu
              <select
                style={S.input}
                value={areaExternalId || ""}
                onChange={(e) => scope.setAreaExternalId(e.target.value || null)}
              >
                <option value="" disabled>— Chọn phân khu —</option>
                {areas.map((a) => (
                  <option key={a.external_id || a.area_id} value={a.external_id || ""} disabled={!a.external_id}>
                    {a.area_name} — {a.unit_type}
                  </option>
                ))}
              </select>
            </label>

            {currentAreaRow && (
            <form onSubmit={saveArea}>
              <label style={S.label}>
                Tên phân khu
                <input
                  style={S.input}
                  value={areaForm.area_name}
                  onChange={(e) => setAreaForm({ ...areaForm, area_name: e.target.value })}
                />
              </label>
              <label style={S.label}>
                Loại căn
                <input
                  style={S.input}
                  value={areaForm.unit_type}
                  onChange={(e) => setAreaForm({ ...areaForm, unit_type: e.target.value })}
                />
              </label>
              <label style={S.label}>
                Số phòng ngủ
                <input
                  style={S.input}
                  type="number"
                  min="0"
                  value={areaForm.bedrooms}
                  onChange={(e) => setAreaForm({ ...areaForm, bedrooms: e.target.value })}
                />
              </label>
              <label style={S.label}>
                Diện tích (m²)
                <input
                  style={S.input}
                  type="number"
                  min="0"
                  step="0.1"
                  value={areaForm.area_sqm}
                  onChange={(e) => setAreaForm({ ...areaForm, area_sqm: e.target.value })}
                />
              </label>
              <label style={S.label}>
                Tổng số căn
                <input
                  style={S.input}
                  type="number"
                  min="0"
                  value={areaForm.total_units}
                  onChange={(e) => setAreaForm({ ...areaForm, total_units: e.target.value })}
                />
              </label>
              <ContentFields value={areaForm} onChange={setAreaForm} disabled={savingArea} />
              <button style={S.button} type="submit" disabled={savingArea || !areaExternalId}>
                {savingArea ? "Đang lưu…" : "Lưu phân khu"}
              </button>
            </form>
            )}

            {currentAreaRow && (
              <ImageManager kind="area" ownerId={currentAreaRow.area_id} currentUrl={currentAreaRow.cover_image_url} onChanged={() => {}} />
            )}
          </>
        )}
      </section>
      )}
    </div>
  );
}

/** Ô chọn ảnh dùng ở form TẠO — chỉ chọn file, chưa gửi đi.
 *  `accept` khớp danh sách đuôi mà backend chấp nhận (ALLOWED_IMAGE_SUFFIXES). */
function ImagePicker({ label, disabled, onPick }) {
  return (
    <label style={S.label}>
      {label}
      <input
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        disabled={disabled}
        onChange={(e) => onPick(e.target.files?.[0] ?? null)}
        style={{ ...S.input, padding: space(1) }}
      />
    </label>
  );
}

/** Quản lý ảnh bìa: xem – tải – thay – xoá. Dùng chung cho dự án và phân khu vì
 *  hai bên có cùng bộ endpoint, chỉ khác `kind`.
 *
 *  Mỗi bản ghi tối đa một ảnh: chưa có thì POST (tạo), đã có thì PUT (thay). */
function ImageManager({ kind, ownerId, currentUrl, onChanged }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const send = currentUrl ? replaceImage : createImage;
      await send(kind, ownerId, file);
      setFile(null);
      e.target.reset();
      await onChanged();
    } catch (e2) {
      setError(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteImage(kind, ownerId);
      await onChanged();
    } catch (e2) {
      setError(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={S.imageBox}>
      <div style={S.label}>Ảnh bìa</div>
      {currentUrl ? (
        <img src={currentUrl} alt="Ảnh bìa" style={S.preview} />
      ) : (
        <p style={S.sub}>Chưa có ảnh bìa.</p>
      )}

      <form onSubmit={submit}>
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif"
          disabled={busy}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          style={{ ...S.input, padding: space(1) }}
        />
        <button style={S.button} type="submit" disabled={busy || !file}>
          {busy ? "Đang xử lý…" : currentUrl ? "Thay ảnh" : "Tải ảnh"}
        </button>
        {currentUrl && (
          <button
            style={{ ...S.button, ...S.buttonDanger }}
            type="button"
            onClick={remove}
            disabled={busy}
          >
            Xoá ảnh
          </button>
        )}
      </form>

      {error && <p style={{ ...S.sub, color: color.danger }}>{error}</p>}
    </div>
  );
}

/** Hai ô nội dung hiển thị, dùng chung cho cả 4 form (tạo/sửa × dự án/phân khu).
 *  `introduce` là cột Text nên dùng textarea, `headline` giới hạn 255 ký tự. */
function ContentFields({ value, onChange, disabled }) {
  return (
    <>
      <label style={S.label}>
        Tiêu đề hiển thị
        <input
          style={S.input}
          maxLength={255}
          value={value.headline}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, headline: e.target.value })}
          placeholder="Không bắt buộc"
        />
      </label>
      <label style={S.label}>
        Mô tả
        <textarea
          style={{ ...S.input, minHeight: 80, resize: "vertical", fontFamily: "inherit" }}
          value={value.introduce}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, introduce: e.target.value })}
          placeholder="Không bắt buộc"
        />
      </label>
    </>
  );
}

function noticeStyle(type) {
  if (type === "ok") return { background: color.okSoft, color: color.ok };
  if (type === "pending") return { background: color.warnSoft, color: color.warn };
  return { background: color.dangerSoft, color: color.danger };
}

const S = {
  wrap: { maxWidth: layout.maxWidth, margin: "0 auto" },
  head: { marginBottom: space(6) },
  h1: { fontSize: size.h1, margin: 0, color: color.ink },
  h2: { fontSize: size.h2, margin: `0 0 ${space(3)}px`, color: color.ink },
  sub: { color: color.muted, fontSize: size.small },
  block: {
    background: color.surface,
    borderRadius: radius.md,
    padding: space(5),
    marginBottom: space(5),
    border: `1px solid ${color.border}`,
    boxShadow: shadow,
  },
  label: {
    display: "block",
    marginBottom: space(3),
    fontSize: size.small,
    color: color.ink,
    fontWeight: 600,
  },
  input: {
    display: "block",
    width: "100%",
    marginTop: space(1),
    padding: space(2),
    fontSize: size.small,
    borderRadius: radius.sm,
    border: `1px solid ${color.border}`,
    fontWeight: 400,
  },
  button: {
    marginTop: space(2),
    padding: `${space(2)}px ${space(5)}px`,
    borderRadius: radius.sm,
    border: "none",
    background: color.accent,
    color: "#fff",
    fontSize: size.small,
    fontWeight: 600,
    cursor: "pointer",
  },
  imageBox: {
    marginTop: space(4),
    paddingTop: space(4),
    borderTop: `1px solid ${color.border}`,
  },
  preview: {
    display: "block",
    maxWidth: "100%",
    maxHeight: 180,
    borderRadius: radius.sm,
    marginBottom: space(2),
    border: `1px solid ${color.border}`,
  },
  buttonDanger: { background: color.danger, marginLeft: space(2) },
  notice: {
    padding: space(3),
    borderRadius: radius.sm,
    marginBottom: space(4),
    fontSize: size.small,
  },
};
