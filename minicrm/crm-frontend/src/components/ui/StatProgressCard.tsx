import type { ReactNode } from "react";

// Biến thể của StatCard cho KPI có "tiến độ trên tổng" (vd 130/392 đã bán) —
// khác StatCard gốc (vốn cho KPI kiểu delta % so kỳ trước). Tách riêng thay vì
// nhồi thêm nhánh điều kiện vào StatCard vì hai ngữ nghĩa khác nhau: một cái so
// với QUÁ KHỨ, cái này so với TỔNG hiện tại.
type Tone = "blue" | "green" | "amber" | "indigo";

const TONE: Record<Tone, { icon: string; bar: string; text: string }> = {
  blue: { icon: "bg-status-bluebg text-status-blue", bar: "bg-status-blue", text: "text-ink" },
  green: { icon: "bg-status-greenbg text-status-green", bar: "bg-status-green", text: "text-ink" },
  amber: { icon: "bg-status-amberbg text-status-amber", bar: "bg-status-amber", text: "text-ink" },
  indigo: { icon: "bg-status-indigobg text-status-indigo", bar: "bg-status-indigo", text: "text-ink" },
};

export function StatProgressCard({
  icon,
  label,
  value,
  note,
  pct,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: number;
  note: string;
  pct: number;
  tone: Tone;
}) {
  const c = TONE[tone];
  return (
    <div className="stat-card group relative flex h-[150px] flex-col justify-between overflow-hidden">
      <div className={`pointer-events-none absolute -right-7 -top-8 h-24 w-24 rounded-full opacity-[0.08] transition-transform duration-300 group-hover:scale-125 ${c.bar}`} />
      <div className="flex items-center gap-2.5">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-[11px] ring-1 ring-inset ring-current/10 ${c.icon}`}>{icon}</div>
        <p className="text-[13px] font-medium text-ink-muted">{label}</p>
      </div>
      <div>
        <p className={`font-display text-[30px] font-bold leading-none tracking-[-0.04em] ${c.text}`}>{value.toLocaleString("vi-VN")}</p>
        <p className="mt-1.5 text-xs text-ink-faint">{note}</p>
      </div>
      <div className="h-1 w-full overflow-hidden rounded-full bg-line">
        <div className={`h-full rounded-full ${c.bar}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      </div>
    </div>
  );
}
