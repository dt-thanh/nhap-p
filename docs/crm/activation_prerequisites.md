# Điều kiện kích hoạt Mini CRM — danh sách chặn

> **Trạng thái: Phase 8E.** Mini CRM **chưa tồn tại**. Không dòng dữ liệu thật nào
> đã từng đi qua hệ thống này. Mọi thứ đã kiểm tới giờ đều chạy trên **fixture
> tổng hợp**, và fixture tổng hợp **không chứng minh** được khả năng tương thích
> với một CRM chưa được thiết kế.
>
> Tài liệu này là câu trả lời máy móc cho câu hỏi "đã sẵn sàng chưa" — thay cho
> một cuộc thảo luận.

Ba nhóm, ba chủ sở hữu khác nhau:

* **Nhóm A** — thứ đội **Mini CRM** phải trả lời. Ta không tự đóng được.
* **Nhóm B** — thứ **hệ thống này** phải có. Đóng dần qua các sub-phase.
* **Nhóm C** — thứ chỉ đóng được khi **payload thật** tồn tại.

---

## Nhóm A — cần đội Mini CRM trả lời (CHẶN)

| # | Câu hỏi | Vì sao chặn | Trạng thái |
|---|---|---|---|
| A1 | `external_id` có bền vững trọn đời và không dùng lại không? | Dùng lại id là trộn hai thực thể vào một dòng | ⬜ chưa hỏi được |
| A2 | Có `source_revision` tăng dần đơn điệu không? | Không có thì rơi về timestamp, thứ tự kém chính xác | ⬜ |
| A3 | `source_updated_at` có offset múi giờ và đủ độ phân giải không? | Thiếu → đụng độ giả; sai múi → lệch ngày, mà ngày là khoá của cả chuỗi hấp thụ | ⬜ |
| **A4** | **Mốc lịch sử có được giữ khi chuyển trạng thái không?** | **Không giữ = mất vĩnh viễn ngày đặt cọc** | ⬜ **xem bên dưới** |
| A5 | CRM biết bản ghi của nó thuộc dự án/phân khu nào không? | Không biết thì mọi bản ghi bị từ chối vì không tra được phân khu | ⬜ |
| A6 | CRM phát được `delete` tường minh không? | Không thì chỉ còn suy ra xoá từ ảnh chụp | ⬜ |
| A7 | CRM xuất được ảnh chụp đầy đủ theo dự án không? | Không thì **không bao giờ** đối soát được với nguồn | ⬜ |
| A8 | Trong một lô, căn có luôn đến trước giao dịch không? | Không thì phải xây bộ đệm bản ghi chờ | ⬜ |
| A9 | `external_batch_id` có giữ nguyên khi retry không? | Không thì mất idempotency mức lô | ⬜ |
| A10 | Từ vựng trạng thái đầy đủ là gì? | Thiếu → bản ghi bị từ chối, hoặc tệ hơn: ánh xạ sai | ⬜ |
| A11 | CRM công bố được số lượng/checksum để đối soát không? | Không thì không chứng minh được bản sao đúng | ⬜ |
| A12 | Khối lượng dự kiến bao nhiêu? | Vượt giới hạn mục 14 thì phải đổi chiến lược chia lô | ⬜ |
| — | **Danh tính dự án**: CRM biết `projects.id` của ta, hay dùng mã riêng? | Không có ánh xạ thì không lô nào vào được dự án nào | ⬜ hợp đồng §3.1 |

### A4 nói riêng

Phase 8B **không** trả lời A4 — không ai ở đây trả lời được. Nó làm một việc
khác: bảo đảm câu trả lời "CRM không giữ được lịch sử" sẽ hiện ra thành **một
loạt bản ghi bị từ chối ngay lô đầu tiên**, thay vì thành một cột `reserved_at`
rỗng dần mà không ai nhận ra trong nhiều tháng.

Ba kết cục có thể, và hệ quả của từng cái:

| Câu trả lời của CRM | Hệ quả |
|---|---|
| **Có, giữ được** | A4 đóng. Không cần làm gì thêm. |
| **Không, chỉ lưu mốc của trạng thái hiện tại** | Mọi giao dịch chuyển trạng thái bị từ chối. Phải bật `SYNC_PRESERVE_DROPPED_TIMESTAMPS` **và** chấp nhận rằng lịch sử đặt cọc là xấp xỉ — quyết định này phải được ghi nhận, và **cổng cắt sang phải hỏng** khi cờ đang bật. |
| **Không rõ / chưa thiết kế** | Vẫn chặn. "Chưa rõ" không phải "được". |

---

## Nhóm B — hệ thống này phải có

