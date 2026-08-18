import { useState } from "react";
import { X } from "lucide-react";
import type { Unit } from "../../types";

const TYPES = ["1 Phòng ngủ", "2 Phòng ngủ", "3 Phòng ngủ", "2 Phòng ngủ Premium", "3 Phòng ngủ Premium"];
const STATUSES: { value: Unit["status"]; label: string }[] = [
  { value: "available", label: "Còn trống" },
  { value: "reserved", label: "Đã đặt chỗ" },
  { value: "sold", label: "Đã bán" },
];

export function UnitModal({ unit, onClose, onSave }: { unit?: Unit | null; onClose: () => void; onSave: (u: Partial<Unit>) => void }) {
  const [form, setForm] = useState<Partial<Unit>>(
    unit ?? { tower: "Paris 2", floor: 18, type: "2 Phòng ngủ", status: "available", sizeSqft: 900, price: 1_800_000_000 }
  );
  const [error, setError] = useState("");
  const set = (k: keyof Unit, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  function submit() {
    if (!form.code?.trim()) return setError("Vui lòng nhập mã căn");
    if (!form.price || form.price <= 0) return setError("Giá phải lớn hơn 0");
    onSave(form);
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-navy-900/40 p-4">
      <div className="w-full max-w-lg rounded-card bg-white shadow-panel">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h3 className="font-display text-xl font-semibold text-ink">{unit ? "Sửa sản phẩm" : "Thêm sản phẩm"}</h3>
          <button onClick={onClose} className="text-ink-faint hover:text-ink"><X className="h-5 w-5" /></button>
        </div>
        <div className="max-h-[70vh] space-y-4 overflow-y-auto px-6 py-5">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Mã căn"><input className="input" value={form.code ?? ""} onChange={(e) => set("code", e.target.value)} placeholder="P2-1805" /></Field>
            <Field label="Toà tháp"><input className="input" value={form.tower ?? ""} onChange={(e) => set("tower", e.target.value)} /></Field>
            <Field label="Tầng"><input type="number" className="input" value={form.floor ?? ""} onChange={(e) => set("floor", +e.target.value)} /></Field>
            <Field label="Loại căn">
              <select className="input" value={form.type} onChange={(e) => set("type", e.target.value)}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
            </Field>
            <Field label="Diện tích (sqft)"><input type="number" className="input" value={form.sizeSqft ?? ""} onChange={(e) => set("sizeSqft", +e.target.value)} /></Field>
            <Field label="Giá (VND)"><input type="number" className="input" value={form.price ?? ""} onChange={(e) => set("price", +e.target.value)} /></Field>
            <Field label="Hướng"><input className="input" value={form.facing ?? ""} onChange={(e) => set("facing", e.target.value)} /></Field>
            <Field label="Trạng thái">
              <select className="input" value={form.status} onChange={(e) => set("status", e.target.value as Unit["status"])}>{STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select>
            </Field>
          </div>
          <Field label="Ghi chú"><textarea className="input min-h-[70px]" value={form.notes ?? ""} onChange={(e) => set("notes", e.target.value)} /></Field>
          {error && <p className="text-sm text-status-red">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 border-t border-line px-6 py-4">
          <button onClick={onClose} className="btn-ghost">Huỷ</button>
          <button onClick={submit} className="btn-teal">{unit ? "Lưu thay đổi" : "Thêm sản phẩm"}</button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="mb-1.5 block text-sm font-medium text-ink">{label}</label>{children}</div>;
}
