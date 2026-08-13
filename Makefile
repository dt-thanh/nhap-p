.PHONY: run test lint format typecheck check clean \
        up down logs ps migrate revision \
        test-docker lint-docker build-frontend reset

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

migrate:
	docker compose run --rm api alembic upgrade head

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

typecheck:
	mypy src/

check: lint format test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
