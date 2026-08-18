// frontend/src/utils/areaLabel.js
// Nhãn hiển thị của một phân khu, dùng CHUNG cho mọi màn hình.
//
// Vì sao cần hàm này: `areas.area_name` của hệ nguồn ĐÃ chứa sẵn loại căn ở
// cuối tên ("Anh Đào - Liền kề", "Sapphire 1 - 2PN"), nên mẫu `${area_name} ·
// ${unit_type}` mà các trang đang dùng luôn in loại căn HAI LẦN:
//
//     "Anh Đào - Liền kề · Liền kề"
//     "Sapphire 1 - 2PN · 2PN"
//
// Đúng 58/58 phân khu trong dữ liệu hiện tại rơi vào trường hợp này.
//
// KHÔNG sửa bằng cách bỏ hẳn `unit_type` khỏi nhãn: quy ước đặt tên là của HỆ
// NGUỒN, không phải bất biến của hệ này. Một phân khu tên "Phân khu A" với
// `unit_type = "2PN"` (xem `src/api/mock.js`) vẫn CẦN hậu tố để phân biệt với
// "Phân khu A · 3PN" — bỏ nó đi sẽ tạo ra hai dòng trùng tên hệt nhau trong
// cùng một dropdown, đúng kiểu lỗi tệ hơn cái đang sửa.
//
// Nên: chỉ BỎ QUA hậu tố khi tên ĐÃ kết thúc bằng chính loại căn đó.
//
// Cùng khuôn với utils/freshness.js và utils/velocity.js: một hàm THUẦN, một
// nguồn sự thật cho mọi nơi hiển thị, để nhãn không lệch nhau giữa các màn hình.

// So khớp KHÔNG phân biệt hoa/thường, và chuẩn hoá Unicode về NFC trước.
// Tiếng Việt có dấu tồn tại ở hai dạng mã hoá khác nhau ("ề" là MỘT ký tự tổ
// hợp sẵn, hoặc "e" + hai dấu rời). Hai dạng đó trông y hệt nhau trên màn hình
// nhưng `===` coi là khác nhau — không chuẩn hoá thì "Liền kề" nhập từ nguồn
// này sẽ không khớp "Liền kề" nhập từ nguồn kia, và bản sửa này im lặng vô tác
// dụng đúng trên những cái tên nó sinh ra để sửa.
const normalize = (value) => String(value ?? "").normalize("NFC").toLowerCase().trim();

// Ký tự được coi là RANH GIỚI TỪ đứng ngay trước loại căn. Có nó thì "2PN" ở
// cuối "Sapphire 1 - 2PN" là một thành phần riêng; không có nó thì "2PN" chỉ
// đang nằm lọt trong một từ dài hơn ("Tháp 12PN") và PHẢI giữ lại hậu tố.
const BOUNDARY_BEFORE_TYPE = /[\s\-–—·:|,]$/;

/**
 * Tên hiển thị của một phân khu, không lặp loại căn.
 *
 * @param {{area_name?: string, unit_type?: string}|null|undefined} area
 * @returns {string} chuỗi rỗng nếu không có gì để hiện (nơi gọi tự quyết định
 *   fallback — hàm này KHÔNG bịa ra "—" hay "Không rõ").
 */
export function areaLabel(area) {
  const name = String(area?.area_name ?? "").trim();
  const unitType = String(area?.unit_type ?? "").trim();

  if (!name) return unitType;
  if (!unitType) return name;

  const normalizedName = normalize(name);
  const normalizedType = normalize(unitType);

  // Tên CHÍNH LÀ loại căn — "Liền kề · Liền kề" là dạng lặp lộ liễu nhất.
  if (normalizedName === normalizedType) return name;

  if (normalizedName.endsWith(normalizedType)) {
    const before = normalizedName.slice(0, normalizedName.length - normalizedType.length);
    if (BOUNDARY_BEFORE_TYPE.test(before)) return name;
  }

  return `${name} · ${unitType}`;
}
