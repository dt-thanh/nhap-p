// Nhãn phân khu: không lặp loại căn, nhưng cũng không bỏ mất nó khi cần.
import { describe, it, expect } from "vitest";
import { areaLabel } from "./areaLabel";

describe("areaLabel — bỏ hậu tố khi tên đã chứa loại căn", () => {
  it("dạng '<tên> - <loại>' của hệ nguồn chỉ in loại căn MỘT lần", () => {
    expect(areaLabel({ area_name: "Sapphire 1 - 2PN", unit_type: "2PN" })).toBe("Sapphire 1 - 2PN");
    expect(areaLabel({ area_name: "Anh Đào - Liền kề", unit_type: "Liền kề" })).toBe("Anh Đào - Liền kề");
  });

  it("loại căn nhiều từ cũng được nhận ra, không chỉ mã ngắn kiểu 2PN", () => {
    expect(areaLabel({ area_name: "The Harmony - Biệt thự song lập", unit_type: "Biệt thự song lập" })).toBe(
      "The Harmony - Biệt thự song lập",
    );
  });

  it("khác hoa/thường vẫn là trùng — 'Liền Kề' và 'Liền kề' là một", () => {
    expect(areaLabel({ area_name: "Ngọc Trai - Liền Kề", unit_type: "Liền kề" })).toBe("Ngọc Trai - Liền Kề");
    expect(areaLabel({ area_name: "Ngọc Trai - liền kề", unit_type: "LIỀN KỀ" })).toBe("Ngọc Trai - liền kề");
  });

  it("tiếng Việt tổ hợp sẵn (NFC) và tổ hợp rời (NFD) trông giống nhau thì phải khớp nhau", () => {
    const nfc = "Anh Đào - Liền kề".normalize("NFC");
    const nfd = "Liền kề".normalize("NFD");
    expect(nfc.includes(nfd)).toBe(false); // hai chuỗi KHÁC nhau ở mức byte
    expect(areaLabel({ area_name: nfc, unit_type: nfd })).toBe(nfc); // nhưng vẫn phải nhận ra là trùng
  });

  it("tên CHÍNH LÀ loại căn thì in đúng một lần", () => {
    expect(areaLabel({ area_name: "Liền kề", unit_type: "Liền kề" })).toBe("Liền kề");
  });

  it("dấu gạch dài, gạch ngắn, dấu chấm giữa — đều là ranh giới hợp lệ", () => {
    expect(areaLabel({ area_name: "Khu A — 3PN", unit_type: "3PN" })).toBe("Khu A — 3PN");
    expect(areaLabel({ area_name: "Khu A – 3PN", unit_type: "3PN" })).toBe("Khu A – 3PN");
    expect(areaLabel({ area_name: "Khu A · 3PN", unit_type: "3PN" })).toBe("Khu A · 3PN");
    expect(areaLabel({ area_name: "Khu A 3PN", unit_type: "3PN" })).toBe("Khu A 3PN");
  });
});

describe("areaLabel — GIỮ hậu tố khi tên chưa có loại căn", () => {
  it("tên không mang loại căn vẫn phải được phân biệt bằng hậu tố", () => {
    expect(areaLabel({ area_name: "Phân khu A", unit_type: "2PN" })).toBe("Phân khu A · 2PN");
    expect(areaLabel({ area_name: "Phân khu A", unit_type: "3PN" })).toBe("Phân khu A · 3PN");
  });

  it("hai phân khu cùng tên, khác loại KHÔNG được thu về cùng một nhãn", () => {
    const a = areaLabel({ area_name: "Phân khu A", unit_type: "2PN" });
    const b = areaLabel({ area_name: "Phân khu A", unit_type: "3PN" });
    expect(a).not.toBe(b);
  });

  it("loại căn chỉ TÌNH CỜ nằm cuối một từ dài hơn thì không phải trùng", () => {
    // "Tháp 12PN" kết thúc bằng chuỗi "2PN" nhưng không có ranh giới từ trước nó.
    expect(areaLabel({ area_name: "Tháp 12PN", unit_type: "2PN" })).toBe("Tháp 12PN · 2PN");
  });

  it("loại căn nằm GIỮA tên, không phải ở cuối, thì hậu tố vẫn cần", () => {
    expect(areaLabel({ area_name: "2PN Tower", unit_type: "2PN" })).toBe("2PN Tower · 2PN");
  });
});

describe("areaLabel — dữ liệu thiếu", () => {
  it("thiếu unit_type -> chỉ tên, không có dấu · lơ lửng", () => {
    expect(areaLabel({ area_name: "Phân khu A" })).toBe("Phân khu A");
    expect(areaLabel({ area_name: "Phân khu A", unit_type: null })).toBe("Phân khu A");
    expect(areaLabel({ area_name: "Phân khu A", unit_type: "   " })).toBe("Phân khu A");
  });

  it("thiếu area_name -> chỉ loại căn", () => {
    expect(areaLabel({ unit_type: "2PN" })).toBe("2PN");
  });

  it("không có gì -> chuỗi rỗng, KHÔNG phải 'undefined · undefined'", () => {
    expect(areaLabel({})).toBe("");
    expect(areaLabel(null)).toBe("");
    expect(areaLabel(undefined)).toBe("");
  });
});
