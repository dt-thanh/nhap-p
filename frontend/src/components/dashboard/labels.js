// Display-only Vietnamese labels and formatters for the absorption dashboard.
// Internal API keys, route values, and status codes stay unchanged.
export const DASHBOARD_TEXT = Object.freeze({
  dashboardTitle: "Bảng điều khiển tiêu thụ",
  dashboardSubtitle: "Theo dõi tốc độ bán theo dự án, phân khu và khoảng thời gian.",
  updatedAt: "Cập nhật lúc",
  loading: "Đang tải…",
  refresh: "Làm mới",
  selectProject: "Dự án",
  noProjects: "— Không có dự án —",
  selectArea: "Phân khu",
  allAreas: "Tất cả phân khu",
  dateRange: "Khoảng thời gian",
  range30d: "30 ngày",
  range90d: "90 ngày",
  range12m: "12 tháng",
  currentYear: "Năm hiện tại",
  selectYear: "Chọn năm",
  chooseYear: "— Chọn năm —",
  allHistory: "Toàn bộ lịch sử",
  customRange: "Khoảng thời gian tùy chỉnh",
  trendTitle: "Xu hướng tiêu thụ",
  trendSubtitle: "Số căn bán mỗi ngày · tốc độ bán theo ngày",
  unitsSoldPerDay: "Số căn bán mỗi ngày",
  unitsSoldPerMonth: "Số căn bán theo tháng",
  movingAverage7d: "Trung bình trượt 7 ngày",
  movingAverage30d: "Trung bình trượt 30 ngày",
  cumulativeSold: "Đã bán cộng dồn",
  sellThrough: "Tỷ lệ tiêu thụ",
  unitsSold: "Đã bán",
  remainingUnits: "Còn lại tổng cộng",
  availableRemainingUnits: "Có thể bán ngay",
  reservedUnits: "Đang giữ chỗ",
  remainingUnitsDefinition: "Tổng số căn chưa bán, bao gồm cả căn đang giữ chỗ.",
  availableRemainingUnitsDefinition: "Số căn có thể mở bán ngay, không gồm căn đã bán, đang giữ chỗ hoặc bị khóa.",
  reservedUnitsDefinition: "Số căn đang được giữ chỗ và chưa được tính là có thể bán ngay.",
  inventoryBreakdown: "Còn lại tổng cộng gồm căn có thể bán ngay và căn đang giữ chỗ; căn bị khóa được tách khỏi quỹ bán.",
  totalUnits: "Tổng số căn",
  velocity7d: "Tốc độ bán 7 ngày",
  velocity30d: "Tốc độ bán 30 ngày",
  estimatedWeeks: "Ước tính số tuần bán hết",
  kpiContext: "{area} · {range} · tồn kho mới nhất và tốc độ bán gần nhất",
  actionSignals: "Tín hiệu vận hành",
  actionSubtitle: "Tín hiệu định lượng hỗ trợ quyết định vận hành",
  velocity: "Tốc độ bán",
  estimatedSellOut: "Thời gian bán hết dự kiến",
  inventoryStatus: "Trạng thái tồn kho",
  insufficientData: "Chưa đủ dữ liệu",
  stable: "Ổn định",
  increasing: "Đang tăng",
  decreasing: "Đang giảm",
  highRemaining: "Tồn kho còn cao",
  soldOut: "Đã bán hết",
  watch: "Cần theo dõi",
  dataQuality: "Chất lượng dữ liệu",
  latestData: "Dữ liệu mới nhất",
  dataSource: "Nguồn dữ liệu",
  dataSourceDomain: "Nguồn dữ liệu: Căn hộ/Giao dịch",
  dateRangeValue: "Khoảng thời gian",
  errorRecords: "Số bản ghi lỗi",
  qualityOk: "Tốt",
  qualityWarnings: "Có cảnh báo",
  qualityError: "Có lỗi",
  openDashboard: "Xem bảng điều khiển",
  unavailableDashboard: "Bảng điều khiển",
  openAreaDashboard: "Mở bảng điều khiển phân khu",
  areaComparison: "So sánh phân khu",
  absorptionByArea: "Tỷ lệ hấp thụ theo phân khu",
  areaDetail: "Chi tiết phân khu",
  areaCount: "phân khu",
  status: "Trạng thái",
  latestDataColumn: "Dữ liệu mới nhất",
  noData: "Chưa có dữ liệu",
  error: "Có lỗi xảy ra",
  retry: "Thử lại",
  actionSignalPrefix: "Tín hiệu",
  suggestedFollowUpPrefix: "Gợi ý tiếp theo",
  noAutomaticDecision: "Chưa đủ dữ liệu để đưa ra khuyến nghị tự động.",
  inventoryHighSignal: "Tín hiệu: tồn kho còn cao. Gợi ý tiếp theo: rà soát tiến độ mở bán và mức độ bao phủ tồn kho.",
  velocityDownSignal: "Tín hiệu: tốc độ bán 7 ngày thấp hơn nền 30 ngày. Gợi ý tiếp theo: rà soát giá, ưu đãi, cơ cấu sản phẩm và hiệu quả kênh bán.",
  noActionSignal: "Chưa ghi nhận cảnh báo định lượng; không tự động đưa ra quyết định kinh doanh.",
  scopeRule: "Ngưỡng tham chiếu: tồn kho từ 50% là cần theo dõi, từ 75% là còn cao.",
  inventorySnapshot: "tồn kho mới nhất",
  latestVelocity: "tốc độ bán gần nhất",
  selectAreaForKpi: "Chọn phân khu để tải KPI quyết định",
  noDomainDataInPeriod: "Không có dữ liệu giao dịch trong khoảng thời gian đã chọn",
  noDomainDataForArea: "Phân khu này chưa có dữ liệu giao dịch",
  noUnitsInScope: "Phạm vi chưa có căn hộ",
});

export function formatDashboardDate(value, options = { day: "2-digit", month: "2-digit", year: "numeric" }) {
  if (!value) return "N/A";
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? "N/A"
    : date.toLocaleDateString("vi-VN", { ...options, timeZone: "UTC" });
}

export function formatDashboardDateTime(value) {
  if (!value) return "N/A";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "N/A"
    : date.toLocaleString("vi-VN", {
      hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit", year: "numeric",
    });
}

export function formatDashboardNumber(value, { digits, suffix = "" } = {}) {
  if (value === null || value === undefined) return "N/A";
  const number = Number(value);
  if (!Number.isFinite(number)) return "N/A";
  const formatted = digits === undefined
    ? number.toLocaleString("vi-VN")
    : number.toLocaleString("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${formatted}${suffix}`;
}

export function formatDashboardUnits(value, { digits, perWeek = false, perDay = false } = {}) {
  const suffix = perWeek ? " căn/tuần" : perDay ? " căn/ngày" : " căn";
  return formatDashboardNumber(value, { digits, suffix });
}
