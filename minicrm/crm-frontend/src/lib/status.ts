import type { UnitStatus, ProjectStatus, DealStage, StaffStatus, AreaStatus } from "../types";

type Tone = "green" | "amber" | "gray" | "blue" | "red" | "teal" | "indigo";

export const unitStatus: Record<UnitStatus, { label: string; tone: Tone }> = {
  available: { label: "Còn trống", tone: "indigo" },
  reserved: { label: "Đã đặt chỗ", tone: "amber" },
  sold: { label: "Đã bán", tone: "green" },
};
export const projectStatus: Record<ProjectStatus, { label: string; tone: Tone }> = {
  active: { label: "Đang mở bán", tone: "green" },
  pre_launch: { label: "Sắp mở bán", tone: "blue" },
  planning: { label: "Đang lên kế hoạch", tone: "gray" },
};
export const areaStatus: Record<AreaStatus, { label: string; tone: Tone }> = {
  on_track: { label: "Đúng tiến độ", tone: "green" },
  behind: { label: "Chậm tiến độ", tone: "red" },
};
export const staffStatus: Record<StaffStatus, { label: string; tone: Tone }> = {
  active: { label: "Đang hoạt động", tone: "green" },
  on_leave: { label: "Đang nghỉ", tone: "amber" },
  inactive: { label: "Ngưng", tone: "gray" },
};
export const dealStage: Record<DealStage, { label: string; color: string }> = {
  new: { label: "Mới", color: "#6B7688" },
  contacted: { label: "Đã liên hệ", color: "#2A72C4" },
  qualified: { label: "Tiềm năng", color: "#17976E" },
  viewing: { label: "Xem nhà", color: "#C6982F" },
  booking: { label: "Đặt chỗ", color: "#8B5CF6" },
  won: { label: "Chốt thành công", color: "#16A34A" },
  lost: { label: "Thất bại", color: "#D0483C" },
};
