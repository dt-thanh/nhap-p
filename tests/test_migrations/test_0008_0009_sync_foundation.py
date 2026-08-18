"""Migration 0008 + 0009 — tiến, lùi, tiến lại, và các ràng buộc thật.

Cùng cách làm với test của 0005/0006/0007: database dùng-một-lần, soi catalog của
Postgres, và thử GHI dữ liệu thật để chứng minh ràng buộc có hiệu lực chứ không
chỉ tồn tại trên giấy.

Hai migration được test chung một file vì chúng là một cặp không tách rời:
`sync_payloads.credential_id` có khoá ngoại trỏ sang `sync_credentials`, nên thứ
tự 0008 → 0009 là bắt buộc chứ không phải sở thích. Test `test_downgrade_order`
chốt lại điều đó.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật",
)

BASE_REVISION = "0007_s3_domain_model"
CREDENTIALS_REVISION = "0008_sync_credentials"
PAYLOADS_REVISION = "0009_sync_payloads"

SHA256_ZERO = "0" * 64


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"


@pytest.fixture
def scratch_db():
    """Database dùng-một-lần. Bị xoá kể cả khi test hỏng giữa chừng."""
    name = f"mig89_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(TEST_DATABASE_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name}
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def upgraded(scratch_db):
    """DB đã ở head 0009, kèm một dự án và một lô để treo payload vào."""
    _alembic(scratch_db, "upgrade", PAYLOADS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', :d, now())"),
            {"i": project_id, "d": "2026-01-01"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at) "
                "VALUES (:i, :p, 'pending', 0, 0, now())"
            ),
            {"i": run_id, "p": project_id},
        )
    try:
        yield {"engine": engine, "url": scratch_db, "project_id": project_id, "run_id": run_id}
    finally:
        engine.dispose()


def _tables(conn) -> set[str]:
    return set(conn.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())


def _insert_credential(conn, *, prefix="afsk_abc", key_hash=None, instance="crm-a", **extra):
    credential_id = uuid.uuid4()
    columns = {
        "id": credential_id,
        "source_system": "mini_crm",
        "source_instance_id": instance,
        "key_prefix": prefix,
        # `is None` chứ không phải `or`: chuỗi rỗng là một giá trị test HỢP LỆ ở
        # đây (nó phải bị CHECK chặn), còn `or` sẽ lặng lẽ thay nó bằng hash ngẫu
        # nhiên và test hoá ra không kiểm gì cả.
        "key_hash": (uuid.uuid4().hex + uuid.uuid4().hex) if key_hash is None else key_hash,
        "label": "",
        **extra,
    }
    names = ", ".join(columns)
    binds = ", ".join(f":{c}" for c in columns)
    conn.execute(sa.text(f"INSERT INTO sync_credentials ({names}, created_at) VALUES ({binds}, now())"), columns)
    return credential_id


# --- Tiến / lùi -------------------------------------------------------------


def test_upgrade_creates_both_tables(upgraded):
    with upgraded["engine"].connect() as conn:
        tables = _tables(conn)
    assert "sync_credentials" in tables
    assert "sync_payloads" in tables


def test_downgrade_removes_both_tables_and_upgrade_restores_them(scratch_db):
    """Vòng tiến → lùi → tiến phải về đúng chỗ cũ, không sót mảnh nào."""
    _alembic(scratch_db, "upgrade", PAYLOADS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            assert {"sync_credentials", "sync_payloads"} <= _tables(conn)

        _alembic(scratch_db, "downgrade", BASE_REVISION)
        with engine.connect() as conn:
            remaining = _tables(conn)
        assert "sync_payloads" not in remaining
        assert "sync_credentials" not in remaining
        # Bảng của giai đoạn trước phải còn nguyên — downgrade chỉ được gỡ đúng
        # phần mình thêm vào.
        assert {"units", "deals", "crm_source_records", "upload_files"} <= remaining

        _alembic(scratch_db, "upgrade", PAYLOADS_REVISION)
        with engine.connect() as conn:
            assert {"sync_credentials", "sync_payloads"} <= _tables(conn)
    finally:
        engine.dispose()


def test_downgrade_order_payloads_before_credentials(scratch_db):
    """0009 phải lùi trước 0008.

    `sync_payloads.credential_id` trỏ sang `sync_credentials`, nên gỡ bảng khoá
    trước sẽ vướng khoá ngoại. Alembic đi ngược chuỗi revision nên thứ tự này là
    hệ quả của `down_revision`; test chốt lại để một lần sửa chuỗi nhầm sẽ lộ ra.
    """
    _alembic(scratch_db, "upgrade", PAYLOADS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        _alembic(scratch_db, "downgrade", CREDENTIALS_REVISION)
        with engine.connect() as conn:
            tables = _tables(conn)
        assert "sync_payloads" not in tables
        assert "sync_credentials" in tables, "lùi một bước phải chỉ gỡ 0009"
    finally:
        engine.dispose()


# --- Ràng buộc của sync_credentials -----------------------------------------


def test_key_hash_must_be_unique(upgraded):
    """Hai dòng cùng hash = cùng một khoá cấp hai lần, và lúc đó 'khoá này thuộc
    instance nào' không còn câu trả lời."""
    shared = uuid.uuid4().hex + uuid.uuid4().hex
    with upgraded["engine"].begin() as conn:
        _insert_credential(conn, key_hash=shared, instance="crm-a")

    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_credential(conn, key_hash=shared, instance="crm-b")
    assert "uq_sync_credentials_key_hash" in str(exc.value)


