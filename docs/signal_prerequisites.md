# Tín hiệu bị CHẶN và điều kiện tiên quyết về lược đồ

Trạng thái: **TÀI LIỆU QUYẾT ĐỊNH — chưa cài đặt gì.**
Cập nhật: 2026-08-19.
Phạm vi: các tín hiệu mà khung ưu tiên nghiệp vụ yêu cầu nhưng repo **chưa có dữ
liệu để suy ra một cách trung thực**.

Tài liệu này KHÔNG đề xuất cột giả, migration giả, ngưỡng tuỳ tiện hay tín hiệu
mô phỏng trên giao diện. Mỗi mục nêu: tín hiệu bị chặn, bằng chứng cho việc nó bị
chặn, và ĐÚNG những gì phải có trước khi được phép cài đặt.

Quy ước đọc: "đã kiểm chứng" nghĩa là đã tra trong cây mã ở lần cập nhật này.

---

## 0. Vì sao có tài liệu này

`frontend/src/utils/signals.js` chỉ phát tín hiệu ở hai tầng:

| Tầng | Ý nghĩa | Tình trạng |
|---|---|---|
| 1 | Tin cậy dữ liệu | **ĐÃ CÓ** — đồng bộ, độ mới, tính lại, phủ xếp hạng, dữ liệu tự mâu thuẫn |
| 2 | Rủi ro thương mại / bán hết | **CÓ MỘT PHẦN** — vận tốc gộp, tầm nhìn bán hết, tồn kho mức xếp hạng thấp |
| 3 | Rủi ro nhu cầu dẫn dắt | **KHÔNG CÓ** — toàn bộ tầng này bị chặn ở mức LƯỢC ĐỒ |

Tầng 3 không phải việc còn tồn trong backlog kỹ thuật. Bốn trong sáu tín hiệu của
tầng đó cần những BẢNG và CỘT hiện không tồn tại. Vì vậy mã nguồn cố ý **không**
khai báo `layer: 3` ở bất kỳ đâu — khai báo sẽ là tuyên bố một năng lực không có.

Mô hình miền hiện có đúng 23 bảng (`src/models/tables.py`):

```
projects  upload_files  upload_errors  crm_source_records  areas  sales_records
inventory_snapshots  absorption_daily  units  deals  sync_credentials
sync_payloads  reconciliation_runs  reconciliation_findings
calculator_comparisons  feature_snapshots  ranking_configs  ranking_runs
ranking_scores  agent_recommendations  sales_campaigns  sales_campaign_units
agent_executions
```

Không có bảng nào cho: mục tiêu bán hàng, lịch sử chuyển trạng thái, khách hàng /
lead / nguồn khách, giá, chiết khấu, doanh thu.

---

## 1. Hấp thụ RÒNG và tỷ lệ huỷ — CHẶN vì NGỮ NGHĨA (không phải vì thiếu cột)

Đây là mục quan trọng nhất, vì nhìn qua thì tưởng đã đủ dữ liệu.

### Cái ĐÃ có (đã kiểm chứng)
* `deals.status` có `lost`; `deals.source_status` giữ nguyên chữ gốc `cancelled`
  (`src/services/domain_projection.py` — `DEAL_STATUS_ALIASES`).
* `deals.lost_at`, `deals.sold_at`, `deals.reserved_at` đều tồn tại
  (`src/models/tables.py`).
* Ràng buộc DB cho phép một dòng vừa `status='lost'` vừa có `sold_at`
  (`alembic/versions/0007_s3_domain_model.py` — chỉ ép `status <> 'sold' OR
  sold_at IS NOT NULL`, không ép chiều ngược lại).
* `history_guard` BẢO VỆ `sold_at` khỏi bị ghi NULL khi cập nhật
  (`src/services/history_guard.py` — `HISTORY_FIELDS["deals"]`).

Nghĩa là **đường ống hoàn toàn CÓ THỂ biểu diễn "một giao dịch đã bán rồi bị
huỷ"**: đó là dòng `status='lost'` **và** `sold_at IS NOT NULL`.

