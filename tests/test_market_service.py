from src.services.market import _market_status, _score_percent, _unit_payload


def test_deal_status_overrides_unit_status():
    assert _market_status("available", "reserved") == "reserved"
    assert _market_status("available", "sold") == "sold"


def test_score_is_rendered_as_percent_or_null():
    assert _score_percent("0.876") == 88
    assert _score_percent(None) is None


def test_unit_payload_uses_database_row_fields():
    row = {
        "unit_id": "11111111-1111-1111-1111-111111111111",
        "unit_code": "A-0101",
        "unit_type": "2BR",
        "unit_status": "available",
        "area_id": "22222222-2222-2222-2222-222222222222",
        "area_external_id": "AREA-1",
        "area_name": "Tower A",
        "area_sqm": "75.5",
        "score": "0.654",
        "rank_in_project": 3,
        "rank_in_area": 1,
        "deal_status": None,
    }

    payload = _unit_payload(row)

    assert payload["id"] == "A-0101"
    assert payload["tower"] == "Tower A"
    assert payload["status"] == "Available"
    assert payload["area"] == 75.5
    assert payload["score"] == 65
    assert payload["phase_id"] == "db_snapshot"