@pytest.mark.parametrize(
    "bad_hash, constraint",
    [
        ("short", "ck_sync_credentials_key_hash_length"),
        ("", "ck_sync_credentials_key_hash_length"),
    ],
)
def test_key_hash_length_is_enforced(upgraded, bad_hash, constraint):
    """Hash bị cắt cụt không được lọt vào bảng — nó sẽ khớp nhầm."""
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_credential(conn, key_hash=bad_hash)
    assert constraint in str(exc.value)


def test_blank_source_instance_is_rejected(upgraded):
    """Instance rỗng phá tan ranh giới cô lập — khoá đó thuộc về ai?"""
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_credential(conn, instance="")
    assert "ck_sync_credentials_source_instance_not_blank" in str(exc.value)


def test_expiry_before_creation_is_rejected(upgraded):
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO sync_credentials (id, source_system, source_instance_id, key_prefix, key_hash, "
                    "label, created_at, expires_at) VALUES (:i, 'mini_crm', 'crm-a', 'afsk_abc', :h, '', "
                    "now(), now() - interval '1 day')"
                ),
                {"i": uuid.uuid4(), "h": uuid.uuid4().hex + uuid.uuid4().hex},
            )
    assert "ck_sync_credentials_expires_after_created" in str(exc.value)


def test_two_live_keys_may_share_a_prefix(upgraded):
    """Xoay khoá cấp bản mới TRƯỚC khi thu hồi bản cũ, nên hai khoá còn sống có
    thể trùng 8 ký tự đầu. Index prefix vì thế KHÔNG được unique."""
    # Đúng 8 ký tự — khớp ck_sync_credentials_key_prefix_length.
    shared_prefix = "afsk_aaa"
    with upgraded["engine"].begin() as conn:
        _insert_credential(conn, prefix=shared_prefix)
        _insert_credential(conn, prefix=shared_prefix)

    with upgraded["engine"].connect() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM sync_credentials WHERE key_prefix = :p"), {"p": shared_prefix}
        ).scalar_one()
    assert count == 2


# --- Ràng buộc của sync_payloads --------------------------------------------


def _insert_payload(conn, run_id, *, sha=SHA256_ZERO, size=10, records=1, credential_id=None):
    payload_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO sync_payloads (id, sync_run_id, payload, payload_sha256, payload_bytes, record_count, "
            "received_at, credential_id) VALUES (:i, :r, '{}'::jsonb, :s, :b, :c, now(), :cred)"
        ),
        {"i": payload_id, "r": run_id, "s": sha, "b": size, "c": records, "cred": credential_id},
    )
    return payload_id


def test_one_payload_per_run(upgraded):
    """Hai payload cho cùng một lô nghĩa là tầng idempotency đã hỏng — chặn ở DB
    để lỗi đó lộ ra ngay thay vì âm thầm nhân bản."""
    with upgraded["engine"].begin() as conn:
        _insert_payload(conn, upgraded["run_id"])

    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_payload(conn, upgraded["run_id"])
    assert "uq_sync_payloads_run" in str(exc.value)


def test_payload_requires_an_existing_run(upgraded):
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_payload(conn, uuid.uuid4())
    assert "fk_sync_payloads_sync_run_id" in str(exc.value)


def test_zero_byte_payload_is_rejected(upgraded):
    """Payload 0 byte không phải payload; nó là một lô rỗng bị ghi nhầm."""
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_payload(conn, upgraded["run_id"], size=0)
    assert "ck_sync_payloads_bytes_positive" in str(exc.value)


def test_sha256_length_is_enforced(upgraded):
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_payload(conn, upgraded["run_id"], sha="abc")
    assert "ck_sync_payloads_sha256_length" in str(exc.value)