### Cái KHÔNG có — và đây chính là chỗ chặn
Không một nguồn dữ liệu nào trong repo từng tạo ra dòng như thế:

| Nguồn | Giao dịch `lost` | Trong đó có `sold_at` |
|---|---|---|
| `datasets/synthetic_v1/deals.csv` | 24 (đều `source_status='cancelled'`) | **0** — `sold_at` là `\N` ở cả 24 dòng |
| `scripts/generate_synthetic_dataset.py` | tạo qua `add(unit, "lost", "cancelled", lost_at=…)` | **0** — không truyền `sold_at` |
| `alembic/versions/0021_seed_ai_crm_fixture_deals.py` | tạo với `reserved_at` + `lost_at` | **0** — không truyền `sold_at` |

Toàn bộ giao dịch `cancelled` hiện có là **booking/lead bị huỷ**, chưa từng là
**đơn bán bị huỷ**.

### Vì sao KHÔNG được cài đặt bằng dữ liệu hiện tại
Công thức `net = sold trong kỳ − lost trong kỳ` sẽ trừ một tập **chưa từng bán**
khỏi một tập **đã bán**. Hai tập khác nhau về bản chất. Với dữ liệu tổng hợp
hiện tại, phép trừ đó cho ra một "tỷ lệ huỷ" ≈ 24/131 ≈ **18%** trong khi số đơn
bán thật sự bị huỷ là **0**. Một con số sai mà trông có thẩm quyền còn tệ hơn ô
trống — nên tầng tín hiệu tiếp tục chỉ nói **GỘP**, và nói rõ là gộp.

### Điều kiện tiên quyết trước khi cài đặt
1. **Quyết định nghiệp vụ**: "huỷ" được tính là huỷ ĐƠN BÁN (đã `sold`) hay gồm
   cả huỷ GIỮ CHỖ (`reserved`)? Hai định nghĩa cho hai con số khác hẳn nhau.
2. **Xác nhận nguồn**: Mini CRM có thật sự gửi `sold_at` kèm khi một giao dịch đã
   bán chuyển sang huỷ hay không. Nếu không, sửa ở hệ NGUỒN, không vá ở đích.
3. **Dữ liệu kiểm chứng**: fixture/synthetic phải sinh được ít nhất một dòng
   `status='lost' AND sold_at IS NOT NULL`, nếu không thì không có gì để test.
4. **Ghi rõ giới hạn thời điểm**: `lost_at` là lúc ghi nhận huỷ, không phải lúc
   đơn bán gốc phát sinh. Hấp thụ ròng theo kỳ vì thế luôn lệch khi đơn bán và
   lệnh huỷ rơi vào hai kỳ khác nhau — phải nêu rõ, không được giấu.
5. **Không đổi tên trường cũ**: mọi trường vận tốc hiện tại là GỘP và phải giữ
   nguyên tên; số ròng phải là trường MỚI, tách bạch.

---

## 2. Hấp thụ so với KẾ HOẠCH bán hàng — CHẶN vì thiếu mô hình mục tiêu

**Tín hiệu bị chặn**: "hấp thụ dưới kế hoạch", "chậm tiến độ so với mục tiêu kỳ".

**Bằng chứng chặn**: không có cột mục tiêu/kế hoạch/hạn mức nào trong 23 bảng.
`areas.total_units` là **quỹ hàng theo kế hoạch** (`src/ranking/service.py` ghi rõ
điều này), tức là mẫu số tồn kho — KHÔNG phải mục tiêu doanh số.

**Điều kiện tiên quyết**
* Một mô hình mục tiêu có phạm vi rõ: mục tiêu gắn vào dự án, phân khu, hay đợt
  mở bán (phase)?
