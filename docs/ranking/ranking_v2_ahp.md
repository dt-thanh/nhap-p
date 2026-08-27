# Xếp hạng V2 — trọng số suy ra bằng AHP, và benchmark đi kèm


1. Xếp hạng AHP là gì?
3. Benchmark đo cái gì?

---
    V1  trọng số do kỹ sư tự điêù chỉnh    

    V2  trọng số SUY RA từ phán đoán chuyên gia, kèm bằng chứng nhất quán

---


**Khác biệt duy nhất giữa V1 và V2 là trọng số ở đâu ra.**

    V1:  kỹ sư gõ 0.35 / 0.25 / 0.20 / 0.20 vào migration
    V2:  chuyên gia so sánh từng cặp tiêu chí → AHP suy ra trọng số + đo độ nhất quán

Phép tổng có trọng số ở `engine.py` **chính là** bước tổng hợp của AHP. Thứ lâu
nay còn thiếu chỉ là căn cứ cho các trọng số — và đó là toàn bộ phạm vi của V2.

---

## 2. AHP làm gì

### 2.1 Hỏi từng cặp, không hỏi thẳng trọng số

Hỏi "`unit_available` đáng bao nhiêu phần trăm?" là câu hỏi không ai trả lời
được cho tử tế. Hỏi "giữa `unit_available` và `unit_demand_norm`, cái nào quan
trọng hơn, hơn bao nhiêu?" thì trả lời được.

Với 4 tiêu chí, hệ thống hỏi đúng **n(n−1)/2 = 6** câu — chỉ **tam giác trên**
của ma trận. Nửa dưới là nghịch đảo, đường chéo là 1. Không hỏi cả n² ô nghĩa là
người dùng **không thể tự mâu thuẫn kiểu "A hơn B" đồng thời "B hơn A"**.

Thang Saaty: `1` = ngang nhau … `9` = vượt trội tuyệt đối, kèm nghịch đảo.

### 2.2 Suy trọng số — RGMM

    w_i = (∏_j a_ij)^(1/n)   rồi chuẩn hoá để Σw = 1

Saaty gốc dùng véc-tơ riêng chính. Ở đây **cố ý** dùng trung bình nhân theo hàng
(RGMM), ba lý do theo thứ tự quan trọng:

1. **Biểu thức đóng** — không giải trị riêng lặp, không LAPACK, nên kết quả
   giống hệt nhau trên mọi máy. `ranking_configs` là hồ sơ kiểm toán; sáu tháng
   sau phải suy lại được đúng con số cũ.
2. Không cần thêm phụ thuộc (`requirements.txt` không có numpy/scipy).
3. Hợp với kỷ luật `Decimal` mà `engine.py` đã theo.

> **RGMM không phải xấp xỉ của véc-tơ riêng.** Hai bên chỉ trùng tuyệt đối khi
> ma trận nhất quán hoàn hảo (đo được: lệch `1.1e-16`). Ngay ở bộ phán đoán của
> dự án (CR = 0.0038) chúng đã lệch `~4e-04` — tức **lớn hơn** chữ số thập phân
> thứ tư mà ta lưu. Ai đối chiếu với một bảng tính chạy phương pháp véc-tơ riêng
> sẽ thấy lệch ở chữ số thứ tư, và đó là hành vi **đúng**. Quan hệ này được ghim
> ở `tests/test_ranking/test_ahp.py`.

### 2.3 Đo độ nhất quán — CR

    λmax = (1/n) · Σ (A·w)_i / w_i
    CI   = (λmax − n) / (n − 1)
    CR   = CI / RI(n)

Ngưỡng **không** phải 0.10 phẳng — nó phụ thuộc n: `n=3 → 0.05`, `n=4 → 0.08`,
`n≥5 → 0.10`. Với `n ≤ 2` thì `RI = 0`, mọi ma trận 2×2 nghịch đảo đều nhất quán
tuyệt đối, nên CR ≡ 0 và phải chặn **trước** phép chia.

