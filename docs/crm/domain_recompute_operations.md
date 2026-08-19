# Vận hành: tính lại lineage miền sau đồng bộ

> Trạng thái: Phase 8A. Lineage `domain_units_deals` được ghi tự động sau mỗi lô
> đồng bộ làm đổi bản sao, nhưng **chưa ai đọc nó** — mọi dự án vẫn ở
> `legacy_aggregate`. Tài liệu này mô tả một cửa sổ sự cố đã biết, lưới an toàn
> đã được lên lịch từ Phase 8A, và những việc còn lại trước khi cắt sang.

## 1. Luồng

```
POST /api/v1/sync/{entity}
  └─ SyncRunService._process
       ├─ [TRANSACTION]  crm_source_records + units/deals + upload_files
       │                  └─ thu thập area_id của các dòng vừa đổi
       ├─ COMMIT  ←──────────── dữ liệu nghiệp vụ an toàn từ đây
       └─ enqueue INGEST_QUEUE → run_domain_recompute(project_id, area_ids, sync_run_id)
                                    └─ compute() → persist()  [chỉ domain_units_deals]
```

Điều kiện xếp hàng: `inserted + updated + tombstoned > 0`. Lô chỉ sinh
`skip_stale`, `duplicate_noop`, `conflict`, lô bị từ chối hết, và lô chạy lại
(replay) **không** xếp hàng — chúng không đổi gì để mà tính lại.

## 2. Cửa sổ sự cố đã biết

**Giữa `COMMIT` và `enqueue` có một khoảng không được bảo vệ.**

Nếu tiến trình chết, Redis mất kết nối, hoặc container bị OOM ngay tại đó thì:

* `units` / `deals` **đã đổi và đã commit**;
* **không job nào được xếp hàng**;
* **không cột nào trong database ghi lại rằng còn nợ một lần tính lại.**

Hệ thống vì vậy **không tự phát hiện** được, và cũng không tự sửa được.

### Vì sao không đảo thứ tự

Xếp hàng trước khi commit sẽ tệ hơn: worker có thể nhận job và bắt đầu tính
trước khi transaction commit xong, rồi tính ra lineage dựa trên trạng thái cũ —
sai một cách im lặng, thay vì thiếu một cách phát hiện được.

### Vì sao chưa làm outbox

Một bảng outbox giao dịch (ghi ý định tính lại trong CÙNG transaction, một tiến
trình khác đọc và xếp hàng) sẽ đóng hẳn cửa sổ này. Đó là cách đúng khi khối
lượng lớn hơn. Ở quy mô hiện tại nó thêm một bảng, một tiến trình, và một vòng
đời nữa để sai — nên lựa chọn hiện tại là **phát hiện thay vì phòng ngừa**, với
điều kiện việc phát hiện thực sự được chạy.

### Vì sao hiện tại chấp nhận được

Không ai đọc lineage miền. Một lần tính lại bị bỏ lỡ chỉ khiến một bảng không
được đọc trở nên lạc hậu, và lần đồng bộ kế tiếp chạm cùng dự án sẽ sửa nó.

**Điều này thay đổi hoàn toàn khi cắt sang.**

## 3. Phát hiện

Quy tắc: một dự án là *lạc hậu* nếu có lô `api_push` đã kết thúc với
`projections` cho thấy bản sao đã đổi, mà `finished_at` mới hơn
`max(absorption_daily.computed_at)` của lineage miền — hoặc dự án chưa có dòng
miền nào.

Quy tắc dùng đúng ba khoá `inserted/updated/tombstoned` mà tầng xếp hàng dùng,
nên phát hiện và xếp hàng không thể lệch nhau về định nghĩa "đã đổi".

Logic nằm ở `src/services/domain_recompute_audit.py`, dùng chung cho cả ba phía
gọi dưới đây. An toàn khi chạy lại: job tính lại là idempotent, xếp lại hàng
nhiều lần không nhân đôi gì.

### 3.1 Tự động (Phase 8A) — đường chính

