import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts";
import { Blocks, Tag, Handshake, Users2, ChevronRight, ChevronDown, Calendar } from "lucide-react";
import { fetchDashboard } from "../services";
import { StatCard } from "../components/ui/StatCard";
import { dealStage } from "../lib/status";
import { formatVNDFull } from "../lib/format";
import type { Deal, Kpi, Project } from "../types";

const KPI_ICONS = [<Blocks className="h-5 w-5" />, <Tag className="h-5 w-5" />, <Handshake className="h-5 w-5" />, <Users2 className="h-5 w-5" />];

export function Dashboard() {
  const [data, setData] = useState<{
    kpis: Kpi[]; salesTrend: { day: string; current: number; previous: number }[];
    unitStatus: { name: string; value: number; pct: number; color: string }[];
    recentDeals: Deal[]; featuredProject: Project;
  } | null>(null);

  useEffect(() => { fetchDashboard().then(setData); }, []);

  if (!data) return <div className="p-8 text-ink-muted">Đang tải…</div>;

  return (
    <div className="px-6 py-6">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-muted">Tổng quan hiệu suất bán hàng bất động sản theo thời gian thực.</p>
        </div>
        <div className="flex gap-3">
          <button className="btn-ghost"><span className="font-normal">Marina Vista Residences</span><ChevronDown className="h-4 w-4" /></button>
          <button className="btn-ghost"><Calendar className="h-4 w-4" /><span className="font-normal">1/5 – 31/5/2024</span></button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {data.kpis.map((k, i) => <StatCard key={k.label} kpi={k} icon={KPI_ICONS[i]} />)}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Sales Performance */}
        <div className="stat-card lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-display text-lg font-semibold text-ink">Hiệu suất bán hàng</h2>
            <div className="flex items-center gap-4 text-xs text-ink-muted">
              <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-full bg-teal" />Đã bán</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-full border border-dashed border-gold" />Kỳ trước</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={data.salesTrend} margin={{ left: -20, right: 8, top: 4 }}>
              <defs>
                <linearGradient id="fillTeal" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#17976E" stopOpacity={0.18} />
                  <stop offset="100%" stopColor="#17976E" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" vertical={false} />
              <XAxis dataKey="day" tick={{ fontSize: 11, fill: "#98A2B3" }} tickLine={false} axisLine={false} interval={4} />
              <YAxis tick={{ fontSize: 11, fill: "#98A2B3" }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={{ borderRadius: 10, border: "1px solid #E9ECF1", fontSize: 12 }} />
              <Area type="monotone" dataKey="current" stroke="#17976E" strokeWidth={2} fill="url(#fillTeal)" name="Đã bán" />
              <Line type="monotone" dataKey="previous" stroke="#C6982F" strokeWidth={1.5} strokeDasharray="4 4" dot={false} name="Kỳ trước" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Unit Status donut */}
        <div className="stat-card">
          <h2 className="mb-4 font-display text-lg font-semibold text-ink">Tình trạng sản phẩm</h2>
          <div className="flex items-center gap-4">
            <div className="relative">
              <ResponsiveContainer width={150} height={150}>
                <PieChart>
                  <Pie data={data.unitStatus} dataKey="value" innerRadius={48} outerRadius={70} paddingAngle={2} stroke="none">
                    {data.unitStatus.map((s) => <Cell key={s.name} fill={s.color} />)}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <span className="font-display text-xl font-bold text-ink">1.248</span>
                <span className="text-[10px] text-ink-muted">Tổng sản phẩm</span>
              </div>
            </div>
            <div className="flex-1 space-y-2.5">
              {data.unitStatus.map((s) => (
                <div key={s.name} className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 text-ink-muted">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: s.color }} />{s.name}
                  </span>
                  <span className="font-medium text-ink">{s.value} <span className="text-ink-faint">({s.pct}%)</span></span>
                </div>
              ))}
            </div>
          </div>
          <Link to="/units" className="mt-4 flex items-center justify-end gap-1 text-sm font-semibold text-teal hover:underline">
            Xem tất cả sản phẩm <ChevronRight className="h-4 w-4" />
          </Link>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Recent Deals */}
        <div className="stat-card overflow-hidden p-0 lg:col-span-2">
          <div className="flex items-center justify-between px-5 py-4">
            <h2 className="font-display text-lg font-semibold text-ink">Giao dịch gần đây</h2>
            <Link to="/deals" className="flex items-center gap-1 text-sm font-semibold text-teal hover:underline">Xem tất cả <ChevronRight className="h-4 w-4" /></Link>
          </div>
          <table className="w-full">
            <thead className="border-y border-line bg-surface-page">
              <tr><th className="th-cell">Mã GD</th><th className="th-cell">Sản phẩm</th><th className="th-cell">Dự án</th><th className="th-cell">Người mua</th><th className="th-cell">Giá trị</th><th className="th-cell">Trạng thái</th></tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.recentDeals.map((d) => {
                const st = dealStage[d.stage];
                return (
                  <tr key={d.id} className="hover:bg-surface-page">
                    <td className="td-cell font-medium">{d.id}</td>
                    <td className="td-cell">{d.unitCode}</td>
                    <td className="td-cell text-ink-muted">{d.projectName}</td>
                    <td className="td-cell">{d.buyerName}</td>
                    <td className="td-cell font-medium">{formatVNDFull(d.value)}</td>
                    <td className="td-cell"><span className="rounded-full px-2.5 py-1 text-xs font-medium" style={{ background: st.color + "1a", color: st.color }}>{st.label}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Project Snapshot */}
        <div className="stat-card">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-display text-lg font-semibold text-ink">Dự án nổi bật</h2>
            <Link to={`/projects/${data.featuredProject.id}`} className="flex items-center gap-1 text-sm font-semibold text-teal hover:underline">Xem dự án <ChevronRight className="h-4 w-4" /></Link>
          </div>
          <div className="relative overflow-hidden rounded-card">
            <img src={data.featuredProject.thumbnailUrl} alt="" className="h-40 w-full object-cover" />
            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-navy-900 to-transparent p-4">
              <p className="font-display text-lg font-semibold text-white">{data.featuredProject.name}</p>
              <p className="text-xs text-white/70">{data.featuredProject.tagline}</p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
            <div><p className="font-display text-lg font-semibold text-ink">1.248</p><p className="text-xs text-ink-muted">Tổng sản phẩm</p></div>
            <div><p className="font-display text-lg font-semibold text-ink">612</p><p className="text-xs text-ink-muted">Còn trống</p></div>
            <div><p className="font-display text-lg font-semibold text-ink">156</p><p className="text-xs text-ink-muted">Giao dịch mở</p></div>
            <div><p className="font-display text-lg font-semibold text-ink">286,4 tỷ</p><p className="text-xs text-ink-muted">GDV (ước tính)</p></div>
          </div>
        </div>
      </div>
    </div>
  );
}
