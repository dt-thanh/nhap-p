import React, { useState } from "react";
import { getMePermissions, removeAreaCoverImage, removeProjectCoverImage, uploadAreaCoverImage, uploadProjectCoverImage } from "../api/endpoints";
import { isAuthError } from "../api/client";
import ProjectSelector from "../components/ProjectSelector";
import { useProjectScope } from "../hooks/useProjectScope";
import { useAsync } from "../hooks/useAsync";
import { color, layout, radius, shadow, size, space } from "../styles/tokens";
import { areaLabel } from "../utils/areaLabel";

function errorText(error) {
  const detail = error?.body?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  return error?.message || "Có lỗi xảy ra. Vui lòng thử lại.";
}

export default function SettingsPage() {
  const permissions = useAsync(() => getMePermissions(), []);
  const scope = useProjectScope();
  const [notice, setNotice] = useState(null);

  if (permissions.loading) return <State text="Đang kiểm tra quyền quản trị…" />;
  if (permissions.error) {
    return <State text={isAuthError(permissions.error) ? "Bạn không có quyền truy cập phần Cài đặt." : "Không thể kiểm tra quyền truy cập phần Cài đặt."} error />;
  }
  if (permissions.data?.role !== "admin") {
    return <State text="Bạn không có quyền truy cập phần Cài đặt." error />;
  }

  async function saveImage(kind, row, file) {
    const upload = kind === "project" ? uploadProjectCoverImage : uploadAreaCoverImage;
    const result = await upload(kind === "project" ? row.project_id : row.area_id, file);
    setNotice({ type: "ok", text: `Đã cập nhật ảnh bìa ${kind === "project" ? "dự án" : "phân khu"}.` });
    return result.url;
  }

  async function removeImage(kind, row) {
    const remove = kind === "project" ? removeProjectCoverImage : removeAreaCoverImage;
    await remove(kind === "project" ? row.project_id : row.area_id);
    setNotice({ type: "ok", text: `Đã xoá ảnh bìa ${kind === "project" ? "dự án" : "phân khu"}.` });
  }

  return (
    <div style={S.wrap}>
      <header style={S.head}>
        <h1 style={S.h1}>Cài đặt</h1>
        <p style={S.sub}>Quản lý ảnh bìa dự án và phân khu. Chỉ quản trị viên có thể thay đổi ảnh.</p>
      </header>

      {notice && <div role="status" style={{ ...S.notice, ...(notice.type === "ok" ? S.noticeOk : S.noticeError) }}>{notice.text}</div>}

      <section style={S.block} aria-labelledby="project-images-heading">
        <h2 id="project-images-heading" style={S.h2}>Ảnh dự án</h2>
        {scope.loadingProjects ? <p style={S.sub}>Đang tải danh sách dự án…</p> : scope.projectsStatus === "error" ? <p role="alert" style={S.error}>Không tải được danh sách dự án.</p> : scope.projects.length === 0 ? <p style={S.sub}>Chưa có dự án trong phạm vi được cấp.</p> : (
          <div style={S.grid}>
            {scope.projects.map((project) => (
              <ImageCard key={project.project_id} kind="project" row={project} currentUrl={project.cover_image_url} onSave={saveImage} onRemove={removeImage} />
            ))}
          </div>
        )}
      </section>

      <section style={S.block} aria-labelledby="area-images-heading">
        <h2 id="area-images-heading" style={S.h2}>Ảnh phân khu</h2>
        <ProjectSelector
          projects={scope.projects}
          value={scope.projectExternalId}
          onChange={scope.setProjectExternalId}
          loading={scope.loadingProjects}
          status={scope.projectsStatus}
        />
        {!scope.projectExternalId ? <p style={S.sub}>Chọn dự án để quản lý ảnh phân khu.</p> : scope.loadingAreas ? <p style={S.sub}>Đang tải danh sách phân khu…</p> : scope.areasStatus === "error" ? <p role="alert" style={S.error}>Không tải được danh sách phân khu.</p> : scope.areas.length === 0 ? <p style={S.sub}>Dự án này chưa có phân khu.</p> : (
          <div style={S.grid}>
            {scope.areas.map((area) => (
              <ImageCard key={area.area_id} kind="area" row={area} currentUrl={area.cover_image_url} onSave={saveImage} onRemove={removeImage} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function ImageCard({ kind, row, currentUrl, onSave, onRemove }) {
  const [url, setUrl] = useState(currentUrl || null);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const name = kind === "project" ? row.name : areaLabel(row);

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const nextUrl = await onSave(kind, row, file);
      setUrl(nextUrl || null);
      setFile(null);
      event.target.reset();
    } catch (errorValue) {
      setError(errorText(errorValue));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await onRemove(kind, row);
      setUrl(null);
    } catch (errorValue) {
      setError(errorText(errorValue));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article style={S.card}>
      <div style={S.cardTop}>
        <div>
          <h3 style={S.h3}>{name || "Chưa có tên"}</h3>
          <div style={S.meta}>{kind === "project" ? row.external_id || "Chưa có external_id" : row.external_id || "Chưa có external_id"}</div>
        </div>
        <span style={url ? S.badgeOk : S.badgeMuted}>{url ? "Đã có ảnh" : "Chưa có ảnh"}</span>
      </div>
      <div style={url ? S.previewWrap : S.previewFallback}>
        {url ? <img src={url} alt={`Ảnh bìa ${name}`} style={S.preview} onError={(event) => { event.currentTarget.style.display = "none"; }} /> : "Chưa có ảnh bìa"}
      </div>
      <form onSubmit={submit} style={S.form}>
        <label style={S.fileLabel}>
          Chọn ảnh
          <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" disabled={busy} onChange={(event) => setFile(event.target.files?.[0] || null)} />
        </label>
        <div style={S.actions}>
          <button type="submit" disabled={busy || !file} style={S.button}>{busy ? "Đang xử lý…" : url ? "Thay ảnh" : "Tải ảnh"}</button>
          {url && <button type="button" disabled={busy} onClick={remove} style={{ ...S.button, ...S.buttonDanger }}>Xoá ảnh</button>}
        </div>
      </form>
      {error && <p role="alert" style={S.error}>{error}</p>}
    </article>
  );
}

function State({ text, error = false }) {
  return <div role={error ? "alert" : undefined} style={{ ...S.state, ...(error ? S.error : null) }}>{text}</div>;
}

const S = {
  wrap: { maxWidth: layout.maxWidth, margin: "0 auto" },
  head: { marginBottom: space(6) },
  h1: { margin: 0, fontSize: size.h1, color: color.ink },
  h2: { margin: `0 0 ${space(4)}px`, fontSize: size.h2, color: color.ink },
  h3: { margin: 0, fontSize: size.body, color: color.ink },
  sub: { color: color.muted, fontSize: size.small },
  block: { marginBottom: space(5), padding: space(5), background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: space(4), marginTop: space(5) },
  card: { minWidth: 0, padding: space(4), border: `1px solid ${color.border}`, borderRadius: radius.md, background: color.canvas },
  cardTop: { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: space(3), marginBottom: space(3) },
  meta: { marginTop: space(1), color: color.muted, fontSize: size.tiny },
  badgeOk: { color: color.ok, background: color.okSoft, borderRadius: radius.pill, padding: "3px 8px", fontSize: size.tiny, whiteSpace: "nowrap" },
  badgeMuted: { color: color.muted, background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.pill, padding: "3px 8px", fontSize: size.tiny, whiteSpace: "nowrap" },
  previewWrap: { height: 150, marginBottom: space(3), overflow: "hidden", borderRadius: radius.sm, background: color.surface },
  previewFallback: { height: 150, display: "grid", placeItems: "center", marginBottom: space(3), borderRadius: radius.sm, background: color.surface, color: color.muted, fontSize: size.small },
  preview: { width: "100%", height: "100%", objectFit: "cover" },
  form: { display: "grid", gap: space(2) },
  fileLabel: { display: "grid", gap: space(1), color: color.body, fontSize: size.small, fontWeight: 600 },
  actions: { display: "flex", flexWrap: "wrap", gap: space(2) },
  button: { padding: `${space(2)}px ${space(4)}px`, border: "none", borderRadius: radius.sm, background: color.accent, color: color.ink, fontWeight: 700, cursor: "pointer" },
  buttonDanger: { background: color.dangerSoft, color: color.danger },
  notice: { marginBottom: space(5), padding: space(3), borderRadius: radius.sm, fontSize: size.small },
  noticeOk: { color: color.ok, background: color.okSoft },
  noticeError: { color: color.danger, background: color.dangerSoft },
  error: { color: color.danger, fontSize: size.small },
  state: { padding: space(8), color: color.muted, textAlign: "center" },
};