* **Ngày hiệu lực** cho từng mục tiêu, và quy tắc khi mục tiêu được sửa giữa kỳ:
  chỉnh sửa tại chỗ hay phiên bản mới? Không có lịch sử hiệu lực thì mọi so sánh
  hồi cố đều vô nghĩa.
* Đơn vị và độ hạt: căn/tháng? doanh thu/quý? Phải khớp với đơn vị vận tốc đang
  có, mà bản thân đơn vị đó còn chưa nhất quán (xem §6).
* Chủ sở hữu quyết định: ai được đặt và duyệt mục tiêu, và mục tiêu có đi qua
  bước duyệt của người hay không.

---

## 3. Tồn kho ĐỌNG (inventory ageing) — CHẶN vì không có lịch sử trạng thái

**Tín hiệu bị chặn**: "N căn ở trạng thái `available` quá X ngày", "tồn kho đọng".

**Bằng chứng chặn**: `units` có `status` và `source_updated_at`, nhưng
`source_updated_at` đổi theo **bất kỳ** thay đổi nào của bản ghi, không riêng
việc đổi trạng thái. Không có bảng lịch sử chuyển trạng thái căn. Vì vậy không
tính được "đã ở trạng thái này bao lâu".

**Điều kiện tiên quyết**
* Bảng lịch sử chuyển trạng thái căn (`unit_id`, trạng thái cũ, trạng thái mới,
  thời điểm, nguồn), **hoặc** tối thiểu một cột `status_changed_at` do hệ nguồn
  cấp — không phải do đích tự suy khi thấy giá trị đổi.
* Một mốc "ngày mở bán" cho từng căn/phân khu: tồn kho chưa mở bán mà bị tính là
  "đọng" sẽ tạo báo động giả ở mọi dự án chưa ra hàng.
* Quyết định nghiệp vụ về ngưỡng ngày, theo từng loại căn — repo hiện không có
  ngưỡng nào.

---

## 4. Độ phủ đường ống, tuổi giai đoạn, tỷ lệ chuyển đổi, trượt tiến độ — CHẶN

**Tín hiệu bị chặn**: độ phủ pipeline, deal nằm lâu ở một giai đoạn, tỷ lệ chuyển
đổi giảm, trượt tiến độ giai đoạn.

**Bằng chứng chặn**: `deals.status` có đủ 7 giai đoạn (`lead`, `qualified`,
`interested`, `viewing`, `reserved`, `sold`, `lost` —
`src/services/domain_projection.py`), nhưng chỉ có **ba** mốc thời gian:
`reserved_at`, `sold_at`, `lost_at`. Bốn giai đoạn đầu **không có mốc vào**.
Cũng không có bảng ảnh chụp (snapshot) trạng thái theo thời gian.

Hệ quả cụ thể:
* Tuổi giai đoạn chỉ tính được cho `reserved` — không tính được cho `lead`,
  `qualified`, `interested`, `viewing`.
* Tỷ lệ chuyển đổi cần **hai** thời điểm để so; hiện chỉ đọc được trạng thái
  **hiện tại**, nên không có xu hướng nào để nói "đang giảm".
* Không endpoint nào của trang danh mục trả về số lượng theo giai đoạn.

**Điều kiện tiên quyết**
* Ảnh chụp chuyển giai đoạn: mỗi lần deal đổi giai đoạn ghi một dòng
  (`deal_id`, giai đoạn cũ, giai đoạn mới, thời điểm) — do hệ NGUỒN cấp.
* Hoặc, tối thiểu, một mốc vào cho từng giai đoạn trên chính bảng `deals`.
* Quyết định về định nghĩa "chuyển đổi": tính theo deal hay theo căn, và kỳ đối
  chiếu là gì.

---

## 5. Chất lượng lead và kênh khách — CHẶN vì thiếu mô hình VÀ chưa có luật riêng tư

**Tín hiệu bị chặn**: chất lượng lead giảm, một kênh khách xấu đi.

**Bằng chứng chặn**: không có bảng khách hàng/lead/liên hệ. Một "lead" chỉ là
dòng `deals` có `status='lead'`. Không có cột nguồn/kênh/chiến dịch trên `deals`.

