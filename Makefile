.PHONY: run test lint format check clean \
        up down logs ps migrate revision \
        test-docker lint-docker build-frontend reset \
        setup bootstrap testdb token urls e2e test-minicrm test-product test-all \
        dev-reset dev-up dev-reseed-from-minicrm

# ---- Docker stack (đường chạy chính) ----
# Cần .dev-secrets/minicrm_sync_api_key đã tồn tại trước (từ `make dev-reset`
# — xem bên dưới): service `minicrm` khai secret Compose trỏ tới file đó,
# thiếu file này thì `up`/`run` cho `minicrm` báo lỗi rõ ràng ngay lúc tạo
# container, không âm thầm chạy thiếu khoá.
up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker scheduler

ps:
	docker compose ps

# RUN_MIGRATIONS=false: lệnh one-off kế thừa env của service api,
# không tắt thì mỗi lần test đều chạy alembic upgrade head trước.
test-docker:
	docker compose run --rm -e RUN_MIGRATIONS=false api pytest tests/ -v

lint-docker:
	docker compose run --rm -e RUN_MIGRATIONS=false api ruff check src/ tests/

build-frontend:
	docker compose run --rm frontend npm run build

# Đổi schema CHỈ đi qua đường này. `alembic upgrade` trực tiếp bỏ qua bước sao
# lưu và bước xác minh — đúng thứ tự ngược đã gây sự cố ở Phase 8D, khi
# `docker compose up api` áp dụng 0013 lên database dev TRƯỚC khi có bản sao lưu.
# `scripts/migrate.sh` buộc: sao lưu → kiểm bản sao lưu đọc được → migrate → xác minh.
#
#   make migrate                      # lên revision mới nhất
#   make migrate rev=0013_calculator_comparisons
#
# Yêu cầu stack đang chạy (`make up`): script dùng `docker compose exec`.
migrate:
	bash scripts/migrate.sh $(rev)

revision:
	docker compose run --rm api alembic revision -m "$(m)"

# ⚠️ PHÁ HUỶ: xoá container + volume (mất toàn bộ dữ liệu DB và file đã upload),
# KHÔNG tự bootstrap lại — sau lệnh này stack đứng im, không service nào chạy.
# Muốn reset RỒI bootstrap lại tự động (khuyến nghị), dùng `make dev-reset`.
reset:
	docker compose down -v

# ---- Local (không Docker — cần tự cài requirements.txt) ----

run:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

# KHÔNG có target `typecheck`: mypy không nằm trong requirements.txt, nên nó chỉ
# là một lệnh luôn hỏng. Thêm mypy vào một codebase chưa gắn kiểu là một việc
# riêng, không phải một dòng Makefile.
check: lint format test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +

# ============================================================================
# Lối tắt cho HỆ THỐNG HAI SUBSYSTEM (Mini CRM + Product/AbsorbIQ)
#
# Các target ở trên tập trung vào backend Product. Phần dưới bổ sung phần Mini
# CRM và luồng chạy lần đầu. KHÔNG target nào ở đây ghi đè target phía trên;
# `migrate` vẫn là đường DUY NHẤT để đổi schema (qua scripts/migrate.sh, có sao
# lưu) — migration lúc khởi động do entrypoint xử lý qua RUN_MIGRATIONS.
# ============================================================================

# LẦN ĐẦU chỉ cần một lệnh này: reset data-only, migration, credential handoff
# nếu thiếu, rồi seed fixture qua API — xem scripts/dev-reset.sh.
setup: bootstrap
	./scripts/dev-reset.sh --yes --seed
	$(MAKE) testdb urls

# Sinh các file .env còn thiếu. KHÔNG ghi đè file đã có.
bootstrap:
	bash scripts/bootstrap_env.sh

# Hard reset rows của cả hai database dev — giữ schema, volumes, Keycloak,
# migration history và sync credential; mặc định không seed lại dữ liệu.
dev-reset:
	./scripts/dev-reset.sh --yes

# Khởi động bình thường, AN TOÀN — không xoá volume, không xoay credential.
# Đòi .dev-secrets/minicrm_sync_api_key đã có (từ một lần `make dev-reset`).
dev-up:
	./scripts/dev-up.sh

# Clear ONLY AbsorpIQ business data and rebuild it from existing MiniCRM
# records through HTTP CRUD -> transactional outbox -> relay.  Does not reset
# either database, Keycloak, users, settings, or the sync credential.
dev-reseed-from-minicrm:
	./scripts/dev-reseed-from-minicrm.sh --yes

# In địa chỉ các giao diện/API.
urls:
	@echo ""
	@echo "  Mini CRM UI     http://localhost:5174"
	@echo "  AbsorbIQ UI     http://localhost:5173"
	@echo "  Mini CRM API    http://localhost:8100/docs"
	@echo "  AbsorbIQ API    http://localhost:8000/docs"
	@echo "  Mini CRM DB     localhost:5434    Product DB  localhost:5432"
	@echo ""

# Database riêng cho bộ test Mini CRM. Chạy một lần, sau đó vô hại — idempotent
# cả bước tạo DB lẫn bước migrate. Thiếu bước migrate khiến các test đi thẳng
# vào `minicrm_checkpoint1_test` (không qua fixture `crm_app`/scratch-DB, vd.
# `tests/test_sync_client.py`) lỗi "relation ... does not exist" — bảng RỖNG,
# không phải bug ở test hay ở code. Đi qua script riêng (không gọi
# `alembic upgrade` thẳng trong Makefile) để không phạm luật
# `test_only_the_migration_script_and_the_dev_entrypoint_run_alembic_upgrade` —
# luật đó canh schema PRODUCTION, database này CHỈ là test (hậu tố `_test`).
testdb:
	@bash scripts/migrate_minicrm_testdb.sh
	@echo "Database test Mini CRM đã sẵn sàng."
	@echo "Database test Mini CRM đã sẵn sàng."

# Token admin để gọi API bằng curl hoặc chạy E2E.
token:
	@grep -E '^MINICRM_AUTH_ADMIN_TOKEN=' .env | grep -v '=$$' | tail -1 | cut -d= -f2-

# Test Mini CRM. Cần stack đang chạy (dùng Postgres ở cổng 5434).
test-minicrm:
	cd minicrm && MINICRM_TEST_DATABASE_URL="postgresql+asyncpg://minicrm:minicrm@localhost:5434/minicrm_checkpoint1_test" PYTHONPATH=. pytest tests/ -q

# Test xác thực/SSO phía Product (chạy offline, không cần stack).
test-product:
	pytest tests/auth/ -q --noconftest

# Toàn bộ test có thể chạy được + build cả hai frontend.
test-all: test-minicrm test-product
	cd minicrm/crm-frontend && npx tsc --noEmit -p tsconfig.app.json && npm run build
	cd frontend && npm run build && npx vitest run

# E2E thật đầu-cuối. Cần stack đang chạy.
e2e:
	@E2E_LIVE=1 \
	 E2E_MINICRM_ADMIN_TOKEN="$$(grep -E '^MINICRM_AUTH_ADMIN_TOKEN=' .env | grep -v '=$$' | tail -1 | cut -d= -f2-)" \
	 E2E_PRODUCT_DSN="postgresql://app:app@localhost:5432/absorption" \
	 pytest tests/e2e/ -v --noconftest
