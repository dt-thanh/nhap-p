import { useState } from "react";
import { X } from "lucide-react";

const STATUSES = [
  { value: "available", label: "Còn trống" },
  { value: "reserved", label: "Đã đặt chỗ" },
  { value: "sold", label: "Đã bán" },
  { value: "blocked", label: "Tạm khoá" },
];

interface UnitFormData {
  unit_code: string;
  unit_status: string;
  external_area_id?: string;
  area_name?: string;
  unit_type?: string;
}

interface Props {
  unit?: { id: string; code: string; status: string; tower?: string; type?: string } | null;
  areas: { id: string; name: string; type: string }[];
  onClose: () => void;
  onSave: (data: UnitFormData) => void;
}

export function UnitModal({ unit, areas, onClose, onSave }: Props) {
  const [unitCode, setUnitCode] = useState(unit?.code ?? "");
  const [unitStatus, setUnitStatus] = useState(unit?.status ?? "available");
  const [areaId, setAreaId] = useState(areas[0]?.id ?? "");
  const [error, setError] = useState("");

  function submit() {
    if (!unitCode.trim()) return setError("Vui lòng nhập mã căn");
    if (!unit && !areaId) return setError("Vui lòng chọn phân khu");

    const data: UnitFormData = {
      unit_code: unitCode.trim(),
      unit_status: unitStatus,
    };

    if (!unit) {
      // Creating: need area reference
      data.external_area_id = areaId;
    }

    onSave(data);
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-navy-900/40 p-4">
      <div className="w-full max-w-lg rounded-card bg-white shadow-panel">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h3 className="font-display text-xl font-semibold text-ink">
            {unit ? "Sửa sản phẩm" : "Thêm sản phẩm"}
          </h3>
          <button onClick={onClose} className="text-ink-faint hover:text-ink">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4 px-6 py-5">
          {!unit && (
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Phân khu</label>
              <select className="input" value={areaId} onChange={(e) => setAreaId(e.target.value)}>
                {areas.length === 0 && <option value="">— Tạo phân khu trước —</option>}
                {areas.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.type})
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Mã căn</label>
              <input
                className="input"
                value={unitCode}
                onChange={(e) => setUnitCode(e.target.value)}
                placeholder="P2-1805"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Trạng thái</label>
              <select className="input" value={unitStatus} onChange={(e) => setUnitStatus(e.target.value)}>
                {STATUSES.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
          </div>
          {error && <p className="text-sm text-status-red">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 border-t border-line px-6 py-4">
          <button onClick={onClose} className="btn-ghost">Huỷ</button>
          <button onClick={submit} className="btn-teal">
            {unit ? "Lưu thay đổi" : "Thêm sản phẩm"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default UnitModal;
