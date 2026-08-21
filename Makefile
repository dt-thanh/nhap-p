.PHONY: run test lint format check clean \
        up down logs ps migrate revision \
        test-docker lint-docker build-frontend reset \
        setup bootstrap testdb token urls e2e test-minicrm test-product test-all

# ---- Docker stack (đường chạy chính) ----
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

# ⚠️ PHÁ HUỶ: xoá container + volume (mất toàn bộ dữ liệu DB và file đã upload)
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

# LẦN ĐẦU chỉ cần một lệnh này.
setup: bootstrap up testdb urls

# Sinh các file .env còn thiếu. KHÔNG ghi đè file đã có.
bootstrap:
	bash scripts/bootstrap_env.sh

# In địa chỉ các giao diện/API.
urls:
	@echo ""
	@echo "  Mini CRM UI     http://localhost:5174"
	@echo "  AbsorbIQ UI     http://localhost:5173"
	@echo "  Mini CRM API    http://localhost:8100/docs"
	@echo "  AbsorbIQ API    http://localhost:8000/docs"
	@echo "  Mini CRM DB     localhost:5433    Product DB  localhost:5432"
	@echo ""

# Database riêng cho bộ test Mini CRM. Chạy một lần, sau đó vô hại.
testdb:
	@docker compose exec -T minicrm_db psql -U minicrm -tc \
		"SELECT 1 FROM pg_database WHERE datname='minicrm_checkpoint1_test'" \
		| grep -q 1 || docker compose exec -T minicrm_db psql -U minicrm -c \
		"CREATE DATABASE minicrm_checkpoint1_test;"
	@echo "Database test Mini CRM đã sẵn sàng."

# Token admin để gọi API bằng curl hoặc chạy E2E.
token:
	@grep -E '^MINICRM_AUTH_ADMIN_TOKEN=' .env | grep -v '=$$' | tail -1 | cut -d= -f2-

# Test Mini CRM. Cần stack đang chạy (dùng Postgres ở cổng 5433).
test-minicrm:
	cd minicrm && MINICRM_TEST_DATABASE_URL="postgresql+asyncpg://minicrm:minicrm@localhost:5433/minicrm_checkpoint1_test" PYTHONPATH=. pytest tests/ -q

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
