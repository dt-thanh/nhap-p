import { useState } from "react";
import { X } from "lucide-react";

interface Props {
  project?: { id: string; name: string; location?: string; launch_date?: string } | null;
  onClose: () => void;
  onSave: (data: { name: string; location?: string; launch_date: string }) => void;
}

export function ProjectModal({ project, onClose, onSave }: Props) {
  const [name, setName] = useState(project?.name ?? "");
  const [launchDate, setLaunchDate] = useState(
    project?.launch_date ?? new Date().toISOString().slice(0, 10),
  );
  const [location, setLocation] = useState(project?.location ?? "");
  const [error, setError] = useState("");

  function submit() {
    if (!name.trim()) return setError("Vui lòng nhập tên dự án");
    if (!launchDate) return setError("Vui lòng chọn ngày mở bán");
    onSave({ name: name.trim(), location: location.trim() || undefined, launch_date: launchDate });
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-navy-900/40 p-4">
      <div className="w-full max-w-md rounded-card bg-white shadow-panel">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h3 className="font-display text-xl font-semibold text-ink">
            {project ? "Sửa dự án" : "Tạo dự án"}
          </h3>
          <button onClick={onClose} className="text-ink-faint hover:text-ink">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4 px-6 py-5">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Tên dự án</label>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Ví dụ: Ocean Park 3"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Địa điểm</label>
            <input
              className="input"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Ví dụ: Xã Long Hưng, Văn Giang, Hưng Yên"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Ngày mở bán</label>
            <input
              type="date"
              className="input"
              value={launchDate}
              onChange={(e) => setLaunchDate(e.target.value)}
            />
          </div>
          {error && <p className="text-sm text-status-red">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 border-t border-line px-6 py-4">
          <button onClick={onClose} className="btn-ghost">Huỷ</button>
          <button onClick={submit} className="btn-teal">
            {project ? "Lưu thay đổi" : "Tạo dự án"}
          </button>
        </div>
      </div>
    </div>
  );
}
