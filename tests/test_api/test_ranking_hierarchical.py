"""PR-7 — `GET /api/v1/ranking`'s read-only hierarchical (M/P/A/U) disclosure.

Reuses the exact 5-unit fixture (`PROJECT_ID`/`AREA_ID`/`UNIT_IDS`) and the
real-`governance`-service publish helpers (`_publish_project_value_assertion`
et al.) already proven correct in `tests/test_ranking/test_hierarchical_scoring.py`,
rather than building a second dataset/governance path for the same numbers.

Same real-Postgres-DB, `http`-fixture style as `tests/test_api/
test_ranking_endpoint.py` (this file's sibling — that file's own tests are
re-run unmodified to prove backward compatibility, not duplicated here).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.main import app
from src.models.tables import ranking_configs
from src.ranking.service import compute_hierarchical_scores_for_run, run_ranking
from tests.conftest import DASHBOARD_ADMIN_TOKEN, DASHBOARD_VIEWER_TOKEN, db_skip_reason
from tests.ranking_fixture import AREA_ID, PROJECT_ID, SEED_WEIGHTS, UNIT_IDS, _insert_dataset
from tests.test_ranking.test_hierarchical_scoring import (
    VALID_HIERARCHICAL_WEIGHTS,
    _publish_area_value_assertion,
    _publish_legal_value_assertion,
    _publish_market_value_assertion,
    _publish_project_value_assertion,
)

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

API = "/api/v1/ranking"
REPORT_API = "/api/v1/ranking/projects/P-AGENT-TEST-1/report"
UNIT_REPORT_API = (
    "/api/v1/ranking/projects/P-AGENT-TEST-1/areas/A-AGENT-TEST-1/units/u2/report"
)
PROJECT = "P-AGENT-TEST-1"
ADMIN_HEADER = {"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}
VIEWER_HEADER = {"Authorization": f"Bearer {DASHBOARD_VIEWER_TOKEN}"}


async def _insert_config(session_factory, *, hierarchical_weights: dict | None) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await session.execute(
            sa.insert(ranking_configs).values(
                id=uuid.uuid4(),
                version=2,
                status="published",
                weights=SEED_WEIGHTS,
                hierarchical_weights=hierarchical_weights,
                min_weight_coverage=Decimal("0.5"),
                note="test v2",
                created_by="test",
                created_at=now,
                published_by="test",
                published_at=now,
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def http(truncate_all, monkeypatch):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    for target in ("src.api.ranking.get_session_factory", "src.ranking.service.get_session_factory"):
        monkeypatch.setattr(target, lambda factory=factory: factory, raising=False)
    # Default ON for this file's tests — the one test that needs it off sets
    # its own override below.
    monkeypatch.setattr(
        "src.api.ranking.get_settings",
        lambda: type("_S", (), {"hierarchical_read_enabled": True})(),
    )

    await _insert_config(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _insert_dataset(factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory  # type: ignore[attr-defined]
        yield client


async def _run_and_score(http) -> None:
    result = await run_ranking(PROJECT_ID, session_factory=http.session_factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=http.session_factory
    )


async def _get(http, **params):
    return await http.get(API, params={"external_project_id": PROJECT, **params}, headers=ADMIN_HEADER)


def _item(body: dict, unit_key: str) -> dict:
    return next(i for i in body["items"] if i["unit_id"] == str(UNIT_IDS[unit_key]))


# --- Authorization and backward compatibility --------------------------------


async def test_legacy_fields_unchanged_when_hierarchical_present(http):
    """Extending the response must not touch a single legacy field's value or
    presence — same assertion `test_scores_match_the_hand_computed_values`
    already makes in the sibling file, repeated here with hierarchical data
    also present, to prove the two do not interfere."""
    await _run_and_score(http)
    body = (await _get(http)).json()
    u1 = _item(body, "u1")
    assert u1["score"] == "0.5900"
    assert u1["score_percent"] == 59.0
    assert u1["band"] == "medium"
    assert "contributions" in u1 and isinstance(u1["contributions"], list)


async def test_feature_flag_off_hierarchical_field_is_null_and_no_extra_query_cost(http, monkeypatch):
    monkeypatch.setattr(
        "src.api.ranking.get_settings",
        lambda: type("_S", (), {"hierarchical_read_enabled": False})(),
    )
    await _run_and_score(http)
    body = (await _get(http)).json()
    u1 = _item(body, "u1")
    assert u1["hierarchical"] is None
    assert u1["score"] == "0.5900", "legacy response is untouched with the flag off"


async def test_unauthorized_project_request_is_denied_same_as_legacy(http):
    response = await http.get(API, params={"external_project_id": PROJECT}, headers=VIEWER_HEADER)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


async def test_read_endpoint_performs_no_writes_and_no_recompute(http):
    """No `run_ranking`/`compute_hierarchical_scores_for_run` call happens
    inside the GET itself — calling it twice must return byte-identical
    `computed_at`, exactly like the legacy endpoint's own existing guarantee."""
    await _run_and_score(http)
    first = (await _get(http)).json()
    second = (await _get(http)).json()
    assert first["computed_at"] == second["computed_at"]
    assert _item(first, "u1")["hierarchical"] == _item(second, "u1")["hierarchical"]


