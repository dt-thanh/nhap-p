# Runbook: đổi schema database

> **Ghi nhận sự cố quy trình (Phase 8D).** Revision `0013_calculator_comparisons`
> đã được áp dụng lên database dev bởi **entrypoint của container**, không phải
> bởi người triển khai: service `api` chạy với `RUN_MIGRATIONS=true`, nên
> `docker compose up api` chạy `alembic upgrade head` ngay khi khởi động — **trước**
> khi bản sao lưu được tạo.
>
> Không mất dữ liệu: 0013 thuần cộng thêm, và đường lùi đã được kiểm trên chính
> database dev (dấu vân `absorption_daily` không đổi qua downgrade/upgrade). Nhưng
> **thứ tự đã sai**, và thứ tự chính là toàn bộ giá trị của quy trình — một bản
> sao lưu lấy SAU khi migrate không cứu được gì.
>
> Tài liệu này là phần sửa.

## Quy tắc

| | |
|---|---|
| **Production KHÔNG BAO GIỜ migrate tự động** | `docker/entrypoint.sh` **thoát với lỗi** nếu `RUN_MIGRATIONS=true` và `APP_ENV=production`. Không phải cảnh báo — nó không chạy. |
| **`RUN_MIGRATIONS=true` chỉ dành cho dev/test** | Ở đó, mất database dev là bất tiện, không phải sự cố. |
| **Production dùng `scripts/migrate.sh`** | Script buộc thứ tự sao lưu → migrate → xác minh. |
| **Không sao lưu được thì không migrate** | Script dừng lại, không hỏi lại. |

## Vì sao migrate tự động lúc khởi động là sai ở production

Ba lý do độc lập, mỗi lý do đủ để cấm:

1. **Nó chạy trước khi ai kịp sao lưu.** Đúng chuyện đã xảy ra ở 8D.
2. **Nó gắn việc đổi schema vào việc khởi động lại tiến trình.** Một lần restart
   vì OOM, vì đổi biến môi trường, vì scale — mỗi lần đều thành một lần migrate
   không ai định làm, vào thời điểm không ai chọn.
3. **`upgrade head` chạy tới revision mới nhất trong IMAGE**, không phải tới
   revision mà người triển khai đang nghĩ tới. Một image cũ hơn đang chạy song
   song sẽ gặp schema mà nó không hiểu.

## Quy trình

```bash
# 1. Xác nhận đang đứng ở đâu và định đi tới đâu
docker compose exec api alembic current
docker compose exec api alembic history | head

# 2. Sao lưu → migrate → xác minh, một lệnh, không bỏ bước được
bash scripts/migrate.sh 0014_ten_revision

# 3. Chạy bộ test đầy đủ trên database test
TEST_TARGET=tests bash scripts/test_db.sh -q

# 4. So với baseline; nếu schema đổi thì ghi baseline mới
python -m scripts.baseline_dev_data --compare docs/baselines/dev_0013.json
python -m scripts.baseline_dev_data --write   docs/baselines/dev_0014.json
```

`scripts/migrate.sh` tự làm, theo đúng thứ tự này:

1. Nạp `.env`, in ra database / `APP_ENV` / revision hiện tại — để không ai migrate
   nhầm môi trường vì tưởng mình đang ở chỗ khác.
2. Nếu `APP_ENV=production`: bắt gõ đúng **tên database** để xác nhận. Không có cờ
   `--yes`; một cờ như thế sẽ nằm sẵn trong lịch sử shell của người tiếp theo.
3. `pg_dump --format=custom` vào `backups/pre_<target>_<timestamp>.dump`.
4. **Kiểm bản sao lưu đọc được** bằng `pg_restore --list`, không chỉ kiểm nó tồn
   tại — một file 0 byte cũng "tồn tại", và người ta phát hiện ra điều đó vào đúng
   lúc cần phục hồi.
5. `alembic upgrade <target>`.
6. Đọc lại `alembic_version` và so dữ liệu với baseline.

## Lùi lại

```bash
docker compose exec api alembic downgrade <revision_truoc>
```

Trước khi lùi, đọc `downgrade()` của revision đó. Không phải downgrade nào cũng vô
hại:

| Revision | Hạ cấp làm gì |
|---|---|
| `0013` | Xoá view + bảng `calculator_comparisons`. **Không chạm gì khác.** |
| `0012` | **XOÁ DỮ LIỆU CÓ CHỦ ĐÍCH** — xoá mọi dòng `calculator='domain_units_deals'` trước khi khôi phục chỉ mục unique hẹp. Dòng `legacy_aggregate` giữ nguyên. |
| `0010` | Đổi khoá ngoại `sync_payloads` CASCADE → RESTRICT. Lùi lại sẽ mở lại khả năng xoá payload thô theo dây chuyền. |

Nếu bản sao lưu phải dùng tới:

```bash
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --clean --if-exists < backups/pre_<revision>_<timestamp>.dump
```

## Danh sách kiểm trước khi migrate production

- [ ] `APP_ENV=production` và `RUN_MIGRATIONS` **không** đặt là `true`
- [ ] Đã đọc `upgrade()` **và** `downgrade()` của revision sắp chạy
- [ ] Revision đã chạy trên dev/test, và đường lùi đã được thử THẬT
- [ ] Bộ test đầy đủ xanh trên database test ở đúng revision đó
- [ ] Có cửa sổ bảo trì, hoặc đã xác nhận revision này chạy được lúc đang phục vụ
- [ ] Biết trước sẽ mất bao lâu (bảng lớn + `ALTER` = khoá)
- [ ] `scripts/migrate.sh` là thứ được chạy, không phải `alembic upgrade head` gõ tay
