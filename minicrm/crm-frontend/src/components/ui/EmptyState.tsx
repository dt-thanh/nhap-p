import type { ReactNode } from "react";

// Trạng thái rỗng dùng chung cho mọi card dashboard (status/areas/deals) —
// tránh mỗi nơi tự bịa một dòng text căn giữa khác nhau. KHÔNG render chart/
// bảng rỗng gây hiểu lầm là lỗi; đây LÀ trạng thái bình thường khi dự án chưa
// có dữ liệu.
export function EmptyState({
  icon,
  title,
  description,
  className = "py-10",
}: {
  icon: ReactNode;
  title: string;
  description?: string;
  className?: string;
}) {
  return (
    <div className={`flex flex-col items-center justify-center text-center ${className}`}>
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl border border-line bg-surface-raised text-ink-faint shadow-sm">
        {icon}
      </div>
      <p className="text-sm font-semibold text-ink">{title}</p>
      {description && <p className="mt-1.5 max-w-[260px] text-xs leading-5 text-ink-faint">{description}</p>}
    </div>
  );
}