**Điều kiện tiên quyết**
* Mô hình lead/khách hàng với **nguồn/kênh** là trường hạng nhất.
* **Luật riêng tư TRƯỚC khi có cột**: dữ liệu cá nhân nào được sao sang hệ này,
  giữ bao lâu, ai đọc được, che thế nào trong log và trong ngữ cảnh đưa vào
  agent. Tầng đồng bộ hiện đã có `redact()` cho giá trị nhạy cảm — quy tắc mới
  phải mở rộng cơ chế đó, không đi vòng qua nó.
* Không được đưa dữ liệu định danh cá nhân vào bất kỳ endpoint tổng hợp nào của
  bảng tín hiệu.

---

## 6. Kháng giá, chiết khấu, rủi ro biên — CHẶN vì KHÔNG CÓ giá ở bất kỳ đâu

**Tín hiệu bị chặn**: kháng giá, áp lực chiết khấu, rủi ro biên lợi nhuận.

**Bằng chứng chặn**: `src/models/tables.py` **không có cột giá nào** — không trên
`units`, không trên `deals`, không ở đâu cả. Chính sách chiết khấu chỉ tồn tại
dưới dạng tệp tĩnh `data/discount_policies.json`, đọc bởi
`src/agents/advisory_tools.py`; nó **không gắn với căn hay giao dịch nào**.

Đây cũng là lý do `attentionScore` hiện chấm theo **số căn**, không theo giá trị
tiền: không có trường tiền nào để chấm.

**Điều kiện tiên quyết**
* Giá niêm yết trên `units`, có lịch sử hiệu lực (giá đổi theo đợt mở bán).
* Giá giao dịch thực tế + chiết khấu đã áp trên `deals`.
* Quyết định về đơn vị tiền tệ, làm tròn, và thuế/phí có nằm trong con số hay
  không.
* Quyết định phân quyền: ai được xem số tiền — hiện `business_viewer` xem được
  mọi thứ trong phạm vi dự án của mình.

---

## 7. Việc nền tảng có thể làm mà KHÔNG cần quyết định lược đồ

Ba việc dưới đây gỡ được giới hạn thật mà không cần bảng mới, nên chúng thuộc
backlog kỹ thuật chứ không thuộc tài liệu quyết định này:

1. **Endpoint tổng hợp cấp danh mục** cho hấp thụ/xếp hạng. Hiện trang phải gọi
   vòng theo từng dự án và bị chặn bởi hai trần (`SIGNAL_PROJECT_LIMIT`,
   `RANKING_PROJECT_LIMIT`), và chính bảng tín hiệu đang tự nêu giới hạn đó.
2. **Lịch sử điểm/thứ hạng theo lần chạy.** `GET /ranking` chỉ trả trạng thái
   HIỆN TẠI nên không có mốc nền để phát tín hiệu "tụt hạng".
3. **Đưa `CLASS_ANOMALY` của bộ so sánh ra giao diện.** Bộ phân loại bất thường
   thật sự duy nhất của repo nằm ở `src/services/comparison_rules.py` và hiện
   không tới được người dùng nào. Đây là bất thường TÍNH TOÁN (tầng 1), không
   phải bất thường bán hàng, và phải được trình bày đúng như vậy.

---

## 8. Điều tuyệt đối không làm

* Không thêm cột giữ chỗ, migration giả, hay giá trị mẫu cho bất kỳ mục nào ở
  trên.
* Không đặt ngưỡng "hợp lý" thay cho quyết định nghiệp vụ. Nếu chưa có ngưỡng,
  tín hiệu phải mang `thresholdStatus: "UNDEFINED"` và chỉ nêu quan sát.
* Không đổi tên trường GỘP hiện có thành "ròng".
* Không khai `layer: 3` trên bất kỳ tín hiệu nào cho tới khi §4 và §5 được giải
  quyết.
