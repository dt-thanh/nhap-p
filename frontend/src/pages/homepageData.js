export const NAV_ITEMS = [
  { label: "Tổng quan", href: "#overview" },
  { label: "Nền tảng", href: "#platform" },
  { label: "Phân tích", href: "#insights" },
  { label: "Phương pháp", href: "#methodology" },
  { label: "Tài nguyên", href: "#resources" },
];

export const TRUST_ITEMS = [
  "Theo dõi theo cấp dự án",
  "Chỉ báo hấp thụ có nguồn dữ liệu",
  "Đề xuất có bước phê duyệt",
];

export const DEMO_PROJECTS = [
  { label: "Riverstone Residences", value: "riverstone" },
  { label: "Northlight Gardens", value: "northlight" },
  { label: "Harbor Point", value: "harbor" },
];

export const PERIODS = [
  { label: "7 ngày gần đây", value: "7d" },
  { label: "30 ngày gần đây", value: "30d" },
  { label: "Toàn bộ dữ liệu", value: "all" },
];

export const PLATFORM_FEATURES = [
  { icon: "rate", number: "01", title: "Theo dõi hấp thụ theo dự án", description: "Xem tồn kho, căn đã bán và vận tốc hấp thụ trong một góc nhìn theo phạm vi dự án." },
  { icon: "calendar", number: "02", title: "Đọc xu hướng lịch sử", description: "Theo dõi số căn bán theo ngày và vận tốc 7/30 ngày khi dữ liệu đủ; không phải dự báo tương lai." },
  { icon: "filter", number: "03", title: "Xem dự đoán và phân tích", description: "Đặt quy mô, tồn kho và chỉ báo hấp thụ cạnh nhau để kiểm tra dữ liệu trước khi trao đổi bước tiếp theo." },
  { icon: "bot", number: "04", title: "Đề xuất có bước phê duyệt", description: "Luồng tư vấn có thể tạo đề xuất từ dữ liệu đã xếp hạng; đề xuất chờ con người duyệt và không tự thực thi." },
];

export const INSIGHT_SERIES = [
  { month: "T7", actual: 18, forecast: 20 }, { month: "T8", actual: 24, forecast: 25 },
  { month: "T9", actual: 29, forecast: 30 }, { month: "T10", actual: 35, forecast: 34 },
  { month: "T11", actual: 41, forecast: 40 }, { month: "T12", actual: 46, forecast: 47 },
  { month: "T1", actual: 50, forecast: 54 }, { month: "T2", actual: 56, forecast: 60 },
  { month: "T3", actual: 61, forecast: 65 }, { month: "T4", actual: null, forecast: 70 },
  { month: "T5", actual: null, forecast: 76 }, { month: "T6", actual: null, forecast: 82 },
];

export const REGION_BARS = [
  { label: "Nhóm A", value: 82 },
  { label: "Nhóm B", value: 68 },
  { label: "Nhóm C", value: 54 },
  { label: "Nhóm D", value: 41 },
];

export const WORKFLOW = [
  { number: "01", title: "Nạp dữ liệu", description: "Tiếp nhận dữ liệu dự án và các bản ghi nghiệp vụ theo hợp đồng hiện có." },
  { number: "02", title: "Kiểm tra", description: "Xem tồn kho, giao dịch và chỉ báo hấp thụ; dữ liệu thiếu được giữ rõ thay vì suy diễn." },
  { number: "03", title: "Xem xét và phê duyệt", description: "Dùng phân tích để trao đổi bước tiếp theo; mọi đề xuất thay đổi phải qua con người phê duyệt." },
];

export const METRICS = [
  { value: "Dự án", label: "phạm vi theo dõi" },
  { value: "Phân khu", label: "góc nhìn chi tiết" },
  { value: "7 / 30 ngày", label: "cửa sổ vận tốc hấp thụ" },
  { value: "Chờ duyệt", label: "trạng thái đề xuất mặc định" },
];
