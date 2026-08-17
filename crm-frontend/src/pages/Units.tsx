import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Plus, Search, SlidersHorizontal, ChevronDown, X, Blocks, Heart, ShoppingBag, Users2,
  Pencil, Trash2, Building2, ArrowUpDown, Compass, Ruler, Tag, LayoutGrid,
} from "lucide-react";
import { fetchUnits, createUnit, updateUnit, deleteUnit } from "../services";
import { Badge } from "../components/ui/Badge";
import { UnitModal } from "../components/units/UnitModal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { unitStatus } from "../lib/status";
import { formatVNDFull, formatNumber } from "../lib/format";
import type { Unit } from "../types";

export function Units() {
  const navigate = useNavigate();
  const [units, setUnits] = useState<Unit[]>([]);
  const [selected, setSelected] = useState<Unit | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [modalUnit, setModalUnit] = useState<Unit | null | undefined>(undefined);
  const [deleting, setDeleting] = useState<Unit | null>(null);

  function reload() { fetchUnits({ search, status: statusFilter }).then(setUnits); }
  useEffect(() => {
    let active = true; // bỏ qua kết quả nếu effect đã bị thay thế (tránh race condition)
    fetchUnits({ search, status: statusFilter }).then((u) => { if (active) setUnits(u); });
    return () => { active = false; };
  }, [search, statusFilter]);

  async function handleSave(u: Partial<Unit>) {
    if (modalUnit) await updateUnit(modalUnit.id, u); else await createUnit(u);
    setModalUnit(undefined);
    reload();
  }
  async function handleDelete() {
    if (!deleting) return;
    await deleteUnit(deleting.id);
    if (selected?.id === deleting.id) setSelected(null);
    setDeleting(null);
    reload();
  }

  const kpis = [
    { label: "Tổng sản phẩm", value: "1.248", icon: <Blocks className="h-5 w-5" />, sub: "" },
    { label: "Còn trống", value: "612", icon: <Heart className="h-5 w-5" />, sub: "(49%)" },
    { label: "Đã đặt chỗ", value: "236", icon: <ShoppingBag className="h-5 w-5" />, sub: "(19%)" },
    { label: "Đã bán", value: "400", icon: <Users2 className="h-5 w-5" />, sub: "(32%)" },
  ];

  return (
    <div className="flex">
      <div className={`min-w-0 flex-1 px-6 py-6 ${selected ? "xl:pr-2" : ""}`}>
        <div className="mb-6 flex items-center gap-3">
          <h1 className="font-display text-3xl font-bold text-ink">Sản phẩm</h1>
          <button className="btn-ghost"><Building2 className="h-4 w-4" /><span className="font-normal">The Paris</span><ChevronDown className="h-4 w-4" /></button>
        </div>

        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {kpis.map((k) => (
            <div key={k.label} className="stat-card flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-full bg-teal-soft text-teal">{k.icon}</div>
              <div>
                <p className="text-sm text-ink-muted">{k.label}</p>
                <p className="font-display text-xl font-semibold text-ink">{k.value} <span className="text-sm font-normal text-ink-faint">{k.sub}</span></p>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-6 rounded-card border border-line bg-surface-card shadow-card">
          <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="input w-auto">
              <option value="all">Trạng thái: Tất cả</option><option value="available">Còn trống</option><option value="reserved">Đã đặt chỗ</option><option value="sold">Đã bán</option>
            </select>
            <select className="input w-auto"><option>Loại: Tất cả</option></select>
            <select className="input w-auto"><option>Toà: Tất cả</option></select>
            <select className="input w-auto"><option>Tầng: Tất cả</option></select>
            <div className="relative min-w-[200px] flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
              <input value={search} onChange={(e) => setSearch(e.target.value)} className="input pl-9" placeholder="Tìm theo mã căn hoặc loại…" />
            </div>
            <button className="btn-ghost"><SlidersHorizontal className="h-4 w-4" /> Bộ lọc</button>
            <button onClick={() => setModalUnit(null)} className="btn-teal"><Plus className="h-4 w-4" /> Thêm sản phẩm</button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-line bg-surface-page">
                <tr>
                  <th className="th-cell"><span className="flex items-center gap-1">Mã căn <ArrowUpDown className="h-3 w-3" /></span></th>
                  <th className="th-cell">Toà</th><th className="th-cell"><span className="flex items-center gap-1">Tầng <ArrowUpDown className="h-3 w-3" /></span></th>
                  <th className="th-cell">Loại</th><th className="th-cell">Diện tích</th><th className="th-cell">Giá (VND)</th>
                  <th className="th-cell">Trạng thái</th><th className="th-cell">Thao tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {units.slice(0, 10).map((u) => {
                  const st = unitStatus[u.status];
                  const active = selected?.id === u.id;
                  return (
                    <tr key={u.id} onClick={() => setSelected(u)} className={`cursor-pointer ${active ? "bg-teal-soft/50" : "hover:bg-surface-page"}`}>
                      <td className="td-cell">
                        <span className="flex items-center gap-2">
                          <span className={`h-3.5 w-3.5 rounded-full border-2 ${active ? "border-teal bg-teal" : "border-line-strong"}`} />
                          <span className="font-medium">{u.code}</span>
                        </span>
                      </td>
                      <td className="td-cell text-ink-muted">{u.tower}</td>
                      <td className="td-cell">{u.floor}</td>
                      <td className="td-cell">{u.type}</td>
                      <td className="td-cell">{formatNumber(u.sizeSqft)}</td>
                      <td className="td-cell font-medium">{formatVNDFull(u.price)}</td>
                      <td className="td-cell"><Badge tone={st.tone}>{st.label}</Badge></td>
                      <td className="td-cell">
                        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                          <button onClick={() => setModalUnit(u)} className="rounded p-1 text-ink-faint hover:bg-surface-page hover:text-teal"><Pencil className="h-4 w-4" /></button>
                          <button onClick={() => setDeleting(u)} className="rounded p-1 text-ink-faint hover:bg-surface-page hover:text-status-red"><Trash2 className="h-4 w-4" /></button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="px-4 py-3 text-sm text-ink-muted">Hiển thị 1–10 trong {formatNumber(1248)} sản phẩm</div>
        </div>
      </div>

      {/* Side detail panel */}
      {selected && (
        <div className="hidden w-80 shrink-0 border-l border-line bg-white px-5 py-6 xl:block">
          <div className="mb-4 flex items-start justify-between">
            <div className="flex items-center gap-2">
              <h3 className="font-display text-xl font-semibold text-ink">{selected.code}</h3>
              <Badge tone={unitStatus[selected.status].tone}>{unitStatus[selected.status].label}</Badge>
            </div>
            <button onClick={() => setSelected(null)} className="text-ink-faint hover:text-ink"><X className="h-5 w-5" /></button>
          </div>
          <img src={selected.imageUrl} alt="" className="h-40 w-full rounded-card object-cover" />
          <dl className="mt-4 space-y-3 text-sm">
            {[
              [<LayoutGrid className="h-4 w-4" />, "Mã căn", selected.code],
              [<Building2 className="h-4 w-4" />, "Toà", selected.tower],
              [<ArrowUpDown className="h-4 w-4" />, "Tầng", String(selected.floor)],
              [<Tag className="h-4 w-4" />, "Loại", selected.type],
              [<Ruler className="h-4 w-4" />, "Diện tích", `${formatNumber(selected.sizeSqft)} sqft`],
              [<Tag className="h-4 w-4" />, "Giá", formatVNDFull(selected.price)],
              [<Compass className="h-4 w-4" />, "Hướng", selected.facing ?? "—"],
            ].map(([icon, k, v], i) => (
              <div key={i} className="flex items-center justify-between">
                <dt className="flex items-center gap-2 text-ink-muted">{icon}{k}</dt>
                <dd className="font-medium text-ink">{v}</dd>
              </div>
            ))}
          </dl>
          {selected.notes && (
            <div className="mt-4">
              <p className="mb-1 text-sm font-semibold text-ink">Ghi chú</p>
              <p className="text-sm text-ink-muted">{selected.notes}</p>
            </div>
          )}
          <div className="mt-5 flex gap-2">
            <button onClick={() => setModalUnit(selected)} className="btn-ghost flex-1"><Pencil className="h-4 w-4" /> Sửa</button>
            <button onClick={() => navigate("/deals")} className="btn-teal flex-1"><Plus className="h-4 w-4" /> Tạo giao dịch</button>
          </div>
        </div>
      )}

      {modalUnit !== undefined && <UnitModal unit={modalUnit} onClose={() => setModalUnit(undefined)} onSave={handleSave} />}
      {deleting && <ConfirmDialog title="Xoá sản phẩm?" message={`Bạn chắc chắn muốn xoá căn ${deleting.code}? Hành động này không thể hoàn tác.`} onConfirm={handleDelete} onCancel={() => setDeleting(null)} />}
    </div>
  );
}
