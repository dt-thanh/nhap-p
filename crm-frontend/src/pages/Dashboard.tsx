import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Building2, Layers, ShoppingBag, Handshake } from "lucide-react";
import { fetchDashboard } from "../services";
import { StatCard } from "../components/ui/StatCard";
import { dealStage } from "../lib/status";
import type { Kpi, Deal } from "../types";

const KPI_ICONS = [
  <Layers className="h-5 w-5" />,
  <ShoppingBag className="h-5 w-5" />,
  <Handshake className="h-5 w-5" />,
  <Building2 className="h-5 w-5" />,
];

export function Dashboard() {
  const navigate = useNavigate();
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [recentDeals, setRecentDeals] = useState<Deal[]>([]);

  useEffect(() => {
    fetchDashboard().then((data) => {
      setKpis(data.kpis);
      setRecentDeals(data.recentDeals);
    });
  }, []);

  return (
    <div className="px-6 py-6">
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold text-ink">Dashboard</h1>
        <p className="mt-1 text-sm text-ink-muted">Tổng quan hệ thống MiniCRM.</p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k, i) => <StatCard key={k.label} kpi={k} icon={KPI_ICONS[i]} />)}
      </div>

      {/* Quick links */}
      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
        <button onClick={() => navigate("/projects")} className="stat-card text-left hover:ring-2 hover:ring-teal/30">
          <Building2 className="h-8 w-8 text-teal mb-2" />
          <h3 className="font-display text-lg font-semibold text-ink">Dự án</h3>
          <p className="text-sm text-ink-muted">Quản lý dự án, phân khu</p>
        </button>
        <button onClick={() => navigate("/units")} className="stat-card text-left hover:ring-2 hover:ring-teal/30">
          <Layers className="h-8 w-8 text-teal mb-2" />
          <h3 className="font-display text-lg font-semibold text-ink">Sản phẩm</h3>
          <p className="text-sm text-ink-muted">Quản lý căn hộ</p>
        </button>
        <button onClick={() => navigate("/deals")} className="stat-card text-left hover:ring-2 hover:ring-teal/30">
          <Handshake className="h-8 w-8 text-teal mb-2" />
          <h3 className="font-display text-lg font-semibold text-ink">Giao dịch</h3>
          <p className="text-sm text-ink-muted">Pipeline giao dịch</p>
        </button>
      </div>

      {/* Recent deals */}
      {recentDeals.length > 0 && (
        <div className="mt-6 rounded-card border border-line bg-surface-card shadow-card">
          <div className="px-5 py-4">
            <h2 className="font-display text-lg font-semibold text-ink">Giao dịch gần đây</h2>
          </div>
          <table className="w-full">
            <thead className="border-y border-line bg-surface-page">
              <tr>
                <th className="th-cell">ID</th>
                <th className="th-cell">Căn</th>
                <th className="th-cell">Trạng thái</th>
                <th className="th-cell">Ngày tạo</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {recentDeals.map((d) => {
                const st = dealStage[d.stage] ?? { label: d.stage, color: "#6B7688" };
                return (
                  <tr key={d.id} className="hover:bg-surface-page">
                    <td className="td-cell font-mono text-xs text-ink-muted">{d.id}</td>
                    <td className="td-cell font-medium">{d.unitCode}</td>
                    <td className="td-cell">
                      <span className="text-xs font-medium" style={{ color: st.color }}>{st.label}</span>
                    </td>
                    <td className="td-cell text-sm text-ink-muted">
                      {d.createdAt ? new Date(d.createdAt).toLocaleDateString("vi-VN") : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
