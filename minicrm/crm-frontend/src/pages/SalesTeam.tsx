import { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { Plus, Search, X, Users2, Handshake, TrendingUp, BadgeCheck, MoreVertical, CheckCircle2 } from "lucide-react";
import { fetchSalesTeam, fetchSalesTeamKpis } from "../services";
import { StatCard } from "../components/ui/StatCard";
import { Badge } from "../components/ui/Badge";
import { Avatar } from "../components/ui/Avatar";
import { staffStatus } from "../lib/status";
import { formatVNDFull } from "../lib/format";
import { staffPerformance, dealsMock } from "../mocks";
import { dealStage } from "../lib/status";
import type { SalesStaff, Kpi } from "../types";

const KPI_ICONS = [<Users2 className="h-5 w-5" />, <Handshake className="h-5 w-5" />, <BadgeCheck className="h-5 w-5" />, <TrendingUp className="h-5 w-5" />];

export function SalesTeam() {
  const [staff, setStaff] = useState<SalesStaff[]>([]);
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [selected, setSelected] = useState<SalesStaff | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => { fetchSalesTeamKpis().then(setKpis); }, []);
  useEffect(() => {
    let active = true; // tránh race condition khi gõ tìm nhanh
    fetchSalesTeam({ search }).then((s) => {
      if (!active) return;
      setStaff(s);
      // dùng functional update để không phụ thuộc biến `selected` (tránh stale closure)
      setSelected((cur) => cur ?? s[0] ?? null);
    });
    return () => { active = false; };
  }, [search]);

  return (
    <div className="flex">
      <div className="min-w-0 flex-1 px-6 py-6">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="font-display text-3xl font-bold text-ink">Đội ngũ Sales</h1>
            <p className="mt-1 text-sm text-ink-muted">Quản lý môi giới và nhân viên sales, theo dõi hiệu suất và thúc đẩy kết quả.</p>
          </div>
          <button className="btn-teal"><Plus className="h-4 w-4" /> Thêm nhân viên</button>
        </div>

        <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          {kpis.map((k, i) => <StatCard key={k.label} kpi={k} icon={KPI_ICONS[i]} />)}
        </div>

        <div className="mt-6 rounded-card border border-line bg-surface-card shadow-card">
          <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
            <div className="relative min-w-[220px] flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
              <input value={search} onChange={(e) => setSearch(e.target.value)} className="input pl-9" placeholder="Tìm theo tên, vai trò, dự án…" />
            </div>
            <select className="input w-auto"><option>Tất cả vai trò</option></select>
            <select className="input w-auto"><option>Tất cả dự án</option></select>
            <select className="input w-auto"><option>Tất cả trạng thái</option></select>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-line bg-surface-page">
                <tr>
                  <th className="th-cell">Nhân viên</th><th className="th-cell">Vai trò</th><th className="th-cell">Dự án phụ trách</th>
                  <th className="th-cell">GD mở</th><th className="th-cell">Đã chốt</th><th className="th-cell">Doanh thu (YTD)</th>
                  <th className="th-cell">Trạng thái</th><th className="th-cell"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {staff.map((s) => {
                  const st = staffStatus[s.status];
                  const active = selected?.id === s.id;
                  return (
                    <tr key={s.id} onClick={() => setSelected(s)} className={`cursor-pointer ${active ? "bg-teal-soft/40" : "hover:bg-surface-page"}`}>
                      <td className="td-cell">
                        <div className="flex items-center gap-2.5">
                          <Avatar src={s.avatarUrl} name={s.name} size={34} />
                          <div><p className="font-medium text-ink">{s.name}</p><p className="text-xs text-ink-muted">{s.email}</p></div>
                        </div>
                      </td>
                      <td className="td-cell">{s.role}</td>
                      <td className="td-cell text-ink-muted">{s.assignedProject}</td>
                      <td className="td-cell">{s.activeDeals}</td>
                      <td className="td-cell">{s.wonDeals}</td>
                      <td className="td-cell font-medium">{formatVNDFull(s.revenueYTD)}</td>
                      <td className="td-cell"><Badge tone={st.tone}>{st.label}</Badge></td>
                      <td className="td-cell"><button onClick={(e) => e.stopPropagation()} className="text-ink-faint hover:text-ink"><MoreVertical className="h-4 w-4" /></button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="px-4 py-3 text-sm text-ink-muted">Hiển thị 1–{staff.length} trong 24 kết quả</div>
        </div>
      </div>

      {/* Detail panel */}
      {selected && (
        <div className="hidden w-96 shrink-0 border-l border-line bg-white px-5 py-6 xl:block">
          <div className="mb-4 flex items-start justify-between">
            <div className="flex items-center gap-3">
              <Avatar src={selected.avatarUrl} name={selected.name} size={56} />
              <div>
                <p className="flex items-center gap-1.5 font-display text-lg font-semibold text-ink">{selected.name} <CheckCircle2 className="h-4 w-4 text-teal" /></p>
                <p className="text-sm text-ink-muted">{selected.role}</p>
                <p className="text-xs text-ink-faint">{selected.email}</p>
              </div>
            </div>
            <button onClick={() => setSelected(null)} className="text-ink-faint hover:text-ink"><X className="h-5 w-5" /></button>
          </div>

          <div className="grid grid-cols-4 gap-2 border-y border-line py-3 text-center">
            {[["GD mở", String(selected.activeDeals)], ["Đã chốt", String(selected.wonDeals)], ["Doanh thu", formatVNDFull(selected.revenueYTD)], ["Chuyển đổi", `${selected.conversionRate}%`]].map(([k, v]) => (
              <div key={k}><p className="font-display text-base font-semibold text-ink">{v}</p><p className="text-[10px] text-ink-muted">{k}</p></div>
            ))}
          </div>

          <div className="mt-4">
            <h4 className="mb-2 font-display text-base font-semibold text-ink">Tổng quan hiệu suất (YTD)</h4>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={staffPerformance} margin={{ left: -18, right: 4, top: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" vertical={false} />
                <XAxis dataKey="month" tick={{ fontSize: 10, fill: "#98A2B3" }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fontSize: 10, fill: "#98A2B3" }} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ borderRadius: 10, border: "1px solid #E9ECF1", fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="revenue" name="Doanh thu (tr)" stroke="#17976E" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="conversion" name="Chuyển đổi (%)" stroke="#C6982F" strokeWidth={2} strokeDasharray="4 4" dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4">
            <h4 className="mb-2 font-display text-base font-semibold text-ink">Giao dịch đang phụ trách</h4>
            <div className="space-y-2">
              {dealsMock.slice(0, 3).map((d) => {
                const stg = dealStage[d.stage];
                return (
                  <div key={d.id} className="flex items-center justify-between rounded-lg border border-line px-3 py-2 text-sm">
                    <div><p className="font-medium text-ink">{d.id}</p><p className="text-xs text-ink-muted">{d.projectName} · {d.unitCode}</p></div>
                    <span className="rounded-full px-2 py-0.5 text-[10px] font-medium" style={{ background: stg.color + "1a", color: stg.color }}>{stg.label}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
