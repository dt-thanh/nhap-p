import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronRight, Plus, Trash2 } from "lucide-react";
import { fetchProjectById, fetchAreas, createArea, deleteArea } from "../services";
import type { AreaCreateData } from "../services";
import { Badge } from "../components/ui/Badge";
import { AreaModal } from "../components/areas/AreaModal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import type { Project, Area } from "../types";

export function ProjectDetail() {
  const { projectId } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [areas, setAreas] = useState<Area[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [deleting, setDeleting] = useState<Area | null>(null);
  const [error, setError] = useState("");

  function reload() {
    if (!projectId) return;
    fetchProjectById(projectId).then((p) => setProject(p ?? null));
    fetchAreas(projectId).then(setAreas);
  }

  useEffect(() => { reload(); }, [projectId]);

  async function handleCreateArea(data: AreaCreateData & { external_project_id: string }) {
    try {
      setError("");
      await createArea(data);
      setShowCreate(false);
      reload();
    } catch (e) { setError((e as Error).message); }
  }

  async function handleDeleteArea() {
    if (!deleting) return;
    try {
      setError("");
      await deleteArea(deleting.id);
      setDeleting(null);
      reload();
    } catch (e) { setError((e as Error).message); setDeleting(null); }
  }

  if (!project) return <div className="p-8 text-ink-muted">Đang tải…</div>;

  return (
    <div className="px-6 py-6">
      <nav className="mb-2 flex items-center gap-1.5 text-sm text-ink-muted">
        <Link to="/" className="hover:text-ink">Dashboard</Link><ChevronRight className="h-3.5 w-3.5" />
        <Link to="/projects" className="hover:text-ink">Dự án</Link><ChevronRight className="h-3.5 w-3.5" />
        <span className="font-medium text-ink">{project.name}</span>
      </nav>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">{project.name}</h1>
          <p className="mt-1 text-sm text-ink-muted">{project.tagline}</p>
        </div>
        <button className="btn-teal" onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4" /> Thêm phân khu
        </button>
      </div>

      {error && <div className="mb-4 rounded-lg bg-status-redbg px-4 py-2 text-sm text-status-red">{error}</div>}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="stat-card"><p className="text-sm text-ink-muted">Phân khu</p><p className="mt-1 font-display text-2xl font-semibold text-ink">{areas.length}</p></div>
        <div className="stat-card"><p className="text-sm text-ink-muted">Tổng căn (kế hoạch)</p><p className="mt-1 font-display text-2xl font-semibold text-ink">{areas.reduce((s, a) => s + a.totalUnits, 0)}</p></div>
        <div className="stat-card"><p className="text-sm text-ink-muted">Trạng thái</p><p className="mt-1 font-display text-2xl font-semibold text-ink">{project.status === "active" ? "Đang hoạt động" : "Đã lưu trữ"}</p></div>
        <div className="stat-card"><p className="text-sm text-ink-muted">ID</p><p className="mt-1 font-mono text-sm text-ink">{project.id}</p></div>
      </div>

      {/* Areas table */}
      <div className="mt-6 rounded-card border border-line bg-surface-card shadow-card">
        <div className="flex items-center justify-between px-5 py-4">
          <h2 className="font-display text-lg font-semibold text-ink">Phân khu <span className="text-ink-faint">({areas.length})</span></h2>
        </div>
        <table className="w-full">
          <thead className="border-y border-line bg-surface-page">
            <tr>
              <th className="th-cell">ID</th>
              <th className="th-cell">Tên</th>
              <th className="th-cell">Loại</th>
              <th className="th-cell">Phòng ngủ</th>
              <th className="th-cell">Diện tích</th>
              <th className="th-cell">Tổng căn</th>
              <th className="th-cell">Trạng thái</th>
              <th className="th-cell"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {areas.map((a) => (
              <tr key={a.id} className="hover:bg-surface-page">
                <td className="td-cell font-mono text-xs text-ink-muted">{a.id}</td>
                <td className="td-cell font-medium">{a.name}</td>
                <td className="td-cell text-ink-muted">{a.type}</td>
                <td className="td-cell">{(a as any).bedrooms ?? "—"}</td>
                <td className="td-cell">{(a as any).area_sqm ?? "—"} m²</td>
                <td className="td-cell">{a.totalUnits}</td>
                <td className="td-cell">
                  <Badge tone={a.status === "on_track" ? "green" : "gray"}>
                    {a.status === "on_track" ? "Hoạt động" : a.status}
                  </Badge>
                </td>
                <td className="td-cell">
                  <button onClick={() => setDeleting(a)} className="rounded p-1 text-ink-faint hover:bg-status-redbg hover:text-status-red" title="Lưu trữ">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </td>
              </tr>
            ))}
            {areas.length === 0 && (
              <tr><td colSpan={8} className="py-12 text-center text-ink-muted">Chưa có phân khu nào. Nhấn "Thêm phân khu" để bắt đầu.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {showCreate && projectId && (
        <AreaModal
          projectId={projectId}
          onClose={() => setShowCreate(false)}
          onSave={handleCreateArea}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Lưu trữ phân khu?"
          message={`Bạn có chắc muốn lưu trữ phân khu "${deleting.name}"?`}
          confirmLabel="Lưu trữ"
          onConfirm={handleDeleteArea}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
