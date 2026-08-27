# Benchmark công thức xếp hạng — V2 (AHP) đối chiếu V1

> Sinh tự động bởi `eval/ahp_benchmark.py`. **Không sửa tay.**

| | |
|---|---|
| **Đầu vào** | `datasets/synthetic_v1/exports/units_ranking.csv` |
| **Phạm vi** | 200 căn / 3 dự án / 4 đặc trưng |
| **Xếp hạng theo** | TỪNG dự án (syn1-P-001: 106, syn1-P-002: 66, syn1-P-003: 28) |
| **Bộ chấm điểm** | `src/ranking/engine.py` (dùng thẳng, không viết lại) |
| **Ngưỡng mức** | `src/ranking/bands.py` |
| **CR của bộ phán đoán AHP** | 0.0038 (ngưỡng 0.08, n=4) |
| **Cách tái lập** | `python -m eval.ahp_benchmark` |

---

## 1. Bốn bộ trọng số

| Bộ | unit_available | unit_demand_norm | area_velocity_norm | area_conversion_norm |
|---|---|---|---|---|
| V1 (đặt tay) | 0.35 | 0.25 | 0.20 | 0.20 |
| V2-AHP | 0.4551 | 0.2627 | 0.1411 | 0.1411 |
| Đều nhau | 0.2500 | 0.2500 | 0.2500 | 0.2500 |
| Entropy | 0.1359 | 0.7537 | 0.0948 | 0.0156 |

`direction` và `missing_value_policy` GIỮ NGUYÊN theo 0022 ở cả bốn bộ — chỉ
độ lớn thay đổi, nếu không ta đang so hai mô hình khác nhau chứ không phải
so cách chọn trọng số.

---

## 2. Khối A — Đồng thuận: AHP có thật sự đổi gì không?

Mọi chỉ số thứ hạng tính TRONG từng dự án rồi lấy trung bình có trọng số theo số căn.

| So với V1 | Spearman ρ | Kendall τ | Chồng lấn top-10 | Đổi mức |
|---|---|---|---|---|
| V2-AHP | 0.9948 | 0.9830 | 0.88 | 3 căn |
| Đều nhau | 0.9644 | 0.9239 | 0.87 | 23 căn |
| Entropy | 0.9857 | 0.9682 | 0.88 | 114 căn |

ρ và τ đo THỨ TỰ; cột "Đổi mức" đo NHÃN. Hai thứ tách nhau: `Entropy` giữ
thứ tự gần như nguyên vẹn mà vẫn đẩy hơn một trăm căn sang mức khác, vì dồn
trọng số làm điểm co lại và tụt qua ngưỡng. Đội bán hàng nhìn NHÃN.

| Bộ | high | medium | low | Căn đứng đầu mỗi dự án |
|---|---|---|---|---|
| V1 (đặt tay) | 19 | 121 | 60 | `HC1-018`, `WPA-012`, `QFN-009` |
| V2-AHP | 22 | 118 | 60 | `HC1-018`, `WPA-012`, `QFN-009` |
| Đều nhau | 14 | 108 | 78 | `HC1-018`, `WPA-012`, `QFN-009` |
| Entropy | 14 | 17 | 169 | `HC1-018`, `WPA-012`, `QFN-009` |

---

## 3. Khối B — Ổn định: thứ hạng chịu được nhiễu trọng số tới đâu

Nhiễu từng trọng số ±x%, chuẩn hoá lại, xếp hạng lại. Không cần nhãn thực tế.

**Đọc bảng này cẩn thận: ổn định KHÔNG tự nó là điều tốt.** Hàng `Entropy` đứng
yên ở mọi mức nhiễu, nhưng đó là ổn định SUY BIẾN — entropy dồn 0.75 trọng số
vào một tiêu chí, nên bảng xếp hạng thực chất chỉ sắp theo `unit_demand_norm`
và không phép nhiễu nhỏ nào đảo được nó. Một mô hình một-tiêu-chí luôn ổn định
hoàn hảo. Con số cần tìm là ổn định ở một bộ trọng số VẪN CÒN dùng cả bốn tiêu chí.

| Bộ | ±10% đổi hạng | ±10% đổi mức | ±20% đổi hạng | ±20% đổi mức | Nhiễu nhỏ nhất đổi Top-1 |
|---|---|---|---|---|---|
| V1 (đặt tay) | 3.0% | 0.0% | 5.6% | 0.2% | > ±100% |
| V2-AHP | 2.2% | 1.6% | 5.9% | 4.6% | > ±100% |
| Đều nhau | 2.4% | 5.4% | 10.5% | 6.1% | > ±100% |
| Entropy | 0.0% | 0.0% | 0.0% | 0.0% | > ±100% |

Cột cuối nhiễu MỖI LẦN MỘT trọng số và trả về ngưỡng mỏng manh nhất trong ba
dự án. Nó đo độ bền của KHUYẾN NGHỊ SỐ 1, không phân biệt được các bộ trọng số
với nhau khi cả bốn cùng xếp một căn lên đầu.

---

## 4. Khối C — Tất định

| Kiểm | Kết quả |
|---|---|
| Suy trọng số hai lần ra cùng một bộ | ✅ |
| Xếp hạng hai lần ra cùng thứ tự | ✅ |

Cùng bất biến mà TC-02 của `eval/results/report.md` canh, đo ở tầng công thức.

---

## 5. Hạn chế — đọc trước khi trích số

**Dữ liệu là TỔNG HỢP.** Sinh bởi `scripts/generate_synthetic_dataset.py`.
Mọi con số trên đo *hành vi của công thức*, không đo độ chính xác trên thị trường thật.

**Cố ý KHÔNG có khối dự báo** ("căn xếp cao có bán nhanh hơn không?"), vì hai lý do:

1. **Rò rỉ nhãn.** `unit_available` mang trọng số lớn nhất và được suy TRỰC TIẾP
   từ `status` — chính kết quả cần dự báo. Căn đã bán có `unit_available = 0` nên
   bị đẩy xuống đáy *do cấu tạo*. Một backtest ngây thơ sẽ kết luận mô hình
   phản-dự-báo, và kết luận đó vô nghĩa.
2. **Cỡ mẫu.** Bộ dữ liệu chỉ có 42 lần bán. Precision@k trên 42 sự kiện có
   phương sai quá lớn để nói được điều gì.

Muốn làm khối dự báo khi có dữ liệu thật: cắt theo THỜI GIAN (đặc trưng tính
tới mốc *T*, đánh giá trên giao dịch trong *(T, T+90 ngày]*), và giới hạn tập
đánh giá vào những căn CÒN BÁN ĐƯỢC tại *T* — điều đó vô hiệu hoá rò rỉ ở trên
vì mọi căn trong tập đều có `unit_available = 1`.

**Khối B là khẳng định mạnh nhất ở đây**, vì nó không cần nhãn thực tế nào.
