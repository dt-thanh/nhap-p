import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Search, SlidersHorizontal, MapPin, MoreVertical, Building2, Handshake, Layers } from "lucide-react";
import { fetchProjects, fetchProjectKpis } from "../services";
import { StatCard } from "../components/ui/StatCard";
import { Badge } from "../components/ui/Badge";
import { Avatar } from "../components/ui/Avatar";
import { Pagination } from "../components/ui/Pagination";
import { projectStatus } from "../lib/status";
import { formatNumber } from "../lib/format";
import type { Project, Kpi } from "../types";

const KPI_ICONS = [<Building2 className="h-5 w-5" />, <MapPin className="h-5 w-5" />, <Building2 className="h-5 w-5" />, <Layers className="h-5 w-5" />, <Handshake className="h-5 w-5" />];

export function Projects() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => { fetchProjectKpis().then(setKpis); }, []);
  useEffect(() => {
    let active = true; // tránh race condition khi gõ tìm nhanh
    fetchProjects(search).then((p) => { if (active) setProjects(p); });
    return () => { active = false; };
  }, [search]);

  return (
    <div className="px-6 py-6">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">Dự án</h1>
          <p className="mt-1 text-sm text-ink-muted">Quản lý và theo dõi toàn bộ dự án bất động sản trong danh mục.</p>
        </div>
        <button className="btn-teal"><Plus className="h-4 w-4" /> Tạo dự án</button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {kpis.map((k, i) => <StatCard key={k.label} kpi={k} icon={KPI_ICONS[i]} />)}
      </div>

      <div className="mt-6 rounded-card border border-line bg-surface-card shadow-card">
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} className="input pl-9" placeholder="Tìm dự án…" />
          </div>
          <select className="input w-auto"><option>Tất cả trạng thái</option></select>
          <select className="input w-auto"><option>Tất cả địa điểm</option></select>
          <select className="input w-auto"><option>Tất cả quản lý</option></select>
          <button className="btn-ghost"><SlidersHorizontal className="h-4 w-4" /> Bộ lọc</button>
          <button className="text-sm font-medium text-teal hover:underline">Xoá lọc</button>
        </div>

        <table className="w-full">
          <thead className="border-b border-line bg-surface-page">
            <tr>
              <th className="th-cell">Dự án</th><th className="th-cell">Địa điểm</th><th className="th-cell">Phân khu</th>
              <th className="th-cell">Tổng SP</th><th className="th-cell">Đã bán</th><th className="th-cell">GD mở</th>
              <th className="th-cell">Trạng thái</th><th className="th-cell">Quản lý</th><th className="th-cell"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {projects.map((p) => {
              const st = projectStatus[p.status];
              const pct = Math.round((p.soldUnits / p.totalUnits) * 100);
              return (
                <tr key={p.id} onClick={() => navigate(`/projects/${p.id}`)} className="cursor-pointer hover:bg-surface-page">
                  <td className="td-cell">
                    <div className="flex items-center gap-3">
                      <img src={p.thumbnailUrl} alt="" className="h-11 w-16 rounded-lg object-cover" />
                      <div>
                        <p className="font-semibold text-ink">{p.name}</p>
                        <p className="text-xs text-ink-muted">{p.tagline}</p>
                      </div>
                    </div>
                  </td>
                  <td className="td-cell"><span className="flex items-center gap-1 text-ink-muted"><MapPin className="h-3.5 w-3.5" />{p.location}</span></td>
                  <td className="td-cell">{p.areas}</td>
                  <td className="td-cell">{formatNumber(p.totalUnits)}</td>
                  <td className="td-cell">
                    <p className="font-medium">{formatNumber(p.soldUnits)} <span className="text-ink-faint">({pct}%)</span></p>
                    <div className="mt-1 h-1 w-20 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-teal" style={{ width: `${pct}%` }} /></div>
                  </td>
                  <td className="td-cell">{p.activeDeals}</td>
                  <td className="td-cell"><Badge tone={st.tone} dot>{st.label}</Badge></td>
                  <td className="td-cell">
                    <div className="flex items-center gap-2">
                      <Avatar src={p.manager.avatarUrl} name={p.manager.name} size={30} />
                      <div><p className="text-sm font-medium text-ink">{p.manager.name}</p><p className="text-xs text-ink-muted">{p.manager.title}</p></div>
                    </div>
                  </td>
                  <td className="td-cell"><button onClick={(e) => e.stopPropagation()} className="text-ink-faint hover:text-ink"><MoreVertical className="h-4 w-4" /></button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <Pagination page={page} pages={4} onPage={setPage} note={`Hiển thị 1–${projects.length} trong 16 dự án`} />
      </div>
    </div>
  );
}
