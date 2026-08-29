import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { SyncBadge } from "../components/ui/SyncBadge";
import { Plus, Search, Pencil, Trash2 } from "lucide-react";
import { fetchUnits, createUnit, updateUnit, deleteUnit } from "../services";
import { apiGet } from "../services/api";
import type { BEArea } from "../services/adapters";
import { Badge } from "../components/ui/Badge";
import { UnitModal } from "../components/units/UnitModal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { unitStatus as unitStatusMap } from "../lib/status";
import type { Unit } from "../types";

export function Units() {
  // Đọc sẵn `?q=` khi được điều hướng tới từ ô tìm kiếm trên Topbar — chỉ lấy
  // giá trị lúc mount, không đồng bộ 2 chiều với URL sau đó.
  const [searchParams] = useSearchParams();
  const [units, setUnits] = useState<Unit[]>([]);
  const [areas, setAreas] = useState<{ id: string; name: string; type: string }[]>([]);
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [statusFilter, setStatusFilter] = useState("all");
  const [modalUnit, setModalUnit] = useState<Unit | null | undefined>(undefined); // undefined=closed, null=create, Unit=edit
  const [deleting, setDeleting] = useState<Unit | null>(null);
  const [error, setError] = useState("");

  function reload() {
    fetchUnits({ search, status: statusFilter }).then(setUnits).catch(() => setUnits([]));
    apiGet<BEArea[]>("/areas").then((a) =>
      setAreas(a.map((x) => ({ id: x.external_id, name: x.area_name, type: x.unit_type })))
    ).catch(() => setAreas([]));
  }

  useEffect(() => { reload(); }, [search, statusFilter]);

  async function handleSave(data: { unit_code: string; unit_status: string; external_area_id?: string; area_name?: string; unit_type?: string }) {
    try {
      setError("");
      if (modalUnit) {
        await updateUnit(modalUnit.id, data);
      } else {
        await createUnit(data);
      }
      setModalUnit(undefined);
      reload();
    } catch (e) { setError((e as Error).message); }
  }

  async function handleDelete() {
    if (!deleting) return;
    try {
      setError("");
      await deleteUnit(deleting.id);
      setDeleting(null);
      reload();
    } catch (e) { setError((e as Error).message); setDeleting(null); }
  }

  return (
    <div className="px-6 py-6">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">Sản phẩm</h1>
          <p className="mt-1 text-sm text-ink-muted">Quản lý toàn bộ căn hộ / sản phẩm trong hệ thống.</p>
        </div>
        <button className="btn-teal" onClick={() => setModalUnit(null)}>
          <Plus className="h-4 w-4" /> Thêm sản phẩm
        </button>
      </div>

      {error && <div className="mb-4 rounded-lg bg-status-redbg px-4 py-2 text-sm text-status-red">{error}</div>}

      <div className="rounded-card border border-line bg-surface-card shadow-card">
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} className="input pl-9" placeholder="Tìm theo mã hoặc loại…" />
          </div>
          <select className="input w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="all">Tất cả trạng thái</option>
            <option value="available">Còn trống</option>
            <option value="reserved">Đã đặt chỗ</option>
            <option value="sold">Đã bán</option>
          </select>
        </div>

        <table className="w-full">
          <thead className="border-b border-line bg-surface-page">
            <tr>
              <th className="th-cell">ID</th>
              <th className="th-cell">Mã căn</th>
              <th className="th-cell">Phân khu</th>
              <th className="th-cell">Loại</th>
              <th className="th-cell">Trạng thái</th>
              <th className="th-cell">Đồng bộ</th>
              <th className="th-cell"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {units.map((u) => {
              const st = unitStatusMap[u.status] ?? { label: u.status, tone: "gray" as const };
              return (
                <tr key={u.id} className="hover:bg-surface-page">
                  <td className="td-cell font-mono text-xs text-ink-muted">{u.id}</td>
                  <td className="td-cell font-semibold">{u.code}</td>
                  <td className="td-cell text-ink-muted">{u.tower}</td>
                  <td className="td-cell text-ink-muted">{u.type}</td>
                  <td className="td-cell"><Badge tone={st.tone} dot>{st.label}</Badge></td>
                  <td className="td-cell"><SyncBadge sync={u.sync} /></td>
                  <td className="td-cell">
                    <div className="flex gap-1">
                      <button onClick={() => setModalUnit(u)} className="rounded p-1 text-ink-faint hover:bg-surface-page hover:text-ink" title="Sửa">
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button onClick={() => setDeleting(u)} className="rounded p-1 text-ink-faint hover:bg-status-redbg hover:text-status-red" title="Xoá">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {units.length === 0 && (
              <tr><td colSpan={7} className="py-12 text-center text-ink-muted">Chưa có sản phẩm nào. Tạo phân khu trước, rồi thêm sản phẩm.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {modalUnit !== undefined && (
        <UnitModal
          unit={modalUnit}
          areas={areas}
          onClose={() => setModalUnit(undefined)}
          onSave={handleSave}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Xoá sản phẩm?"
          message={`Bạn có chắc muốn xoá sản phẩm "${deleting.code}" (${deleting.id})? Thao tác này là xoá mềm.`}
          confirmLabel="Xoá"
          onConfirm={handleDelete}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
