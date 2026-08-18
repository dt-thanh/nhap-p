import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, ChevronDown, Users2, MoreVertical, Trophy, CalendarCheck, Wallet } from "lucide-react";
import { fetchDeals, fetchDealKpis } from "../services";
import { StatCard } from "../components/ui/StatCard";
import { Avatar } from "../components/ui/Avatar";
import { dealStage } from "../lib/status";
import { formatVNDFull } from "../lib/format";
import type { Deal, DealStage, Kpi } from "../types";

const COLUMNS: DealStage[] = ["new", "contacted", "qualified", "viewing", "booking", "won"];
const KPI_ICONS = [<Users2 className="h-5 w-5" />, <CalendarCheck className="h-5 w-5" />, <Trophy className="h-5 w-5" />, <Wallet className="h-5 w-5" />];

export function Deals() {
  const navigate = useNavigate();
  const [deals, setDeals] = useState<Deal[]>([]);
  const [kpis, setKpis] = useState<Kpi[]>([]);

  useEffect(() => {
    fetchDeals().then(setDeals);
    fetchDealKpis().then(setKpis);
  }, []);

  const byStage = (s: DealStage) => deals.filter((d) => d.stage === s);
  const colValue = (s: DealStage) => byStage(s).reduce((sum, d) => sum + d.value, 0);

  return (
    <div className="px-6 py-6">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">Pipeline giao dịch</h1>
          <p className="mt-1 text-sm text-ink-muted">Theo dõi và quản lý giao dịch bất động sản qua từng giai đoạn.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button className="btn-ghost"><span className="font-normal">Tất cả dự án</span><ChevronDown className="h-4 w-4" /></button>
          <button className="btn-ghost"><span className="font-normal">Tất cả phân khu</span><ChevronDown className="h-4 w-4" /></button>
          <button className="btn-ghost"><Users2 className="h-4 w-4" /><span className="font-normal">Tất cả sales</span><ChevronDown className="h-4 w-4" /></button>
          <button className="btn-teal"><Plus className="h-4 w-4" /> Tạo giao dịch</button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k, i) => <StatCard key={k.label} kpi={k} icon={KPI_ICONS[i]} />)}
      </div>

      <div className="mt-6 flex gap-4 overflow-x-auto pb-4">
        {COLUMNS.map((s) => {
          const st = dealStage[s];
          const items = byStage(s);
          return (
            <div key={s} className="flex w-72 shrink-0 flex-col">
              <div className="rounded-t-card border-t-2 bg-surface-card px-4 py-3" style={{ borderColor: st.color }}>
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-ink">{st.label}</span>
                  <span className="rounded-full bg-surface-page px-2 py-0.5 text-xs font-medium text-ink-muted">{items.length}</span>
                </div>
                <p className="text-xs text-ink-faint">{formatVNDFull(colValue(s))}</p>
              </div>
              <div className="flex-1 space-y-3 rounded-b-card bg-surface-page/60 p-3">
                {items.map((d) => (
                  <button key={d.id} onClick={() => navigate(`/deals/${d.id}`)} className="w-full rounded-card border border-line bg-white p-3 text-left shadow-card transition-shadow hover:shadow-panel">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="text-sm font-semibold text-ink">{d.buyerName}</p>
                        <p className="text-xs text-ink-muted">{d.unitCode}</p>
                      </div>
                      {s === "won"
                        ? <span className="rounded-full bg-status-greenbg px-2 py-0.5 text-[10px] font-semibold text-status-green">Đã chốt</span>
                        : <MoreVertical className="h-4 w-4 text-ink-faint" />}
                    </div>
                    <p className="mt-2 text-sm font-semibold text-ink">{formatVNDFull(d.value)}</p>
                    <p className="text-xs text-ink-muted">{d.projectName}</p>
                    <div className="mt-2 flex items-center gap-1.5 border-t border-line pt-2">
                      <Avatar src={d.assignedTo.avatarUrl} name={d.assignedTo.name} size={20} />
                      <span className="text-xs text-ink-muted">{d.assignedTo.name}</span>
                    </div>
                  </button>
                ))}
                <button className="flex w-full items-center justify-center gap-1 rounded-lg border border-dashed border-line-strong py-2 text-sm font-medium text-teal hover:bg-teal-soft/40">
                  <Plus className="h-4 w-4" /> Thêm giao dịch
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
