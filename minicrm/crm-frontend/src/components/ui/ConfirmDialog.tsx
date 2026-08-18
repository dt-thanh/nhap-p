import { AlertTriangle } from "lucide-react";

export function ConfirmDialog({ title, message, confirmLabel = "Xoá", onConfirm, onCancel }: {
  title: string; message: string; confirmLabel?: string; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy-900/40 p-4">
      <div className="w-full max-w-sm rounded-card bg-white p-6 shadow-panel">
        <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-status-redbg text-status-red">
          <AlertTriangle className="h-5 w-5" />
        </div>
        <h3 className="font-display text-lg font-semibold text-ink">{title}</h3>
        <p className="mt-1 text-sm text-ink-muted">{message}</p>
        <div className="mt-5 flex justify-end gap-3">
          <button onClick={onCancel} className="btn-ghost">Huỷ</button>
          <button onClick={onConfirm} className="inline-flex items-center justify-center rounded-lg bg-status-red px-4 py-2.5 text-sm font-semibold text-white hover:opacity-90">{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
