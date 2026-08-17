// frontend/src/components/dashboard/statusMap.js
// Ánh xạ trạng thái phân khu -> nhãn + màu. Dùng chung để nhất quán.
import { color } from "../../styles/tokens";

export const STATUS = {
  hot:    { label: "Bán chạy", fg: () => color.danger, bg: () => color.dangerSoft },
  normal: { label: "Ổn định",  fg: () => color.ok,     bg: () => color.okSoft },
  slow:   { label: "Bán chậm", fg: () => color.warn,   bg: () => color.warnSoft },
  new:    { label: "Mới mở",   fg: () => color.accent, bg: () => color.accentSoft },
};
