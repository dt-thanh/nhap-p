import { CheckCircle2, Clock, AlertTriangle } from "lucide-react";
import type { SyncState } from "../../types";

/** Huy hiệu trạng thái đồng bộ sang AbsorbIQ (mục 9).
 *
 *  CỐ Ý nhỏ. Đây không phải màn hình giám sát — nó trả lời đúng MỘT câu hỏi mà
 *  người dùng CRM thật sự gặp: "vì sao tôi chưa tạo được giao dịch cho căn này?"
 *  Chi tiết vận hành (số lần thử, HTTP status, dead-letter) nằm ở `/outbox`,
 *  không kéo vào đây. */
export function SyncBadge({ sync, showDetail = false }: { sync?: SyncState; showDetail?: boolean }) {
  if (!sync) return null;

  const config = {
    synced: {
      Icon: CheckCircle2,
      label: "Đã đồng bộ",
      className: "bg-status-greenbg text-status-green",
      title: `Đã mirror sang AbsorbIQ ở revision ${sync.mirroredRevision}. Có thể tạo giao dịch.`,
    },
    pending: {
      Icon: Clock,
      label: "Chờ đồng bộ",
      className: "bg-status-amberbg text-status-amber",
      title:
        `Revision ${sync.sourceRevision} chưa được AbsorbIQ xác nhận` +
        (sync.mirroredRevision !== null ? ` (đã mirror tới ${sync.mirroredRevision})` : "") +
        ". Chưa tạo được giao dịch cho tới khi đồng bộ xong.",
    },
    failed: {
      Icon: AlertTriangle,
      label: "Đồng bộ lỗi",
      className: "bg-status-redbg text-status-red",
      title: "Lần đẩy gần nhất thất bại. Xem sổ gửi đi (/outbox) để biết chi tiết.",
    },
  }[sync.status];

  const { Icon, label, className, title } = config;

  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${className}`}
    >
      <Icon className="h-3 w-3" />
      {label}
      {showDetail && sync.mirroredAt && (
        <span className="ml-1 font-normal opacity-75">
          {new Date(sync.mirroredAt).toLocaleString("vi-VN")}
        </span>
      )}
    </span>
  );
}