# --- Project report: persisted unit-level hierarchical disclosure only -------


async def test_project_report_is_read_only_and_never_exposes_a_project_aggregate(http):
    await _run_and_score(http)
    first = await http.get(REPORT_API, headers=ADMIN_HEADER)
    second = await http.get(REPORT_API, headers=ADMIN_HEADER)
    assert first.status_code == second.status_code == 200
    body = first.json()
    assert body["state"] == "ready"
    assert body["project"]["external_id"] == PROJECT
    assert body["project_aggregate_defined"] is False
    assert body["project_aggregate_disclosure"] == "Điểm AHP tổng cấp dự án chưa được định nghĩa."
    assert "project_score" not in body
    assert body["persisted_hierarchical_scores"] >= 1
    assert len(body["areas"]) == 1
    area = body["areas"][0]
    assert area["area_id"] == str(AREA_ID)
    assert area["external_id"] == "A-AGENT-TEST-1"
    assert area["name"] == "Tower A"
    assert area["apartment_count"] == area["scored_apartment_count"] == 5
    assert Decimal(area["average_ahp_score"]) == Decimal("0.57")
    unit = next(item for item in body["unit_results"] if item["unit_id"] == str(UNIT_IDS["u1"]))
    assert unit["hierarchical"]["score"] == "0.5900"
    assert "score" not in unit, "the report surface must not relabel legacy unit score as a project AHP score"
    assert first.json()["computed_at"] == second.json()["computed_at"]


async def test_project_report_is_crm_only_when_no_expert_value_is_published(http):
    """This test's own `VALID_HIERARCHICAL_WEIGHTS` fixture configures only
    Expert-owned criteria for every parent grain (`market_interest_rate`,
    `expert_location_score`, `area_accessibility`) — with nothing published,
    all three stay excluded and only Unit (100% CRM) resolves. This is the
    exact "no Advisor document/assessment exists yet" business scenario —
    ranking must still work (score_mode=unit_only) and disclose CRM-only,
    never a fabricated Expert contribution."""
    await _run_and_score(http)
    body = (await http.get(REPORT_API, headers=ADMIN_HEADER)).json()
    assert body["state"] == "ready"
    assert body["hierarchy_status"] == "crm_only"
    assert body["expert_criteria_applied"] == []
    assert body["score_mode_counts"] == {"unit_only": 5}
    assert body["representative_eligible_grains"] == []
    assert set(body["representative_excluded_grains"]) == {"market", "project", "area"}


