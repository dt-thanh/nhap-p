// frontend/src/hooks/useAsync.js
// ---------------------------------------------------------------------------
// Hook gọi API chuẩn hoá các TRẠNG THÁI mà spec yêu cầu:
//   loading · error (API/mạng) · empty · partial · missing values.
// Mọi section trong dashboard dùng chung hook này -> không lặp code trạng thái.
//
//   const { data, loading, error, reload } = useAsync(() => getX(args), [args]);
// ---------------------------------------------------------------------------
import { useEffect, useState, useCallback, useRef } from "react";

export function useAsync(fn, deps = []) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fn();
      if (mounted.current) setData(res);
    } catch (e) {
      if (mounted.current) {
        // Phân biệt lỗi mạng với lỗi API để UI hiển thị đúng thông điệp
        const isNetwork = e?.name === "TypeError" || e?.status === 0 || e?.status === undefined;
        setError({ message: e?.message || "Đã xảy ra lỗi", status: e?.status, network: isNetwork });
      }
    } finally {
      if (mounted.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => { run(); }, [run]);

  return { data, loading, error, reload: run };
}