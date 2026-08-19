import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { fetchDeals, fetchDealKpis, fetchUnits, createDeal, deleteDeal } from "../services";
import { StatCard } from "../components/ui/StatCard";
import { DealModal } from "../components/deals/DealModal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { dealStage } from "../lib/status";
import type { Deal, DealStage, Kpi } from "../types";

const COLUMNS: DealStage[] = ["new", "contacted", "qualified", "viewing", "booking", "won", "lost"];

export function Deals() {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [units, setUnits] = useState<{ id: string; code: string }[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [deleting, setDeleting] = useState<Deal | null>(null);
  const [error, setError] = useState("");
  const [viewMode, setViewMode] = useState<"kanban" | "table">("table");

  function reload() {
    fetchDeals().then(setDeals).catch(() => setDeals([]));
    fetchDealKpis().then(setKpis).catch(() => setKpis([]));
    fetchUnits().then((u) => setUnits(u.map((x) => ({ id: x.id, code: x.code })))).catch(() => setUnits([]));
  }

  useEffect(() => { reload(); }, []);

  const byStage = (s: DealStage) => deals.filter((d) => d.stage === s);

  async function handleCreate(data: {
    external_unit_id: string;
    deal_status: string;
    reserved_at?: string | null;
    sold_at?: string | null;
    lost_at?: string | null;
  }) {
    try {
      setError("");
      await createDeal(data);
      setShowCreate(false);
      reload();
    } catch (e) { setError((e as Error).message); }
  }

  async function handleDelete() {
    if (!deleting) return;
    try {
      setError("");
      await deleteDeal(deleting.id);
      setDeleting(null);
      reload();
    } catch (e) { setError((e as Error).message); setDeleting(null); }
  }

  return (
    <div className="px-6 py-6">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">Pipeline giao dịch</h1>
          <p className="mt-1 text-sm text-ink-muted">Theo dõi và quản lý giao dịch bất động sản.</p>
        </div>
        <div className="flex gap-3">
          <button
            className={viewMode === "table" ? "btn-teal" : "btn-ghost"}
            onClick={() => setViewMode("table")}
          >Bảng</button>
          <button
            className={viewMode === "kanban" ? "btn-teal" : "btn-ghost"}
            onClick={() => setViewMode("kanban")}
          >Kanban</button>
          <button className="btn-teal" onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" /> Tạo giao dịch
          </button>
        </div>
      </div>

      {error && <div className="mb-4 rounded-lg bg-status-redbg px-4 py-2 text-sm text-status-red">{error}</div>}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 mb-6">
        {kpis.map((k) => <StatCard key={k.label} kpi={k} />)}
      </div>

      {viewMode === "table" ? (
        <div className="rounded-card border border-line bg-surface-card shadow-card">
          <table className="w-full">
            <thead className="border-b border-line bg-surface-page">
              <tr>
                <th className="th-cell">ID</th>
                <th className="th-cell">Căn hộ</th>
                <th className="th-cell">Trạng thái</th>
                <th className="th-cell">Ngày tạo</th>
                <th className="th-cell"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {deals.map((d) => {
                const st = dealStage[d.stage] ?? { label: d.stage, color: "#6B7688" };
                return (
                  <tr key={d.id} className="hover:bg-surface-page">
                    <td className="td-cell font-mono text-xs text-ink-muted">{d.id}</td>
                    <td className="td-cell font-medium">{d.unitCode}</td>
                    <td className="td-cell">
                      <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium" style={{ background: st.color + "18", color: st.color }}>
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: st.color }} />
                        {st.label}
                      </span>
                    </td>
                    <td className="td-cell text-ink-muted text-sm">
                      {d.createdAt ? new Date(d.createdAt).toLocaleDateString("vi-VN") : "—"}
                    </td>
                    <td className="td-cell">
                      <button onClick={() => setDeleting(d)} className="rounded p-1 text-ink-faint hover:bg-status-redbg hover:text-status-red" title="Xoá">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                );
              })}
              {deals.length === 0 && (
                <tr><td colSpan={5} className="py-12 text-center text-ink-muted">Chưa có giao dịch nào. Tạo căn hộ trước, rồi tạo giao dịch.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        /* Kanban view */
        <div className="flex gap-4 overflow-x-auto pb-4">
          {COLUMNS.map((col) => {
            const st = dealStage[col] ?? { label: col, color: "#6B7688" };
            const items = byStage(col);
            return (
              <div key={col} className="min-w-[260px] flex-1 rounded-card border border-line bg-surface-page p-3">
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm font-semibold" style={{ color: st.color }}>{st.label}</span>
                  <span className="rounded-full bg-surface-card px-2 py-0.5 text-xs text-ink-muted">{items.length}</span>
                </div>
                <div className="space-y-2">
                  {items.map((d) => (
                    <div key={d.id} className="rounded-lg border border-line bg-white p-3 shadow-sm">
                      <p className="font-mono text-xs text-ink-muted">{d.id}</p>
                      <p className="mt-1 text-sm font-medium text-ink">{d.unitCode}</p>
                      <div className="mt-2 flex justify-end">
                        <button onClick={() => setDeleting(d)} className="text-ink-faint hover:text-status-red">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showCreate && (
        <DealModal
          units={units}
          onClose={() => setShowCreate(false)}
          onSave={handleCreate}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Xoá giao dịch?"
          message={`Bạn có chắc muốn xoá giao dịch "${deleting.id}"?`}
          confirmLabel="Xoá"
          onConfirm={handleDelete}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
