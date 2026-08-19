import { useState } from "react";
import { X } from "lucide-react";

interface AreaFormData {
  area_name: string;
  unit_type: string;
  bedrooms: number;
  area_sqm: number;
  total_units: number;
}

interface Props {
  area?: { id: string; name: string; type: string } | null;
  projectId: string;
  onClose: () => void;
  onSave: (data: AreaFormData & { external_project_id: string }) => void;
}

const UNIT_TYPES = ["Low Rise", "High Rise", "Villa", "Townhouse", "Penthouse"];

export function AreaModal({ area, projectId, onClose, onSave }: Props) {
  const [areaName, setAreaName] = useState(area?.name ?? "");
  const [unitType, setUnitType] = useState(area?.type ?? UNIT_TYPES[0]);
  const [bedrooms, setBedrooms] = useState(2);
  const [areaSqm, setAreaSqm] = useState(65);
  const [totalUnits, setTotalUnits] = useState(50);
  const [error, setError] = useState("");

  function submit() {
    if (!areaName.trim()) return setError("Vui lòng nhập tên phân khu");
    if (!unitType.trim()) return setError("Vui lòng chọn loại căn");
    if (totalUnits < 0) return setError("Tổng căn phải ≥ 0");
    onSave({
      external_project_id: projectId,
      area_name: areaName.trim(),
      unit_type: unitType.trim(),
      bedrooms,
      area_sqm: areaSqm,
      total_units: totalUnits,
    });
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-navy-900/40 p-4">
      <div className="w-full max-w-lg rounded-card bg-white shadow-panel">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h3 className="font-display text-xl font-semibold text-ink">
            {area ? "Sửa phân khu" : "Thêm phân khu"}
          </h3>
          <button onClick={onClose} className="text-ink-faint hover:text-ink">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4 px-6 py-5">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Tên phân khu</label>
              <input className="input" value={areaName} onChange={(e) => setAreaName(e.target.value)} placeholder="Le Jardin" />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Loại căn</label>
              <select className="input" value={unitType} onChange={(e) => setUnitType(e.target.value)}>
                {UNIT_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Số phòng ngủ</label>
              <input type="number" className="input" value={bedrooms} onChange={(e) => setBedrooms(+e.target.value)} min={0} />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Diện tích (m²)</label>
              <input type="number" className="input" value={areaSqm} onChange={(e) => setAreaSqm(+e.target.value)} min={1} />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Tổng số căn</label>
              <input type="number" className="input" value={totalUnits} onChange={(e) => setTotalUnits(+e.target.value)} min={0} />
            </div>
          </div>
          {error && <p className="text-sm text-status-red">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 border-t border-line px-6 py-4">
          <button onClick={onClose} className="btn-ghost">Huỷ</button>
          <button onClick={submit} className="btn-teal">
            {area ? "Lưu thay đổi" : "Thêm phân khu"}
          </button>
        </div>
      </div>
    </div>
  );
}
