"""CLI seed for the `crm_real_data.json`-derived AI/dev fixture (async, `src/db.py`).

    python -m scripts.seed_backend_from_json

Thin wrapper — all mapping/statement logic lives in
`scripts/_seed_ai_crm_fixture_core.py`, shared with
`alembic/versions/0019_seed_ai_crm_fixture.py` so both callers stay in sync.
Prefer running the Alembic revision (`alembic upgrade head`) for anything that
needs to be reproducible across environments; this CLI form exists for local
iteration without touching migration history (e.g. after editing
`scripts/fixtures/ingestion_seed.json` by hand while debugging).

Reads `scripts/fixtures/ingestion_seed.json` — see
`scripts/derive_ingestion_seed_from_crm_real_data.py` for how that file is
produced from the raw `crm_real_data.json` export (not part of this repo).

Ghi thẳng bằng SQLAlchemy Core (async), id tất định (`uuid5`),
`ON CONFLICT DO UPDATE`, MỘT transaction — KHÔNG đi qua `ProjectService.create_project`
(bị Phase D thu hẹp — chỉ ingestion mới tạo được `projects`/`areas`); đây là lối
ghi trực tiếp CÓ CHỦ ĐÍCH dành cho dữ liệu demo/AI-dev, cùng khuôn với
`scripts/seed_dev.py`, tách biệt khỏi luồng ghi nghiệp vụ thật.

KHÔNG chạm Mini CRM (`scripts/seed_mini_crm_from_json.py` lo phần đó) và KHÔNG
chạm `ranking_configs`/`ranking_runs`/`ranking_scores`/`feature_snapshots` —
bốn bảng đó có ranh giới riêng (`tests/test_ranking_boundary.py`: CHỈ
`src/ranking/service.py` được ghi vào chúng, kể cả từ Phase 6 trở đi).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncConnection

from scripts._seed_ai_crm_fixture_core import SeedError, build_upserts, load_seed
from src.db import get_engine


async def _run() -> int:
    try:
        data = load_seed()
        plan = build_upserts(data)
    except SeedError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1

    engine = get_engine()
    conn: AsyncConnection
    async with engine.begin() as conn:
        for _table_name, stmt in plan.statements:
            await conn.execute(stmt)
    await engine.dispose()

    print("=== Mapping report ===")
    for name, n in plan.counts.items():
        print(f"  [{name}] {n} upserted")
    print("\nOK — một transaction, đã commit.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