Scheduler đẩy `run_domain_recompute_audit` vào `INGEST_QUEUE` theo cron. Job phát
hiện, **báo động**, rồi xếp lại hàng.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DOMAIN_RECOMPUTE_AUDIT_ENABLED` | `true` | Tắt thì scheduler ghi `scheduler.audit_disabled` |
| `DOMAIN_RECOMPUTE_AUDIT_CRON` | `15 * * * *` | Hằng giờ — độ trễ phát hiện chính là khoảng thời gian số liệu sai |
| `DOMAIN_RECOMPUTE_AUDIT_REPAIR` | `true` | Tắt thì chỉ báo động, không xếp hàng |

Job vào `INGEST_QUEUE` chứ không phải hàng đợi forecast: lần kiểm là một câu SQL,
xếp sau một job Prophet chạy hàng phút sẽ biến chu kỳ hằng giờ thành "khi nào
Prophet xong thì tính". `coalesce=True`, `max_instances=1` — worker chết vài
tiếng rồi sống lại không bắn một loạt lần kiểm bị lỡ.

**Vá xong vẫn báo động.** Sửa im lặng sẽ khiến một đường `enqueue` hỏng vĩnh viễn
trông y hệt một hệ thống khoẻ mạnh: mỗi lô đều lỡ, mỗi lần kiểm đều vá, và không
ai biết cửa sổ sự cố đã thành trạng thái thường trực.

### 3.2 Cảnh báo

Hai đường, không đường nào cần thêm secret hay dịch vụ ngoài:

**Log có cấu trúc** — mức `error`, tên sự kiện ổn định:

```
domain.recompute.audit_stale         stale_projects=… project_ids=[…] never_computed=[…]
domain.recompute.audit_repair_failed stale_projects=… project_ids=[…] error_type=…
domain.recompute.audit_failed        error_type=…     # chính lần kiểm hỏng
```

**Đầu dò HTTP** — `GET /api/v1/ops/domain-recompute`:

```bash
curl -H "X-Ops-Token: $OPS_API_TOKEN" http://localhost:8000/api/v1/ops/domain-recompute
```

* Cần header `X-Ops-Token`; `OPS_API_TOKEN` **rỗng = endpoint TẮT (503)**, không
  phải mở. Endpoint trả `project_id` nội bộ, nên mặc định phải là đóng.
* Token vận hành riêng, **không** dùng khoá API của CRM: khoá đồng bộ được cấp
  cho một `source_instance_id` để GHI dữ liệu vào, không phải để đọc trạng thái
  toàn hệ thống.
* **Chỉ đọc** — endpoint không xếp hàng gì. Việc vá do job định kỳ làm.
* Luôn trả **HTTP 200** khi đã xác thực, kể cả lúc `status="degraded"`. Trả 503
  khi có dự án lạc hậu sẽ khiến mọi công cụ uptime báo "API sập" trong khi API
  hoàn toàn khoẻ.

### 3.3 Thủ công — lúc xử lý sự cố và cho cổng kiểm tra

```bash
python -m scripts.requeue_missing_domain_recompute            # báo cáo, thoát 1 nếu có
python -m scripts.requeue_missing_domain_recompute --enqueue  # xếp lại hàng
```

Script còn tồn tại vì hai việc job không làm được: chạy theo yêu cầu, và trả về
**mã thoát** để dùng trong cổng kiểm tra trước khi cắt sang.

## 4. YÊU CẦU BẮT BUỘC TRƯỚC KHI CẮT SANG

Trước khi bất kỳ dự án nào chuyển `projects.absorption_calculator` sang
`domain_units_deals`:

1. `python -m scripts.requeue_missing_domain_recompute` phải **thoát 0**. ⬜
2. Việc kiểm tra đó phải được **lên lịch**. ✅ *Phase 8A* — `scheduler` service,
   cron `DOMAIN_RECOMPUTE_AUDIT_CRON`.
3. Phải có **cảnh báo** khi nó phát hiện lạc hậu. ✅ *Phase 8A* — mục 3.2.
4. Cảnh báo đã được **kiểm chứng bằng một lần hỏng cố ý**, không chỉ bằng test. ⬜
   Xem `docs/crm/cutover_runbook.md` khi tài liệu đó tồn tại (Phase 8G).
5. Lần kiểm đã chạy sạch **liên tục ≥ 7 ngày**. ⬜ — đây là phụ thuộc lịch, không
   rút ngắn bằng cách làm nhanh hơn được.

Lý do: sau khi cắt, một lần tính lại bị bỏ lỡ không còn là "bảng không ai đọc bị
cũ" mà là **số liệu sai hiển thị cho người dùng**, không có dấu hiệu gì trên
giao diện.

> Phase 8A đóng mục 2 và 3. Mục 1, 4, 5 vẫn mở, và cắt sang vẫn **chưa** được
> phép — xem `docs/crm/activation_prerequisites.md` (Phase 8B).

## 5. Retry

Job xếp hàng với `Retry(max=3, interval=[10, 30, 60])`. An toàn vì `persist()`
xoá-rồi-ghi trong một transaction, giới hạn đúng lineage và đúng phạm vi phân
khu: chạy lại cho ra nội dung y hệt, chỉ `computation_id` và `computed_at` đổi.

Hết ba lần retry, job nằm ở failed registry của RQ và dự án sẽ bị công cụ ở mục 3
phát hiện.

## 6. Lỗi khi xếp hàng

Redis hỏng lúc xếp hàng **không** làm hỏng lô đồng bộ: lô đã commit, và báo lỗi
cho hệ nguồn sẽ nói sai rằng dữ liệu bị từ chối, đồng thời mời họ gửi lại một lô
đã áp dụng xong.

Thay vào đó, ghi log ở mức `error` với đủ ba định danh:

```
domain.recompute.enqueue_failed  sync_run_id=… project_id=… area_ids=[…]
```

Ba giá trị đó đủ để xếp lại hàng bằng tay mà không cần tra thêm gì.

## 7. Gỡ bỏ (rollback)

Không có migration nào ở Phase 7, nên gỡ bỏ là việc của mã nguồn.

* **KHÔNG BAO GIỜ** `Queue('ingest').empty()`. `INGEST_QUEUE` dùng chung với job
  parse file Excel/CSV; xoá sạch hàng đợi sẽ giết cả những job không liên quan.
* Job tính lại là idempotent và chỉ ghi lineage chưa ai đọc, nên cách an toàn
  nhất là **để các job đã xếp chạy nốt**.
* Cần gỡ gấp thì chỉ huỷ đúng job Phase 7 theo `job_id` đã ghi trong log
  `domain.recompute.enqueued`.
* Dòng miền đã ghi xoá được an toàn bằng một câu duy nhất — nó không thể chạm
  tới dòng của bộ tính cũ:

```sql
DELETE FROM absorption_daily
WHERE calculator = 'domain_units_deals';
```
