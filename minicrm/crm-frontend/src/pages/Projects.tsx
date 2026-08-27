import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Search, Building2, Handshake, Layers, Trash2 } from "lucide-react";
import { fetchProjects, fetchProjectKpis, createProject, updateProject, deleteProject } from "../services";
import { StatCard } from "../components/ui/StatCard";
import { Badge } from "../components/ui/Badge";
import { Pagination } from "../components/ui/Pagination";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { ProjectModal } from "../components/projects/ProjectModal";
import { formatNumber } from "../lib/format";
import type { Project, Kpi } from "../types";

const KPI_ICONS = [<Building2 className="h-5 w-5" />, <Layers className="h-5 w-5" />, <Layers className="h-5 w-5" />, <Handshake className="h-5 w-5" />];

export function Projects() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const [error, setError] = useState("");

  function reload() {
    fetchProjects(search).then(setProjects).catch(() => setProjects([]));
    fetchProjectKpis().then(setKpis).catch(() => setKpis([]));
  }

  useEffect(() => { reload(); }, [search]);

  async function handleCreate(data: { name: string; location?: string; launch_date: string }) {
    try {
      setError("");
      await createProject(data);
      setShowCreate(false);
      reload();
    } catch (e) { setError((e as Error).message); }
  }

  async function handleUpdate(data: { name: string; location?: string; launch_date: string }) {
    if (!editing) return;
    try {
      setError("");
      await updateProject(editing.id, data);
      setEditing(null);
      reload();
    } catch (e) { setError((e as Error).message); }
  }

  async function handleDelete() {
    if (!deleting) return;
    try {
      setError("");
      await deleteProject(deleting.id);
      setDeleting(null);
      reload();
    } catch (e) { setError((e as Error).message); setDeleting(null); }
  }

  return (
    <div className="px-6 py-6">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">Dự án</h1>
          <p className="mt-1 text-sm text-ink-muted">Quản lý và theo dõi toàn bộ dự án bất động sản trong danh mục.</p>
        </div>
        <button className="btn-teal" onClick={() => setShowCreate(true)}><Plus className="h-4 w-4" /> Tạo dự án</button>
      </div>

      {error && <div className="mb-4 rounded-lg bg-status-redbg px-4 py-2 text-sm text-status-red">{error}</div>}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k, i) => <StatCard key={k.label} kpi={k} icon={KPI_ICONS[i]} />)}
      </div>

      <div className="mt-6 rounded-card border border-line bg-surface-card shadow-card">
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} className="input pl-9" placeholder="Tìm dự án…" />
          </div>
        </div>

        <table className="w-full">
          <thead className="border-b border-line bg-surface-page">
            <tr>
              <th className="th-cell">ID</th>
              <th className="th-cell">Dự án</th>
              <th className="th-cell">Địa điểm</th>
              <th className="th-cell">Ngày mở bán</th>
              <th className="th-cell">Phân khu</th>
              <th className="th-cell">Tổng SP</th>
              <th className="th-cell">Trạng thái</th>
              <th className="th-cell"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {projects.map((p) => (
              <tr key={p.id} onClick={() => navigate(`/projects/${p.id}`)} className="cursor-pointer hover:bg-surface-page">
                <td className="td-cell font-mono text-xs text-ink-muted">{p.id}</td>
                <td className="td-cell font-semibold text-ink">{p.name}</td>
                <td className="td-cell text-ink-muted">{p.location}</td>
                <td className="td-cell text-ink-muted">{p.tagline}</td>
                <td className="td-cell">{p.areas}</td>
                <td className="td-cell">{formatNumber(p.totalUnits)}</td>
                <td className="td-cell">
                  <Badge tone={p.status === "active" ? "green" : "gray"} dot>
                    {p.status === "active" ? "Đang hoạt động" : "Đã lưu trữ"}
                  </Badge>
                </td>
                <td className="td-cell">
                  <div className="flex gap-1">
                    <button onClick={(e) => { e.stopPropagation(); setEditing(p); }} className="rounded p-1 text-ink-faint hover:bg-surface-page hover:text-ink" title="Sửa">✏️</button>
                    <button onClick={(e) => { e.stopPropagation(); setDeleting(p); }} className="rounded p-1 text-ink-faint hover:bg-status-redbg hover:text-status-red" title="Lưu trữ">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {projects.length === 0 && (
              <tr><td colSpan={8} className="py-12 text-center text-ink-muted">Chưa có dự án nào. Nhấn "Tạo dự án" để bắt đầu.</td></tr>
            )}
          </tbody>
        </table>
        <Pagination page={page} pages={Math.max(1, Math.ceil(projects.length / 20))} onPage={setPage} note={`${projects.length} dự án`} />
      </div>

      {showCreate && <ProjectModal onClose={() => setShowCreate(false)} onSave={handleCreate} />}
      {editing && (
        <ProjectModal
          project={{ id: editing.id, name: editing.name, location: editing.location, launch_date: editing.tagline?.replace("Ngày mở bán: ", "") }}
          onClose={() => setEditing(null)}
          onSave={handleUpdate}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Lưu trữ dự án?"
          message={`Bạn có chắc muốn lưu trữ dự án "${deleting.name}"? Dự án sẽ không bị xoá vĩnh viễn.`}
          confirmLabel="Lưu trữ"
          onConfirm={handleDelete}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