| # | Yêu cầu | Sub-phase | Trạng thái |
|---|---|---|---|
| B1 | Kiểm lineage miền chạy theo lịch | 8A | ✅ scheduler, cron `DOMAIN_RECOMPUTE_AUDIT_CRON` |
| B2 | Có cảnh báo khi lần kiểm phát hiện lạc hậu | 8A | ✅ log `domain.recompute.audit_stale` + `GET /api/v1/ops/domain-recompute` |
| B3 | Cảnh báo đã được kiểm chứng bằng một lần hỏng **cố ý** | 8G | ⬜ |
| B4 | Chốt A4: đánh rơi mốc lịch sử là lỗi có mã, không phải im lặng | **8B** | ✅ `HISTORY_TIMESTAMP_DROPPED` |
| B5 | Phân biệt được vắng mặt / null tường minh / có giá trị | **8B** | ✅ `payload_completeness`, hợp đồng §4.3 |
| B6 | `SYNC_PRESERVE_DROPPED_TIMESTAMPS` mặc định TẮT | **8B** | ✅ `src/config.py` |
| B7 | Bộ kiểm phù hợp (conformance) chạy được, không ghi gì | **8C** | ✅ `scripts/conformance_check.py`, xem `conformance_testing.md` |
| B8 | Chạy song song hai bộ tính và lưu lại kết quả theo thời gian | **8D** | ✅ `calculator_comparisons` (0013), xem `parallel_run.md` |
| B9 | Quy tắc so sánh cũ ↔ miền, quyết định TRƯỚC khi nhìn số | **8E** | ✅ `src/services/comparison_rules.py`, xem `calculator_comparison_rules.md` |
| B10 | Đối soát `scope='source'` | 8F | ⬜ |
| B11 | Cổng cắt sang, và nó phải HỎNG hôm nay vì đúng lý do | 8G | ⬜ |
| B12 | Khoá API cấp theo `source_instance_id`, không commit secret | 3 | ✅ |
| B13 | Có backup đã kiểm chứng, lấy sau lần đổi schema cuối | 0/3/5/6 | ✅ |
| B14 | Migrate tự động BỊ CẤM ở production; quy trình sao lưu→migrate→xác minh | **8E** | ✅ `docker/entrypoint.sh` từ chối, `scripts/migrate.sh`, `docs/runbooks/migrations.md` |

---

## Nhóm C — chỉ đóng được khi có payload thật

| # | Yêu cầu | Vì sao không đóng sớm được |
|---|---|---|
| C1 | Bộ kiểm phù hợp (8C) chạy sạch trên payload **thật** | Fixture tổng hợp do chính ta viết; chúng chứng minh bộ kiểm hoạt động, không chứng minh CRM tương thích. Công cụ đã sẵn sàng — thứ còn thiếu là payload |
| C2 | Đối soát `scope='source'` đạt với **số liệu thật** của CRM | Không có nguồn thì không có gì để so ngoài chính ta |
| C3 | ≥ 14 ngày chạy song song liên tục, không có chênh lệch mức chặn (đếm bằng `cutover_evidence_count`, quy tắc ở 8E) | Phụ thuộc lịch, không rút ngắn bằng cách làm nhanh hơn. Hạ tầng ghi đã có (8D); đếm ngày CHỈ tính dòng qua view `calculator_comparisons_gate` — dòng `domain_has_data=false` là cái khớp rỗng tuếch |
| C4 | Từ vựng trạng thái thật đã ánh xạ đủ | Chỉ biết khi thấy dữ liệu thật |
| C5 | Khối lượng thật nằm trong giới hạn §14 | — |

---

## Trước khi ĐỔI `projects.absorption_calculator` của bất kỳ dự án nào

Tất cả các mục sau phải đúng **cùng lúc**:

1. Toàn bộ **Nhóm A** đã trả lời — không mục nào ở trạng thái "chưa rõ".
2. Toàn bộ **Nhóm B** ✅.
3. Toàn bộ **Nhóm C** ✅ cho chính dự án sắp cắt.
4. `python -m scripts.requeue_missing_domain_recompute` **thoát 0**.
5. `SYNC_PRESERVE_DROPPED_TIMESTAMPS` **TẮT** — hoặc, nếu bật, quyết định chấp
   nhận số liệu xấp xỉ đã được ghi nhận bằng văn bản và cổng cắt sang được ghi đè
   một cách tường minh, có người chịu trách nhiệm.
6. Lineage `legacy_aggregate` của dự án đó **còn nguyên và khác rỗng** — đích quay
   về phải tồn tại TRƯỚC khi cắt, không phải sau.

Cắt sang là thao tác **của con người**, từng dự án một, dự án đầu tiên chạy riêng.
Không script nào trong repo này được phép tự đổi `absorption_calculator`.
