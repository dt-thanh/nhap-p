from __future__ import annotations

import csv

from scripts.backfill_minicrm_project_locations import (
    build_plan,
    normalize_project_name,
    parse_address,
    read_addresses,
)


def test_parser_accepts_prefix_whitespace_trailing_dot_and_unicode():
    parsed = parse_address("  DỰ ÁN  Cầu Rồng. ,  Đường Lê Lợi, Đà Nẵng.  ", 2)
    assert parsed is not None
    assert parsed.project_name == "Cầu Rồng"
    assert parsed.location == "Đường Lê Lợi, Đà Nẵng"
    assert normalize_project_name("Dự án Cầu Rồng") == normalize_project_name("cầu   rồng.")


def test_parser_rejects_missing_or_ambiguous_address_shape():
    assert parse_address(None, 2) is None
    assert parse_address("Dự án Không có địa điểm", 3) is None
    assert parse_address("Dự án , Hà Nội", 4) is None
    assert parse_address("Dự án Có tên,", 5) is None


def test_csv_reader_reports_malformed_rows(tmp_path):
    path = tmp_path / "source.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Address"])
        writer.writerow(["Dự án Một, Hà Nội"])
        writer.writerow(["Dự án Hỏng"])
    parsed, malformed = read_addresses(path)
    assert len(parsed) == 1
    assert malformed == [3]


def test_plan_matches_case_insensitively_and_does_not_overwrite_existing_location():
    parsed = [parse_address("Dự án Ocean Park, Hà Nội", 2)]
    plan = build_plan(parsed, [{"id": "p1", "name": "oCeAn   PaRk", "location": "Hà Nội"}])
    assert plan.updates == ()
    assert plan.report["unchanged"] == 1

    different = [parse_address("Dự án Ocean Park, Hải Phòng", 2)]
    plan = build_plan(different, [{"id": "p1", "name": "Ocean Park", "location": "Hà Nội"}])
    assert plan.updates == ()
    assert plan.report["skipped_existing_location"] == 1


def test_plan_is_idempotent_and_overwrite_is_explicit():
    parsed = [parse_address("Dự án Ocean Park, Hải Phòng", 2)]
    existing = [{"id": "p1", "name": "Ocean Park", "location": "Hà Nội"}]
    plan = build_plan(parsed, existing, overwrite=True)
    assert plan.updates == (("p1", "Hải Phòng"),)
    after = [{"id": "p1", "name": "Ocean Park", "location": "Hải Phòng"}]
    assert build_plan(parsed, after, overwrite=True).updates == ()


def test_plan_chooses_majority_and_skips_tied_conflict():
    parsed = [
        parse_address("Dự án A, Hà Nội", 2),
        parse_address("Dự án A, Hà Nội", 3),
        parse_address("Dự án A, Đà Nẵng", 4),
    ]
    plan = build_plan(parsed, [{"id": "p1", "name": "A", "location": None}])
    assert plan.updates == (("p1", "Hà Nội"),)
    assert plan.report["conflicts"][0]["selected"] == "Hà Nội"

    tied = [parse_address("Dự án B, Hà Nội", 2), parse_address("Dự án B, Đà Nẵng", 3)]
    plan = build_plan(tied, [{"id": "p2", "name": "B", "location": None}])
    assert plan.updates == ()
    assert plan.report["conflicts"][0]["selected"] is None


def test_plan_reports_unmatched_and_duplicate_database_names():
    parsed = [parse_address("Dự án Missing, Hà Nội", 2), parse_address("Dự án Duplicate, Hà Nội", 3)]
    plan = build_plan(
        parsed,
        [
            {"id": "p1", "name": "Duplicate", "location": None},
            {"id": "p2", "name": "duplicate", "location": None},
        ],
    )
    assert plan.report["unmatched"] == ["Missing"]
    assert plan.report["ambiguous"] == ["Duplicate"]
