import { ChevronLeft, ChevronRight } from "lucide-react";
export function Pagination({ page, pages, onPage, note }: { page: number; pages: number; onPage: (p: number) => void; note?: string }) {
  return (
    <div className="flex items-center justify-between px-4 py-3">
      <span className="text-sm text-ink-muted">{note}</span>
      <div className="flex items-center gap-1">
        <button onClick={() => onPage(Math.max(1, page - 1))} className="rounded-md border border-line-strong p-1.5 text-ink-muted hover:bg-surface-page disabled:opacity-40" disabled={page === 1}><ChevronLeft className="h-4 w-4" /></button>
        {Array.from({ length: pages }, (_, i) => i + 1).slice(0, 4).map((p) => (
          <button key={p} onClick={() => onPage(p)} className={`h-8 w-8 rounded-md text-sm font-medium ${p === page ? "bg-teal text-white" : "text-ink-muted hover:bg-surface-page"}`}>{p}</button>
        ))}
        <button onClick={() => onPage(Math.min(pages, page + 1))} className="rounded-md border border-line-strong p-1.5 text-ink-muted hover:bg-surface-page disabled:opacity-40" disabled={page === pages}><ChevronRight className="h-4 w-4" /></button>
      </div>
    </div>
  );
}