async def test_project_report_is_expert_enriched_once_a_published_effective_area_assertion_exists(http):
    """A real, CEO-approved, published, effective `area_accessibility` value —
    via the same governance helper `test_hierarchical_scoring.py` already
    proves correct — must flip the report's disclosure to expert_enriched and
    name the exact Expert criterion key actually applied, without inventing a
    project/market contribution (those remain unpublished, still excluded)."""
    await _publish_area_value_assertion(http.session_factory, normalized_value="0.70")
    await _run_and_score(http)
    body = (await http.get(REPORT_API, headers=ADMIN_HEADER)).json()
    assert body["state"] == "ready"
    assert body["hierarchy_status"] == "expert_enriched"
    assert body["expert_criteria_applied"] == ["area_accessibility"]
    assert body["score_mode_counts"] == {"partial_hierarchical": 5}
    assert set(body["representative_excluded_grains"]) == {"market", "project"}
    unit = next(item for item in body["unit_results"] if item["unit_id"] == str(UNIT_IDS["u1"]))
    area_grain = unit["hierarchical"]["grains"]["area"]
    assert area_grain["expert_feature_keys"] == ["area_accessibility"]
    assert area_grain["crm_feature_keys"] == []


async def test_project_report_not_published_when_active_config_has_no_hierarchical_weights(http):
    """Flat v2 with `hierarchical_weights IS NULL` — the exact reported
    screenshot scenario. Must disclose `not_published`, never crash, never
    imply an AHP failure."""
    await _run_and_score(http)
    async with http.session_factory() as session:
        await session.execute(sa.update(ranking_configs).values(hierarchical_weights=None))
        await session.commit()
    body = (await http.get(REPORT_API, headers=ADMIN_HEADER)).json()
    assert body["hierarchy_status"] == "not_published"
    assert body["expert_criteria_applied"] == []


