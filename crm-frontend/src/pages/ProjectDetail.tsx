import { useEffect, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  AreaChart, Area as RArea, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { ChevronRight, MapPin, Layers, Clock, MoreVertical } from "lucide-react";
import { fetchProjectById, fetchAreas } from "../services";
import { Badge } from "../components/ui/Badge";
import { areaStatus } from "../lib/status";
import { salesTrend } from "../mocks";
import type { Project, Area } from "../types";

const TABS = ["Tổng quan", "Phân khu", "Sản phẩm", "Giao dịch", "Đội sales"];

function MiniStat({ label, value, sub, dotColor }: { label: string; value: string; sub?: string; dotColor?: string }) {
  return (
    <div className="stat-card">
      <div className="flex items-center gap-2">
        {dotColor && <span className="h-2.5 w-2.5 rounded-full" style={{ background: dotColor }} />}
        <p className="text-sm text-ink-muted">{label}</p>
      </div>
      <p className="mt-1 font-display text-2xl font-semibold text-ink">{value}</p>
      {sub && <p className="mt-1 text-xs text-ink-faint">{sub}</p>}
    </div>
  );
}

export function ProjectDetail() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [areas, setAreas] = useState<Area[]>([]);
  const [tab, setTab] = useState(0);

  useEffect(() => {
    if (!projectId) return;
    let active = true; // bỏ qua kết quả nếu đổi projectId trước khi request về
    // fetchProjectById/fetchAreas là import tĩnh (ổn định) nên chỉ cần [projectId]
    fetchProjectById(projectId).then((p) => { if (active) setProject(p ?? null); });
    fetchAreas(projectId).then((a) => { if (active) setAreas(a); });
    return () => { active = false; };
  }, [projectId]);

  if (!project) return <div className="p-8 text-ink-muted">Đang tải…</div>;

  return (
    <div className="px-6 py-6">
      <nav className="mb-2 flex items-center gap-1.5 text-sm text-ink-muted">
        <Link to="/" className="hover:text-ink">Dashboard</Link><ChevronRight className="h-3.5 w-3.5" />
        <Link to="/projects" className="hover:text-ink">Dự án</Link><ChevronRight className="h-3.5 w-3.5" />
        <span className="font-medium text-ink">{project.name}</span><ChevronRight className="h-3.5 w-3.5" />
        <span className="font-medium text-ink">The Paris</span>
      </nav>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">{project.name} / The Paris</h1>
          <p className="mt-1 text-sm text-ink-muted">Tổng quan và hiệu suất phân khu trong dự án.</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
        <MiniStat label="Tổng sản phẩm" value="286" sub="Toàn dự án 2.148" />
        <MiniStat label="Còn trống" value="112" sub="39,2% phân khu" dotColor="#17976E" />
        <MiniStat label="Đã đặt chỗ" value="68" sub="23,8% phân khu" dotColor="#C6982F" />
        <MiniStat label="Đã bán" value="106" sub="37,1% phân khu" dotColor="#D8DCE3" />
        <MiniStat label="Giao dịch mở" value="24" sub="8,4% phân khu" />
      </div>

      <div className="mt-6 flex gap-6 border-b border-line">
        {TABS.map((t, i) => (
          <button key={t} onClick={() => setTab(i)} className={`flex items-center gap-2 border-b-2 pb-3 text-sm font-medium ${tab === i ? "border-teal text-teal" : "border-transparent text-ink-muted hover:text-ink"}`}>
            {t}
          </button>
        ))}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Area snapshot */}
        <div className="stat-card">
          <h2 className="mb-3 font-display text-lg font-semibold text-ink">Ảnh phân khu</h2>
          <img src={project.thumbnailUrl} alt="" className="h-40 w-full rounded-card object-cover" />
          <p className="mt-3 font-display text-lg font-semibold text-ink">The Paris</p>
          <p className="mt-1 text-sm text-ink-muted">Bộ sưu tập căn hộ lấy cảm hứng từ nét thanh lịch Paris và tiện nghi hiện đại.</p>
          <div className="mt-3 space-y-1.5 text-sm text-ink-muted">
            <p className="flex items-center gap-2"><MapPin className="h-4 w-4" />{project.name}</p>
            <p className="flex items-center gap-2"><Layers className="h-4 w-4" />Thấp tầng</p>
            <p className="flex items-center gap-2"><Clock className="h-4 w-4" />Bàn giao dự kiến: Q4 2026</p>
          </div>
          <button className="btn-ghost mt-4 w-full">Xem chi tiết phân khu <ChevronRight className="h-4 w-4" /></button>
        </div>

        {/* Sales trend */}
        <div className="stat-card">
          <h2 className="mb-4 font-display text-lg font-semibold text-ink">Xu hướng bán (Đã bán)</h2>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={salesTrend} margin={{ left: -22, right: 4, top: 4 }}>
              <defs><linearGradient id="pdFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#17976E" stopOpacity={0.16} /><stop offset="100%" stopColor="#17976E" stopOpacity={0} /></linearGradient></defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" vertical={false} />
              <XAxis dataKey="day" tick={{ fontSize: 10, fill: "#98A2B3" }} tickLine={false} axisLine={false} interval={6} />
              <YAxis tick={{ fontSize: 10, fill: "#98A2B3" }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={{ borderRadius: 10, border: "1px solid #E9ECF1", fontSize: 12 }} />
              <RArea type="monotone" dataKey="current" stroke="#17976E" strokeWidth={2} fill="url(#pdFill)" />
              <Line type="monotone" dataKey="previous" stroke="#C6982F" strokeWidth={1.5} strokeDasharray="4 4" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
          <div className="mt-2 flex items-center justify-between rounded-lg bg-surface-page px-3 py-2 text-sm">
            <span className="text-ink-muted">Kỳ này <b className="text-ink">106</b></span>
            <span className="text-ink-muted">Kỳ trước <b className="text-ink">92</b></span>
            <span className="font-semibold text-status-green">▲ 15,2%</span>
          </div>
        </div>

        {/* Area statistics */}
        <div className="stat-card">
          <h2 className="mb-3 font-display text-lg font-semibold text-ink">Thống kê phân khu</h2>
          <dl className="space-y-2 text-sm">
            {[["Tổng sản phẩm", "286"], ["Còn trống", "112 (39,2%)"], ["Đã đặt chỗ", "68 (23,8%)"], ["Đã bán", "106 (37,1%)"]].map(([k, v]) => (
              <div key={k} className="flex justify-between"><dt className="text-ink-muted">{k}</dt><dd className="font-medium text-ink">{v}</dd></div>
            ))}
            <div className="my-2 h-px bg-line" />
            {[["Giao dịch mở", "24 (8,4%)"], ["Giá TB/căn", "1,842 tỷ"], ["Giá trị đã bán", "195,1 tỷ"], ["Vận tốc bán (30N)", "3,5 căn/ngày"], ["Hấp thụ (30N)", "3,1%"], ["Tỷ lệ take-up", "0,82"]].map(([k, v]) => (
              <div key={k} className="flex justify-between"><dt className="text-ink-muted">{k}</dt><dd className="font-medium text-ink">{v}</dd></div>
            ))}
            <div className="mt-2 flex justify-between border-t border-line pt-2"><dt className="text-ink-muted">So với toàn dự án</dt><dd className="font-semibold text-status-green">▲ 12,3%</dd></div>
          </dl>
        </div>
      </div>

      {/* Sub-areas table */}
      <div className="mt-6 rounded-card border border-line bg-surface-card shadow-card">
        <div className="flex items-center justify-between px-5 py-4">
          <h2 className="font-display text-lg font-semibold text-ink">Phân khu / Toà tháp <span className="text-ink-faint">({areas.length})</span></h2>
          <Link to="/units" className="flex items-center gap-1 text-sm font-semibold text-teal hover:underline">Xem tất cả <ChevronRight className="h-4 w-4" /></Link>
        </div>
        <table className="w-full">
          <thead className="border-y border-line bg-surface-page">
            <tr>
              <th className="th-cell">Tên</th><th className="th-cell">Loại</th><th className="th-cell">Tổng SP</th>
              <th className="th-cell">Còn trống</th><th className="th-cell">Đặt chỗ</th><th className="th-cell">Đã bán</th>
              <th className="th-cell">GD mở</th><th className="th-cell">Vận tốc (30N)</th><th className="th-cell">Hấp thụ</th><th className="th-cell">Trạng thái</th><th className="th-cell"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {areas.map((a) => {
              const st = areaStatus[a.status];
              const pctA = Math.round((a.available / a.totalUnits) * 100);
              const pctR = Math.round((a.reserved / a.totalUnits) * 100);
              const pctS = Math.round((a.sold / a.totalUnits) * 100);
              return (
                <tr key={a.id} onClick={() => navigate("/units")} className="cursor-pointer hover:bg-surface-page">
                  <td className="td-cell">
                    <div className="flex items-center gap-2.5">
                      <img src={a.thumbnailUrl} alt="" className="h-9 w-9 rounded-md object-cover" />
                      <span className="font-medium">{a.name}</span>
                    </div>
                  </td>
                  <td className="td-cell text-ink-muted">{a.type}</td>
                  <td className="td-cell">{a.totalUnits}</td>
                  <td className="td-cell">{a.available} <span className="text-ink-faint">({pctA}%)</span></td>
                  <td className="td-cell">{a.reserved} <span className="text-ink-faint">({pctR}%)</span></td>
                  <td className="td-cell">{a.sold} <span className="text-ink-faint">({pctS}%)</span></td>
                  <td className="td-cell">{a.activeDeals}</td>
                  <td className="td-cell">{a.salesVelocity} căn/ngày</td>
                  <td className="td-cell">{a.absorption}%</td>
                  <td className="td-cell"><Badge tone={st.tone}>{st.label}</Badge></td>
                  <td className="td-cell"><button onClick={(e) => e.stopPropagation()} className="text-ink-faint hover:text-ink"><MoreVertical className="h-4 w-4" /></button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
