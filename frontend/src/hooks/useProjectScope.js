// frontend/src/hooks/useProjectScope.js
// Phase F: ngữ cảnh Project → Area dùng chung cho mọi trang scoped.
//
// Ba nguyên tắc, khớp yêu cầu Phase F:
//   1. Chỉ hiện Project mà token hiện tại ĐƯỢC PHÉP — /v1/projects backend đã
//      TỰ lọc theo phạm vi (Phase E, tầng truy vấn); hook này không lọc lại,
//      chỉ hiển thị NGUYÊN VẸN những gì backend trả — lọc lần hai ở FE sẽ tạo
//      cảm giác an toàn giả (FE filtering is not security).
//   2. Phạm vi sống trong URL (`?project=<external_id>&area=<external_id>`) —
//      deep link phục hồi đúng ngữ cảnh, không phải một biến trong bộ nhớ mất
//      khi tải lại trang.
//   3. Đổi Project RESET Area và dữ liệu scoped theo nó — không giữ lại area
//      của dự án cũ (đó chính là lớp lỗi "activeProjectId() cache toàn cục"
//      mà endpoints.js cũ mắc phải, xem ghi chú ở đó).
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { listAreasScoped, listProjects } from "../api/endpoints";
import { isAuthError } from "../api/client";

const PROJECT_PARAM = "project";
const AREA_PARAM = "area";

/**
 * @param {{ projectExternalId?: string }} [options] - khi trang truyền
 *   `projectExternalId` (dự án sống trong PATH, vd
 *   `/projects/:externalId/dashboard`), giá trị đó là NGUỒN SỰ THẬT cho dự án
 *   — query `?project=` bị bỏ qua để không có hai nơi cùng giữ một trạng
 *   thái. Không truyền gì (mặc định) giữ NGUYÊN hành vi cũ: dự án đọc từ
 *   `?project=`, như các trang dùng chung ngữ cảnh project đang dùng.
 */
export function useProjectScope({ projectExternalId: routeProjectExternalId } = {}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryProjectExternalId = searchParams.get(PROJECT_PARAM) || null;
  const projectExternalId = routeProjectExternalId ?? queryProjectExternalId;
  const areaExternalId = searchParams.get(AREA_PARAM) || null;

  const [projects, setProjects] = useState([]);
  const [areas, setAreas] = useState([]);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loadingAreas, setLoadingAreas] = useState(false);
  // "idle" | "ok" | "unauthorized" | "error"
  const [projectsStatus, setProjectsStatus] = useState("idle");
  const [areasStatus, setAreasStatus] = useState("idle");
  const setProjectExternalId = useCallback(
    (nextId) => {
      // Chế độ path-scoped (routeProjectExternalId có mặt): đổi dự án là
      // ĐIỀU HƯỚNG route, không phải sửa query — hook không biết gì về
      // router, nên trang gọi (vd AbsorptionDashboard) tự `navigate()` sang
      // path mới. Gọi hàm này trong chế độ đó là no-op có chủ đích, không
      // phải lỗi bị bỏ sót.
      if (routeProjectExternalId !== undefined) return;
      // Đổi Project → RESET Area: không có `area` cũ nào còn ý nghĩa dưới một
      // Project khác, giữ lại nó sẽ khiến bảng dữ liệu hiển thị lẫn phạm vi.
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (nextId) next.set(PROJECT_PARAM, nextId);
        else next.delete(PROJECT_PARAM);
        next.delete(AREA_PARAM);
        return next;
      });
    },
    [setSearchParams, routeProjectExternalId],
  );

  const setAreaExternalId = useCallback(
    (nextId) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (nextId) next.set(AREA_PARAM, nextId);
        else next.delete(AREA_PARAM);
        return next;
      });
    },
    [setSearchParams],
  );

  // 1) Danh sách Project được phép — nạp một lần, backend đã tự lọc phạm vi.
  useEffect(() => {
    let cancelled = false;
    setLoadingProjects(true);
    listProjects()
      .then((rows) => {
        if (cancelled) return;
        setProjects(rows || []);
        setProjectsStatus("ok");
      })
      .catch((e) => {
        if (cancelled) return;
        setProjectsStatus(isAuthError(e) ? "unauthorized" : "error");
      })
      .finally(() => {
        if (!cancelled) setLoadingProjects(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Resolve only IDs that the API actually returned. A stale URL/local state
  // must never trigger a scoped request for another project.
  useEffect(() => {
    if (routeProjectExternalId !== undefined || loadingProjects || projectsStatus !== "ok") return;
    const valid = projects.filter((project) => project.external_id);
    if (valid.some((project) => project.external_id === projectExternalId)) return;
    if (valid.length) {
      setProjectExternalId(valid[0].external_id);
    }
    // An empty project list is never auto-filled here — projects come from
    // MiniCRM sync only. Pages render their own "no project yet" empty state.
  }, [loadingProjects, projectExternalId, projects, projectsStatus, routeProjectExternalId, setProjectExternalId]);

  // 2) Danh sách Area của Project đang chọn — nạp lại mỗi khi Project đổi.
  useEffect(() => {
    if (!projectExternalId) {
      setAreas([]);
      setAreasStatus("idle");
      return;
    }
    let cancelled = false;
    setLoadingAreas(true);
    setAreasStatus("idle");
    listAreasScoped(projectExternalId)
      .then((rows) => {
        if (cancelled) return;
        setAreas(rows || []);
        setAreasStatus("ok");
      })
      .catch((e) => {
        if (cancelled) return;
        setAreas([]);
        setAreasStatus(isAuthError(e) ? "unauthorized" : "error");
      })
      .finally(() => {
        if (!cancelled) setLoadingAreas(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectExternalId]);

  useEffect(() => {
    if (!projectExternalId || loadingAreas || areasStatus !== "ok") return;
    const valid = areas.filter((area) => area.external_id);
    if (valid.some((area) => area.external_id === areaExternalId)) return;
    if (valid.length) {
      setAreaExternalId(valid[0].external_id);
    }
    // A project with zero areas (not yet synced, or genuinely empty) is never
    // papered over by switching to an isolated demo project — areas come from
    // MiniCRM sync only. Pages render their own "no area yet" empty state.
  }, [areaExternalId, areas, areasStatus, loadingAreas, projectExternalId, setAreaExternalId]);

  const currentProject = useMemo(
    () => projects.find((p) => p.external_id === projectExternalId) || null,
    [projects, projectExternalId],
  );
  const currentArea = useMemo(
    () => areas.find((a) => a.external_id === areaExternalId) || null,
    [areas, areaExternalId],
  );

  return {
    projects,
    areas,
    projectExternalId,
    areaExternalId,
    currentProject,
    currentArea,
    setProjectExternalId,
    setAreaExternalId,
    loadingProjects,
    loadingAreas,
    projectsStatus,
    areasStatus,
  };
}