### 2.4 Cổng CR ba mức

| CR | Xử lý |
|---|---|
| ≤ ngưỡng theo n | Qua |
| ngưỡng … 0.20 | Cần `override=true` **kèm lý do**, lý do đi thẳng vào `note` |
| > 0.20 | Từ chối hẳn, **không có đường vòng** |

Mức giữa tồn tại vì một giám đốc kinh doanh thật, dưới áp lực thời gian, sẽ có
lúc hơi thiếu nhất quán — và một lần vượt ngưỡng **được ghi lại** thì tốt hơn
nhiều so với việc họ sửa liều vài con số cho qua cổng.

Khi từ chối, API trả kèm **hotspot**: phán đoán lệch xa nhất khỏi trọng số suy
ra, đo bằng `|ln a_ij − ln(w_i/w_j)|`. "CR = 0.31, mời nhập lại" là lời từ chối
không dùng được; *"bạn chấm A hơn B gấp 2, nhưng các câu trả lời còn lại của bạn
hàm ý 1.73"* thì sửa được.

---

## 3. Dùng nó thế nào

Đúng **một** endpoint, và nó **không ghi gì cả**:

    POST /api/v1/ranking/ahp/weights        (vai trò: admin)

Nó cố ý dừng ở "trả về trọng số" thay vì tự tạo bản nháp config. `ranking_configs`
đã có sẵn đường soạn–phát hành–quay lui với đúng một nơi ghi
(`src/services/ranking_config.py`, xem `tests/test_ranking_boundary.py`). Mở
thêm đường ghi thứ hai chỉ để đỡ cho người dùng một lần gọi API là đánh đổi một
bất biến thật lấy một tiện nghi nhỏ.

Luồng đầy đủ — **ba hành động NGƯỜI tách bạch**:

```
1. POST /ranking/ahp/weights           chuyên gia nhập so sánh cặp → nhận trọng số + CR
2. POST /ranking/configs               người duyệt tạo bản nháp        (đã có sẵn)
3. POST /ranking/configs/{v}/publish   phát hành + xếp hàng tính lại   (đã có sẵn)
```

Bước 1 **không chạm database**. `AGENTS.md` coi bước duyệt của người là bắt
buộc, và cách rẻ nhất để không bao giờ lỡ tay bỏ qua nó là bước tính toán
**không có khả năng ghi** ngay từ đầu.

Trọng số trả về đã đi qua chính `validate_weights` mà bước 2 sẽ gọi — nên
endpoint trả 200 thì kết quả **chắc chắn** post được sang `/ranking/configs`.

### `note` là vết kiểm toán, và nó phải NẠP LẠI được

`note` là nơi **duy nhất** còn giữ các phán đoán gốc. Không có nó, AHP chỉ thay
bốn con số không giải thích được bằng bốn con số khác cũng không giải thích được.

    Ranking V2-AHP — trọng số SUY RA bằng AHP (Saaty).
    CR=0.0038 (ngưỡng 0.08, n=4).
    So sánh cặp: unit_available>unit_demand_norm=2; unit_available>area_velocity_norm=3; …

Ghi lại đúng `Decimal` đã đem đi tính, **không** rút gọn bằng `:g` — định dạng
đó giữ 6 chữ số nên nghịch đảo `1/9` vào note thành `0.111111`, và nạp lại chuỗi
đó thì `validate` từ chối vì ngoài thang. Một vết kiểm toán không tái lập được
thì không phải vết kiểm toán.

---

## 4. Hai chi tiết dễ vấp

**Dồn phần dư khi làm tròn.** Bộ phán đoán tham chiếu làm tròn ngây thơ ra
`0.4550 + 0.2627 + 0.1411 + 0.1411 = 0.9999`, còn `validate_weights` chỉ dung
sai `1e-9` quanh 1.0 — sẽ trượt ở bước cuối với những phán đoán hoàn toàn hợp
lệ. Phần dư được dồn vào trọng số **lớn nhất** (sai lệch tương đối nhỏ nhất);
hoà thì lấy khoá **nhỏ nhất theo tên**, cố ý không lấy khoá gõ trước — phá hoà
theo vị trí làm bộ trọng số phát hành phụ thuộc thứ tự người dùng gõ.

