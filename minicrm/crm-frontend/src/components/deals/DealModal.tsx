import { useState } from "react";
import { X } from "lucide-react";

const DEAL_STATUSES = [
  { value: "lead", label: "Mới (Lead)" },
  { value: "qualified", label: "Tiềm năng" },
  { value: "interested", label: "Quan tâm" },
  { value: "viewing", label: "Xem nhà" },
  { value: "reserved", label: "Đặt chỗ" },
  { value: "sold", label: "Đã bán" },
  { value: "lost", label: "Thất bại" },
];

interface Props {
  deal?: { id: string; external_unit_id: string; deal_status: string } | null;
  units: { id: string; code: string }[];
  onClose: () => void;
  onSave: (data: {
    external_unit_id: string;
    deal_status: string;
    reserved_at?: string | null;
    sold_at?: string | null;
    lost_at?: string | null;
  }) => void;
}

export function DealModal({ deal, units, onClose, onSave }: Props) {
  const [unitId, setUnitId] = useState(deal?.external_unit_id ?? (units[0]?.id ?? ""));
  const [status, setStatus] = useState(deal?.deal_status ?? "lead");
  const [reservedAt, setReservedAt] = useState("");
  const [soldAt, setSoldAt] = useState("");
  const [lostAt, setLostAt] = useState("");
  const [error, setError] = useState("");

  function submit() {
    if (!unitId) return setError("Vui lòng chọn căn hộ");
    if (status === "reserved" && !reservedAt)
      return setError("Trạng thái 'Đặt chỗ' yêu cầu ngày reserved_at");
    if (status === "sold" && !soldAt)
      return setError("Trạng thái 'Đã bán' yêu cầu ngày sold_at");

    onSave({
      external_unit_id: unitId,
      deal_status: status,
      reserved_at: reservedAt ? new Date(reservedAt).toISOString() : null,
      sold_at: soldAt ? new Date(soldAt).toISOString() : null,
      lost_at: lostAt ? new Date(lostAt).toISOString() : null,
    });
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-navy-900/40 p-4">
      <div className="w-full max-w-lg rounded-card bg-white shadow-panel">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h3 className="font-display text-xl font-semibold text-ink">
            {deal ? "Sửa giao dịch" : "Tạo giao dịch"}
          </h3>
          <button onClick={onClose} className="text-ink-faint hover:text-ink">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4 px-6 py-5">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Căn hộ</label>
            <select className="input" value={unitId} onChange={(e) => setUnitId(e.target.value)} disabled={!!deal}>
              {units.length === 0 && <option value="">— Chưa có căn nào —</option>}
              {units.map((u) => (
                <option key={u.id} value={u.id}>{u.code} ({u.id})</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Trạng thái</label>
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              {DEAL_STATUSES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">reserved_at</label>
              <input type="datetime-local" className="input text-xs" value={reservedAt} onChange={(e) => setReservedAt(e.target.value)} />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">sold_at</label>
              <input type="datetime-local" className="input text-xs" value={soldAt} onChange={(e) => setSoldAt(e.target.value)} />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">lost_at</label>
              <input type="datetime-local" className="input text-xs" value={lostAt} onChange={(e) => setLostAt(e.target.value)} />
            </div>
          </div>
          {error && <p className="text-sm text-status-red">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 border-t border-line px-6 py-4">
          <button onClick={onClose} className="btn-ghost">Huỷ</button>
          <button onClick={submit} className="btn-teal">
            {deal ? "Lưu thay đổi" : "Tạo giao dịch"}
          </button>
        </div>
      </div>
    </div>
  );
}
