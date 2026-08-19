import { Sparkles } from "lucide-react";

export function ComingSoon({ title }: { title: string }) {
  return (
    <div className="flex min-h-[70vh] flex-col items-center justify-center px-6 text-center">
      <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-teal-soft text-teal">
        <Sparkles className="h-7 w-7" />
      </div>
      <h1 className="font-display text-2xl font-bold text-ink">{title}</h1>
      <p className="mt-2 max-w-sm text-sm text-ink-muted">
        Tính năng này đang được phát triển và sẽ sớm ra mắt. Vui lòng quay lại sau.
      </p>
    </div>
  );
}