def test_revoking_a_credential_must_not_delete_payload_history(upgraded):
    """Xoá khoá đặt `credential_id` về NULL, KHÔNG xoá dòng payload.

    Lịch sử "lô này đã vào bằng khoá nào" mất đi là chấp nhận được; mất luôn cả
    payload thì không — đó là thứ duy nhất cho phép chạy lại và điều tra.
    """
    with upgraded["engine"].begin() as conn:
        credential_id = _insert_credential(conn)
        _insert_payload(conn, upgraded["run_id"], credential_id=credential_id)

    with upgraded["engine"].begin() as conn:
        conn.execute(sa.text("DELETE FROM sync_credentials WHERE id = :i"), {"i": credential_id})

    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text("SELECT credential_id FROM sync_payloads WHERE sync_run_id = :r"), {"r": upgraded["run_id"]}
        ).one()
    assert row[0] is None, "ON DELETE SET NULL không có hiệu lực"


def test_deleting_a_run_cascades_to_its_payload(upgraded):
    """Xoá lô thì payload của nó đi theo — payload mồ côi không tra được về đâu."""
    with upgraded["engine"].begin() as conn:
        _insert_payload(conn, upgraded["run_id"])

    with upgraded["engine"].begin() as conn:
        conn.execute(sa.text("DELETE FROM upload_files WHERE id = :i"), {"i": upgraded["run_id"]})

    with upgraded["engine"].connect() as conn:
        remaining = conn.execute(sa.text("SELECT count(*) FROM sync_payloads")).scalar_one()
    assert remaining == 0


# --- 0010: bảo vệ payload thô khỏi xoá dây chuyền ---------------------------

RETENTION_REVISION = "0010_sync_payload_retention"


def _delete_rule(conn, constraint: str) -> str:
    """Quy tắc ON DELETE thật của một khoá ngoại, đọc từ catalog.

    Đọc catalog chứ không tin vào mã migration: câu hỏi cần trả lời là "database
    ĐANG cư xử thế nào", không phải "migration định làm gì".
    """
    return conn.execute(
        sa.text("SELECT confdeltype FROM pg_constraint WHERE conname = :c"), {"c": constraint}
    ).scalar_one()


def test_upgrade_switches_the_payload_fk_to_restrict(scratch_db):
    """0009 để CASCADE; 0010 phải đổi thành RESTRICT."""
    _alembic(scratch_db, "upgrade", PAYLOADS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            # 'c' = CASCADE trong pg_constraint.confdeltype
            assert _delete_rule(conn, "fk_sync_payloads_sync_run_id") == "c"

        _alembic(scratch_db, "upgrade", RETENTION_REVISION)
        with engine.connect() as conn:
            # 'r' = RESTRICT
            assert _delete_rule(conn, "fk_sync_payloads_sync_run_id") == "r"
    finally:
        engine.dispose()


def test_downgrade_restores_cascade(scratch_db):
    _alembic(scratch_db, "upgrade", RETENTION_REVISION)
    _alembic(scratch_db, "downgrade", PAYLOADS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            assert _delete_rule(conn, "fk_sync_payloads_sync_run_id") == "c"
            assert "ix_sync_payloads_received_at" not in {
                row[0]
                for row in conn.execute(sa.text("SELECT indexname FROM pg_indexes WHERE tablename='sync_payloads'"))
            }
    finally:
        engine.dispose()


def test_restrict_blocks_deleting_a_run_that_still_has_a_payload(scratch_db):
    """Bằng chứng hành vi: xoá lô khi payload còn tồn tại phải THẤT BẠI."""
    _alembic(scratch_db, "upgrade", RETENTION_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', :d, now())"),
                {"i": project_id, "d": "2026-01-01"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at) "
                    "VALUES (:i, :p, 'completed', 0, 0, now())"
                ),
                {"i": run_id, "p": project_id},
            )
            _insert_payload(conn, run_id)

        with pytest.raises(sa.exc.IntegrityError) as exc:
            with engine.begin() as conn:
                conn.execute(sa.text("DELETE FROM upload_files WHERE id = :i"), {"i": run_id})
        assert "fk_sync_payloads_sync_run_id" in str(exc.value)

        # Chính sách lưu giữ: xoá payload trước thì xoá lô được — mất lịch sử
        # payload phải là hành động CỐ Ý, chứ không bị cấm hẳn.
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM sync_payloads WHERE sync_run_id = :i"), {"i": run_id})
            conn.execute(sa.text("DELETE FROM upload_files WHERE id = :i"), {"i": run_id})

        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM upload_files")).scalar_one() == 0
    finally:
        engine.dispose()
