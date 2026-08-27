import React, { useEffect, useRef, useState } from "react";
import { listEvidenceChunks } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { ErrorState, EmptyState, SectionState } from "./ui/States";
import { color, font, radius, size, space } from "../styles/tokens";

// Extraction runs as a background job (`POST .../extract`), so chunks don't
// exist yet the moment this mounts. `GET .../chunks` has no status field to
// distinguish "still processing" from "extraction failed" — polling on an
// empty result, capped at 60s, is the honest thing the frontend can do
// without a backend change.
//
// Capped by ATTEMPT COUNT, not wall-clock time: a `Date.now()`-based
// deadline is indistinguishable, from inside this component, from a clock
// that simply isn't advancing — 30 attempts at the fixed 2s interval is the
// same 60s budget without that ambiguity.
const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 30;

export default function ChunkViewer({ documentId }) {
  const result = useAsync(() => (documentId ? listEvidenceChunks(documentId) : Promise.resolve([])), [documentId]);
  const [timedOut, setTimedOut] = useState(false);
  const reloadRef = useRef(result.reload);
  reloadRef.current = result.reload;

  const hasChunks = (result.data?.length ?? 0) > 0;

  useEffect(() => setTimedOut(false), [documentId]);

  // ONE interval per (documentId, hasChunks, timedOut) combination — it keeps
  // firing on its own timer, independent of whether/when React re-renders in
  // between ticks, unlike a recursive setTimeout that depends on an effect
  // re-running to schedule the next one. Stops itself (cleanup) the moment
  // `hasChunks`/`timedOut` flip true and this effect re-runs.
  useEffect(() => {
    if (!documentId || hasChunks || timedOut) return undefined;
    let attempts = 0;
    const interval = setInterval(() => {
      attempts += 1;
      if (attempts >= MAX_POLL_ATTEMPTS) {
        clearInterval(interval);
        setTimedOut(true);
        return;
      }
      reloadRef.current();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [documentId, hasChunks, timedOut]);

  const stillWaiting = Boolean(documentId) && !result.loading && !result.error && !hasChunks && !timedOut;

  if (!documentId) return <EmptyState compact title="Chưa chọn tài liệu" />;
  if (result.error) return <ErrorState error={result.error} onRetry={result.reload} compact />;
  return (
    <section style={S.wrap} aria-label="Các đoạn bằng chứng">
      <div style={S.head}><h3 style={S.title}>Các đoạn đã trích xuất</h3><span style={S.count}>{result.data?.length ?? 0}</span></div>
      {stillWaiting && <div role="status" style={S.pollNotice}>Đang chờ trích xuất… (tự làm mới mỗi 2 giây)</div>}
      {timedOut && !hasChunks && (
        <div role="status" style={S.pollNotice}>
          Chưa có chunk sau 60 giây — có thể đang mất nhiều thời gian hoặc trích xuất đã thất bại.{" "}
          <button
            type="button"
            style={S.retryLink}
            onClick={() => { startedAtRef.current = null; setTimedOut(false); result.reload(); }}
          >
            Thử lại
          </button>
        </div>
      )}
      <SectionState loading={result.loading} empty={!result.loading && !hasChunks && !stillWaiting && !timedOut} emptyTitle="Chưa có chunk" compact>
        <div style={S.list}>{(result.data || []).map((chunk) => <article key={chunk.id} style={S.chunk}>
          <div style={S.meta}>#{chunk.chunk_index + 1} · trang {chunk.page_number ?? "—"} · {chunk.embedding_model}</div>
          <p style={S.content}>{chunk.content}</p>
        </article>)}</div>
      </SectionState>
    </section>
  );
}

const S = {
  wrap: { border: `1px solid ${color.border}`, borderRadius: radius.md, background: color.surface, padding: space(4) },
  head: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: space(2), borderBottom: `1px solid ${color.border}`, paddingBottom: space(3), marginBottom: space(3) },
  title: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2 },
  count: { color: color.muted, fontSize: size.tiny },
  list: { display: "grid", gap: space(3), maxHeight: 430, overflowY: "auto" },
  chunk: { padding: space(3), borderRadius: radius.sm, background: color.canvas },
  meta: { color: color.muted, fontSize: size.tiny, fontFamily: font.mono },
  content: { margin: `${space(2)}px 0 0`, color: color.body, fontSize: size.small, lineHeight: 1.55, whiteSpace: "pre-wrap" },
  pollNotice: { padding: space(3), marginBottom: space(3), borderRadius: radius.sm, background: color.warnSoft, color: color.body, fontSize: size.tiny, lineHeight: 1.5 },
  retryLink: { border: 0, background: "none", padding: 0, color: color.accent, fontWeight: 700, cursor: "pointer", fontFamily: "inherit", fontSize: "inherit", textDecoration: "underline" },
};
