"""`compare()` phải phân biệt được ba loại thay đổi khác hẳn nhau.

Hàm này là thứ quyết định câu trả lời cho "đường nạp file cũ có bị động vào
không" ở các giai đoạn sau. Nó là hàm thuần nên test được không cần DB — và
chính vì sẽ có người tin vào output của nó, nó cần test.
"""

from scripts.baseline_dev_data import compare


def _snapshot(revision="0007_s3_domain_model", **tables):
    return {
        "meta": {"database": "T", "alembic_revision": revision, "captured_at": "2026-08-09T00:00:00+00:00"},
        "tables": {
            name: {"rows": rows, "checksum": checksum, "columns": ["a", "b"]}
            for name, (rows, checksum) in tables.items()
        },
    }


def test_identical_snapshots_produce_no_findings():
    snap = _snapshot(sales_records=(360, "abc"), units=(0, "EMPTY"))
    assert compare(snap, snap) == []


def test_row_count_change_is_reported():
    before = _snapshot(sales_records=(360, "abc"))
    after = _snapshot(sales_records=(361, "def"))

    findings = compare(before, after)

    assert len(findings) == 1
    assert "sales_records" in findings[0]
    assert "360 -> 361" in findings[0]


def test_content_change_with_same_row_count_is_reported():
    """Sửa tại chỗ là loại thay đổi dễ lọt nhất — đếm dòng không thấy gì cả."""
    before = _snapshot(sales_records=(360, "abc"))
    after = _snapshot(sales_records=(360, "CHANGED"))

    findings = compare(before, after)

    assert len(findings) == 1
    assert "NỘI DUNG" in findings[0], "sửa tại chỗ phải được nêu khác với đổi số dòng"


def test_new_and_removed_tables_are_reported():
    before = _snapshot(sales_records=(1, "a"))
    after = _snapshot(sales_records=(1, "a"), units=(0, "EMPTY"))

    assert any("bảng MỚI: units" in f for f in compare(before, after))
    assert any("bảng BỊ XOÁ: units" in f for f in compare(after, before))


def test_revision_change_is_reported():
    before = _snapshot(revision="0006_sync_foundation", sales_records=(1, "a"))
    after = _snapshot(revision="0007_s3_domain_model", sales_records=(1, "a"))

    findings = compare(before, after)

    assert len(findings) == 1
    assert "0006_sync_foundation -> 0007_s3_domain_model" in findings[0]


def test_missing_column_is_surfaced_instead_of_a_false_checksum_diff():
    """Cột trong baseline mà DB hiện tại không còn → nói rõ là thiếu cột.

    Nếu không, việc XOÁ một cột sẽ hiện ra thành "nội dung đã đổi" và người đọc
    sẽ đi tìm nhầm chỗ.
    """
    before = _snapshot(sales_records=(360, "abc"))
    after = _snapshot(sales_records=(360, "abc"))
    after["tables"]["sales_records"] = {
        "rows": None,
        "checksum": None,
        "columns": ["a", "b"],
        "error": "thiếu cột: ['b']",
    }

    findings = compare(before, after)

    assert len(findings) == 1
    assert "thiếu cột" in findings[0]