**`src/ranking/ahp.py` là hàm thuần.** Không I/O, không mạng, không DB — cùng
ràng buộc với `engine.py`, và `tests/test_ranking_boundary.py` cưỡng chế điều đó
cho cả hai module. Nó rất muốn đọc thẳng config đang phát hành từ DB, và đúng
cái tiện đó sẽ biến một hàm kiểm được bằng số học thuần thành thứ phải dựng cả
database mới chạy được.

---

## 5. Benchmark

    python -m eval.ahp_benchmark        →  eval/results/ahp_benchmark.md

Không DB, không mạng, không LLM. Chạy ~8 giây.

### 5.1 Vì sao nó đáng tin

**Nó chấm điểm bằng chính `src/ranking/engine.py`**, không viết lại công thức, và
cắt mức bằng chính `src/ranking/bands.py`. Viết lại phép tổng có trọng số sẽ tạo
bản sao thứ hai của cùng một quy tắc, và benchmark sẽ đo **bản sao** chứ không
đo hệ thống thật.

Bảo đảm mạnh nhất: chấm lại 200 căn bằng trọng số V1 phải ra **đúng** điểm/hạng/
mức mà pipeline thật đã xuất — hiện khớp **200/200 cả ba**. Test đó
(`tests/test_ranking/test_ahp_benchmark.py`) đã bắt được một lỗi thật: bản đầu
gộp cả 200 căn của **ba** dự án vào một rổ, trong khi `run_ranking` chạy theo
**từng** dự án. Điểm khớp 200/200 nhưng thứ hạng lệch 195/200.

### 5.2 Bốn bộ trọng số được so

| Bộ | Vai trò |
|---|---|
| **V1** (0.35/0.25/0.20/0.20) | mốc **thật**, không phải mốc rơm |
| **V2-AHP** (0.4551/0.2627/0.1411/0.1411) | thứ đang được đánh giá |
| **Đều nhau** (0.25 ×4) | mốc "không có mô hình" |
| **Entropy** (0.1359/0.7537/0.0948/0.0156) | mốc khách quan, **không** có ý kiến chuyên gia |

`direction` và `missing_value_policy` giữ nguyên ở cả bốn — chỉ độ lớn thay đổi,
nếu không ta đang so hai mô hình khác nhau chứ không phải so cách chọn trọng số.

### 5.3 Ba khối

| Khối | Đo gì |
|---|---|
| **A — Đồng thuận** | AHP có thật sự đổi gì không (ρ, τ, chồng lấn top-10, đổi mức) |
| **B — Ổn định** | nhiễu trọng số ±10/20% thì thứ hạng đổi bao nhiêu |
| **C — Tất định** | chạy hai lần ra kết quả giống hệt |

Mọi chỉ số thứ hạng tính **trong từng dự án** rồi lấy trung bình có trọng số
theo số căn. Không được gộp thứ hạng ba dự án vào một véc-tơ: hạng 1 của dự án
28 căn và hạng 1 của dự án 106 căn là hai đại lượng khác nhau.

### 5.4 Kết quả hiện tại — và cách đọc

| So với V1 | Spearman ρ | Kendall τ | Chồng lấn top-10 | Đổi mức |
|---|---|---|---|---|
| V2-AHP | 0.9948 | 0.9830 | 0.88 | **3** căn |
| Đều nhau | 0.9644 | 0.9239 | 0.87 | 23 căn |
| Entropy | 0.9857 | 0.9682 | 0.88 | 114 căn |

**Phát hiện chính: AHP phần lớn XÁC NHẬN bộ trọng số đặt tay, không lật đổ nó.**
Chỉ 3 trên 200 căn đổi mức. Đây **không** phải thất bại — nó nghĩa là trực giác
của đội hồi V1 vốn đã tốt, và bây giờ nó có căn cứ thành văn thay vì chỉ là trực
giác. Một AHP làm đảo lộn bảng xếp hạng mới là thứ đáng ngờ.