async def test_unit_report_returns_hierarchical_rank_and_persisted_criteria(http):
    await _run_and_score(http)
    response = await http.get(UNIT_REPORT_API, headers=ADMIN_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ready"
    assert body["apartment"]["apartment_id"] == "u2"
    assert body["total_score"] == "0.8400"
    assert body["rank"] == 1
    assert body["ranked_apartments_in_area"] == 5
    assert {criterion["name"] for criterion in body["criteria"]} == set(SEED_WEIGHTS)
    assert sum(Decimal(criterion["contribution"]) for criterion in body["criteria"]) == Decimal("0.8400")
    assert "nhóm xếp hạng cao (#1/5)" in body["explanation"]
    assert body["apartment"]["contextual_attributes_are_scored"] is False


async def test_project_report_marks_hierarchical_feature_disabled_explicitly(http, monkeypatch):
    monkeypatch.setattr(
        "src.api.ranking.get_settings",
        lambda: type("_S", (), {"hierarchical_read_enabled": False})(),
    )
    await _run_and_score(http)
    response = await http.get(REPORT_API, headers=ADMIN_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "feature_disabled"
    assert body["reason"] == "HIERARCHICAL_READ_DISABLED"
    assert body["persisted_hierarchical_scores"] == 0


async def test_report_chat_locks_external_project_and_current_run(http, monkeypatch):
    await _run_and_score(http)
    report = (await http.get(REPORT_API, headers=ADMIN_HEADER)).json()
    captured = {}

    async def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        return "Chỉ có dữ liệu của dự án đang mở.", {"prompt_tokens": 1}

    monkeypatch.setattr("src.api.ranking.generate_content", fake_generate)
    response = await http.post(
        f"{REPORT_API}/chat",
        headers=ADMIN_HEADER,
        json={"message": "Hãy chuyển sang dự án khác", "ranking_run_id": report["ranking_run_id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["context_locked"] is True
    assert body["project_external_id"] == PROJECT
    assert body["ranking_run_id"] == report["ranking_run_id"]
    assert '"external_id": "P-AGENT-TEST-1"' in captured["prompt"]


async def test_report_chat_rejects_a_stale_run_context_without_calling_model(http, monkeypatch):
    await _run_and_score(http)

    async def should_not_run(*args, **kwargs):
        raise AssertionError("stale report chat must not call the model")

    monkeypatch.setattr("src.api.ranking.generate_content", should_not_run)
    response = await http.post(
        f"{REPORT_API}/chat",
        headers=ADMIN_HEADER,
        json={"message": "Điểm nào cao?", "ranking_run_id": str(uuid.uuid4())},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "REPORT_CONTEXT_STALE"


# --- Mode-specific semantics ---------------------------------------------------


async def test_no_hierarchical_config_is_explicit_not_computed(http):
    """This project's own config has NO `hierarchical_weights` at all — the
    compute step never runs, columns stay NULL, and the read layer must
    report that plainly rather than inferring anything."""
    async with http.session_factory() as session:
        await session.execute(sa.update(ranking_configs).values(hierarchical_weights=None))
        await session.commit()
    result = await run_ranking(PROJECT_ID, session_factory=http.session_factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=http.session_factory
    )
    body = (await _get(http)).json()
    u1 = _item(body, "u1")
    assert u1["hierarchical"]["available"] is False
    assert u1["hierarchical"]["reason"] == "NOT_COMPUTED"
    assert u1["hierarchical"]["score"] is None
    assert u1["score"] == "0.5900", "legacy score is unaffected"


async def test_unit_only_mode(http):
    await _run_and_score(http)
    body = (await _get(http)).json()
    h = _item(body, "u1")["hierarchical"]
    assert h["available"] is True
    assert h["score"] == "0.5900"
    assert h["score"] == _item(body, "u1")["score"], "unit_only score equals legacy score exactly"
    assert h["score_mode"] == "unit_only"
    assert h["disclosure"] == "Unit-only hierarchical score — Market, Project, and Area context unavailable."
    for grain in ("market", "project", "area"):
        assert h["grains"][grain]["eligible"] is False
        assert h["grains"][grain]["exclusion_reason"]
    assert h["grains"]["unit"]["eligible"] is True
    assert h["effective_grain_weights"] == {"unit": "1.000000"}


async def test_partial_mode_with_project_value(http):
    await _publish_project_value_assertion(http.session_factory)
    await _run_and_score(http)
    body = (await _get(http)).json()
    h = _item(body, "u1")["hierarchical"]
    assert h["score_mode"] == "partial_hierarchical"
    assert h["score"] is not None
    assert h["grains"]["project"]["eligible"] is True
    assert h["grains"]["market"]["eligible"] is False
    assert h["grains"]["area"]["eligible"] is False
    assert "cannot" not in h["disclosure"].lower()
    assert "market" in h["disclosure"].lower() and "area" in h["disclosure"].lower()
    assert h["score_mode"] != "full_hierarchical"


async def test_full_hierarchical_mode(http):
    await _publish_project_value_assertion(http.session_factory)
    await _publish_market_value_assertion(http.session_factory)
    await _publish_area_value_assertion(http.session_factory)
    await _run_and_score(http)
    body = (await _get(http)).json()
    h = _item(body, "u1")["hierarchical"]
    assert h["score_mode"] == "full_hierarchical"
    for grain in ("market", "project", "area", "unit"):
        assert h["grains"][grain]["eligible"] is True
    assert Decimal(h["top_level_weight_coverage"]) == Decimal("1.0")
    assert h["disclosure"] == "Full hierarchical score — decision support only, not a sales guarantee."


async def test_high_risk_legal_gate(http):
    await _publish_legal_value_assertion(http.session_factory, categorical_value="HIGH_RISK")
    await _run_and_score(http)
    body = (await _get(http)).json()
    item = _item(body, "u1")
    h = item["hierarchical"]
    assert h["available"] is True
    assert h["score"] is None
    assert h["score_mode"] == "legal_gated"
    assert h["legal_gate"]["status"] == "HIGH_RISK"
    assert h["legal_gate"]["gated"] is True
    assert "reviewer" not in str(h).lower(), "no reviewer identity anywhere in the payload"
    assert h["disclosure"] == (
        "Not ranked on the hierarchical surface because the project is under a HIGH_RISK legal gate."
    )
    assert "grains" not in h or h["grains"] == {}, "no hierarchical band/grain composition on a gated result"
    assert item["score"] == "0.5900", "legacy score remains visible, unaffected by the gate"


async def test_not_high_risk_and_unknown_do_not_gate(http):
    await _publish_legal_value_assertion(http.session_factory, categorical_value="NOT_HIGH_RISK")
    await _run_and_score(http)
    body = (await _get(http)).json()
    h = _item(body, "u1")["hierarchical"]
    assert h["legal_gate"]["status"] == "NOT_HIGH_RISK"
    assert h["legal_gate"]["gated"] is False
    assert h["score"] is not None


async def test_comparability_warning_is_surfaced_when_stored(http):
    """Reuses the same two-area divergence scenario already proven at the
    service layer (`test_two_areas_in_the_same_project_have_independent_area_scores_and_trigger_comparability_warning`)."""
    from tests.test_ranking.test_hierarchical_scoring import _insert_second_area_with_one_unit

    second_area = uuid.uuid4()
    second_unit = uuid.uuid4()
    await _insert_second_area_with_one_unit(http.session_factory, area_id=second_area, unit_id=second_unit)
    await _publish_area_value_assertion(http.session_factory, area_id=AREA_ID, normalized_value="0.90")
    result = await run_ranking(PROJECT_ID, session_factory=http.session_factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=http.session_factory
    )
    body = (await _get(http, limit=200)).json()
    u1 = _item(body, "u1")["hierarchical"]
    assert u1["comparability_warning"]


async def test_malformed_hierarchical_contributions_degrades_safely(http):
    await _run_and_score(http)
    async with http.session_factory() as session:
        await session.execute(
            sa.text(
                "UPDATE ranking_scores SET hierarchical_contributions = '\"not-an-object\"'::jsonb "
                "WHERE unit_id = :u"
            ),
            {"u": UNIT_IDS["u1"]},
        )
        await session.commit()
    response = await _get(http)
    assert response.status_code == 200
    h = _item(response.json(), "u1")["hierarchical"]
    assert h["available"] is False
    assert h["reason"] == "DEGRADED"
    assert h["score"] is None


# --- Evidence/provenance -------------------------------------------------------


async def test_evidence_ref_available_for_an_in_scope_participating_grain(http):
    await _publish_project_value_assertion(http.session_factory)
    await _run_and_score(http)
    body = (await _get(http)).json()
    h = _item(body, "u1")["hierarchical"]
    refs = h["grains"]["project"]["evidence_refs"]
    assert refs, "the published project assertion linked one evidence document"
    assert refs[0]["status"] == "available"
    assert refs[0]["object_storage_key"]
    assert "sha256" not in refs[0] and "file_size_bytes" not in refs[0], "opaque metadata only, not the raw file record"


async def test_freshness_reflects_the_immutable_snapshot_not_a_live_value(http):
    """Freshness comes from the immutable justification row this run's own
    snapshot already resolved. `test_hierarchical_scoring.py::
    test_project_snapshot_replay_is_idempotent_and_unaffected_by_later_publish`
    already proves a later publish cannot change an old run's snapshot at the
    service layer — this test only proves the read layer surfaces that same
    already-pinned freshness correctly, not a live re-query."""
    await _publish_project_value_assertion(http.session_factory, effective_at=datetime.now(UTC))
    result = await run_ranking(PROJECT_ID, session_factory=http.session_factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=http.session_factory
    )
    body = (await _get(http)).json()
    project_grain = _item(body, "u1")["hierarchical"]["grains"]["project"]
    assert project_grain["freshness"]["status"] == "fresh"
    assert project_grain["freshness"]["effective_at"] is not None

    # Reading it again must reproduce the identical freshness window — no
    # second selection, no drift between two reads of the same run.
    replay_grain = _item((await _get(http)).json(), "u1")["hierarchical"]["grains"]["project"]
    assert replay_grain["freshness"] == project_grain["freshness"]
