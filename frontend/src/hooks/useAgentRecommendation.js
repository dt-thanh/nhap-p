// Phase 6: trigger + theo dõi một đề xuất tư vấn của AI Agent, CHỜ DUYỆT.
//
// Khớp quy ước `useAsync.js` đã có: state phẳng { data, loading, error }, lỗi
// được bọc thành object thường (không phải throw), không dùng thư viện state
// mới. Khác `useAsync` ở một điểm: đây là BA thao tác ghi (generate/approve/
// reject) trên CÙNG một state, không phải một lần đọc — nên viết tay thay vì
// tái dùng `useAsync` (nó chỉ khớp cho một lời gọi đọc duy nhất theo deps).
//
// `approve`/`reject` đòi `actor` — không có danh tính người dùng nào tự suy ra
// được ở tầng FE hiện tại (chưa có đăng nhập cá nhân hoá), nên nơi gọi (form
// duyệt) phải truyền tên người duyệt tường minh. Bịa một actor mặc định sẽ làm
// rỗng chính bước duyệt mà AGENTS.md yêu cầu.
import { useCallback, useState } from "react";
import { approveRecommendation, createRecommendation, executeRecommendation, rejectRecommendation } from "../api/endpoints";

export function useAgentRecommendation() {
  const [state, setState] = useState({ data: null, loading: false, error: null });

  const generate = useCallback(async (projectId, areaId) => {
    setState({ data: null, loading: true, error: null });
    try {
      const data = await createRecommendation(projectId, areaId);
      setState({ data, loading: false, error: null });
      return data;
    } catch (err) {
      setState({ data: null, loading: false, error: err });
      throw err;
    }
  }, []);

  const approve = useCallback(async (recommendationId, reason, actor) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await approveRecommendation(recommendationId, reason, actor);
      setState({ data, loading: false, error: null });
      return data;
    } catch (err) {
      setState((s) => ({ ...s, loading: false, error: err }));
      throw err;
    }
  }, []);

  const reject = useCallback(async (recommendationId, reason, actor) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await rejectRecommendation(recommendationId, reason, actor);
      setState({ data, loading: false, error: null });
      return data;
    } catch (err) {
      setState((s) => ({ ...s, loading: false, error: err }));
      throw err;
    }
  }, []);

  const execute = useCallback(async (recommendationId, actor) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const result = await executeRecommendation(recommendationId, actor);
      setState((s) => ({
        ...s, loading: false,
        data: { ...s.data, execution_status: result.status, executed_by: result.executed_by,
          executed_at: result.executed_at, execution_result: result.result },
      }));
      return result;
    } catch (error) {
      setState((s) => ({ ...s, loading: false, error }));
      throw error;
    }
  }, []);

  return { ...state, generate, approve, reject, execute };
}