| Bộ | ±10% đổi hạng | ±20% đổi hạng | Nhiễu nhỏ nhất đổi Top-1 |
|---|---|---|---|
| V1 | 3.0% | 5.6% | > ±100% |
| V2-AHP | 2.2% | 5.9% | > ±100% |
| Entropy | 0.0% | 0.0% | > ±100% |

**Khuyến nghị số 1 của cả ba dự án bền tuyệt đối**: cả bốn bộ trọng số cùng chọn
một căn, và không phép nhiễu một-trọng-số nào tới ±100% đổi được nó.

> **Ổn định KHÔNG tự nó là điều tốt.** Hàng `Entropy` đứng yên 0.0% ở mọi mức
> nhiễu — nhưng đó là ổn định **suy biến**: entropy dồn 0.75 trọng số vào một
> tiêu chí, nên bảng xếp hạng thực chất chỉ sắp theo `unit_demand_norm`. Một mô
> hình một-tiêu-chí luôn ổn định hoàn hảo. Con số cần tìm là ổn định ở một bộ
> trọng số **vẫn còn dùng cả bốn tiêu chí**.

Và: ρ đo **thứ tự**, "đổi mức" đo **nhãn**. `Entropy` giữ thứ tự gần nguyên vẹn
(ρ = 0.99) mà vẫn đẩy 114 căn sang mức khác. **Đội bán hàng nhìn nhãn.**

---

## 6. Hạn chế — đọc trước khi trích số

**Dữ liệu là TỔNG HỢP**, sinh bởi `scripts/generate_synthetic_dataset.py`. Mọi
con số trên đo *hành vi của công thức*, không đo độ chính xác trên thị trường thật.

**Cố ý KHÔNG có khối dự báo** ("căn xếp cao có bán nhanh hơn không?"), hai lý do:

1. **Rò rỉ nhãn.** `unit_available` mang trọng số lớn nhất (0.4551) và được suy
   **trực tiếp** từ `status` — chính kết quả cần dự báo. Căn đã bán có
   `unit_available = 0` nên bị đẩy xuống đáy *do cấu tạo*. Một backtest ngây thơ
   sẽ kết luận mô hình phản-dự-báo, và kết luận đó vô nghĩa.
2. **Cỡ mẫu.** Bộ dữ liệu chỉ có **42** lần bán. Precision@k trên 42 sự kiện có
   phương sai quá lớn để nói được điều gì.

Cách làm khối dự báo **khi có dữ liệu thật**: cắt theo **thời gian** (đặc trưng
tính tới mốc *T*, đánh giá trên giao dịch trong *(T, T+90 ngày]*), và giới hạn
tập đánh giá vào những căn **còn bán được tại *T*** — điều đó vô hiệu hoá rò rỉ
trên, vì mọi căn trong tập đều có `unit_available = 1`.

** Benchmark đúng về mặt phương pháp cho một
công cụ *trích xuất sở thích* là Kendall τ giữa xếp hạng AHP và xếp hạng do giám
đốc kinh doanh chấm tay (~30 căn là đủ).

---

## 7. Bản đồ file

| File | Vai trò |
|---|---|
| `src/ranking/ahp.py` | Toán AHP. **Hàm thuần**, không I/O |
| `src/api/ahp.py` | Một endpoint, **không ghi gì** |
| `eval/ahp_benchmark.py` | Bộ chạy benchmark |
| `eval/results/ahp_benchmark.md` | Báo cáo **sinh tự động** — không sửa tay |
| `tests/test_ranking/test_ahp.py` | 36 test toán + endpoint |
| `tests/test_ranking/test_ahp_benchmark.py` | 10 test, gồm test trung thực 200/200 |
| `src/ranking/engine.py` | **Không đổi.** V1 và V2 dùng chung |
