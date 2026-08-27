"""`compute_hierarchical_scores_for_run()` — PR-1's post-run hierarchical
step, extended by PR-3 (Project grain) and PR-4 (Market grain).

DB-backed, reusing `tests/test_agent_e2e.py`'s five-unit fixture (PROJECT_ID/
AREA_ID/UNIT_IDS, legacy scores u1=0.59/u2=0.84/u3=0.59/u4=0.24 already proven
correct there) so this suite composes ON TOP of numbers already verified,
rather than re-deriving legacy arithmetic.

Tests that never publish a Project/Market value assertion stay
`score_mode=unit_only` (no writer/CEO workflow was exercised, not because
none exists) — `hierarchical_score` reduces to exactly the persisted legacy
`score`. Tests further down publish a real, CEO-approved value assertion
through the actual `governance` service and assert the resulting partial
composition. Area still has no writer/CEO workflow at all (PR-5).
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.tables import areas, ranking_configs, ranking_scores, units
from src.ranking.service import compute_hierarchical_scores_for_run, run_ranking
from src.services.ranking_config import HierarchicalConfigError, validate_hierarchical_weights
from tests.conftest import db_skip_reason
from tests.test_agent_e2e import AREA_ID, PROJECT_ID, SEED_WEIGHTS, UNIT_IDS, _insert_dataset

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

VALID_HIERARCHICAL_WEIGHTS = {
    "market": {
        "market_interest_rate": {"weight": 1.0, "direction": "negative", "missing_value_policy": "neutral"},
    },
    "project": {
        "expert_location_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"},
    },
    "area": {
        # Deliberately an EXPERT key, not a CRM key: `_insert_dataset()`
        # (shared with `tests/test_agent_e2e.py`) always seeds live deals for
        # `AREA_ID`, so a CRM key (`area_velocity_norm`/`area_conversion_norm`)
        # would make Area genuinely eligible in EVERY test using this base
        # fixture (PR-5's whole point — CRM-only Area IS a valid eligible
        # state). Tests below that are not specifically testing Area's CRM
        # path want their pre-PR-5 "Area stays excluded, nothing published"
        # baseline back — using an expert key (never published unless a test
        # explicitly does so) restores exactly that, without touching the
        # shared fixture.
        "area_accessibility": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"},
    },
    "grain_weights": {
        "market": {"weight": 0.10, "missing_value_policy": "skip"},
        "project": {"weight": 0.25, "missing_value_policy": "skip"},
        "area": {"weight": 0.25, "missing_value_policy": "skip"},
        "unit": {"weight": 0.40, "missing_value_policy": "skip"},
    },
}

LEGACY_SCORES = {"u1": "0.5900", "u2": "0.8400", "u3": "0.5900", "u4": "0.2400"}


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
async def factory(truncate_all, monkeypatch):
    session_factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    monkeypatch.setattr("src.ranking.service.get_session_factory", lambda: session_factory, raising=False)
    return session_factory


async def _run_with(factory, hierarchical_weights: dict | None):
    await _insert_config(factory, hierarchical_weights=hierarchical_weights)
    await _insert_dataset(factory)
    return await run_ranking(PROJECT_ID, session_factory=factory)


async def _score_row(factory, unit_id):
    async with factory() as session:
        return (
            await session.execute(sa.select(ranking_scores).where(ranking_scores.c.unit_id == unit_id))
        ).mappings().first()


# --- Config isolation (T19) --------------------------------------------------


async def test_missing_hierarchical_weights_is_a_no_op_legacy_still_succeeds(factory):
    result = await _run_with(factory, hierarchical_weights=None)
    hr = await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    assert hr.hierarchical_weights_present is False
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["score"] == Decimal("0.5900")
    assert row["hierarchical_score"] is None
    assert row["hierarchical_contributions"] is None


async def test_malformed_hierarchical_weights_raises_before_any_hierarchical_write(factory):
    bad = copy.deepcopy(VALID_HIERARCHICAL_WEIGHTS)
    bad["grain_weights"]["unit"]["weight"] = 0.10  # total now 0.70, not 1.0

    result = await _run_with(factory, hierarchical_weights=bad)
    with pytest.raises(HierarchicalConfigError):
        await compute_hierarchical_scores_for_run(
            PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
        )

    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["score"] == Decimal("0.5900"), "legacy ranking result is unaffected by a malformed hierarchical config"
    assert row["hierarchical_score"] is None


def test_nested_shape_in_legacy_weights_is_rejected_by_existing_validate_weights():
    """Documents the observed compatibility reason for D41: nested data was
    never made to reach legacy `validate_weights()` in this code, and if it
    were handed there directly, it is already rejected on unregistered keys."""
    from src.services.ranking_config import ConfigError, validate_weights

    with pytest.raises(ConfigError) as exc:
        validate_weights(VALID_HIERARCHICAL_WEIGHTS)
    assert exc.value.code == "UNKNOWN_FEATURE"


def test_nested_unit_block_is_rejected_by_the_hierarchical_validator():
    bad = copy.deepcopy(VALID_HIERARCHICAL_WEIGHTS)
    bad["unit"] = {"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}}
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_WEIGHTS_UNIT_BLOCK_FORBIDDEN"


# --- D37 behavior: unit-only (PR-1 has no Market/Project/Area source) --------


async def test_unit_only_hierarchical_score_equals_legacy_score_exactly(factory):
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    hr = await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    assert hr.written == 5
    assert hr.attempted == 5
    assert hr.no_op_absent == 0

    for key, expected in LEGACY_SCORES.items():
        row = await _score_row(factory, UNIT_IDS[key])
        assert row["hierarchical_score"] == Decimal(expected)
        assert row["hierarchical_score"] == row["score"], "unit-only: hierarchical_score == legacy score exactly"

        c = row["hierarchical_contributions"]
        assert c["score_mode"] == "unit_only"
        assert c["eligible_grains"] == []
        # `excluded_grains` values are `{"reason": ...}` objects. No Market or
        # Project value has been published for this project in this suite
        # (no governance rows seeded here), so both grains reach the same
        # "nothing published" reason PR-3/PR-4 introduced; Area stays
        # excluded with the PR-5-not-implemented placeholder.
        assert c["excluded_grains"] == {
            "market": {"reason": "NO_PUBLISHED_MARKET_VALUE"},
            "project": {"reason": "NO_PUBLISHED_PROJECT_VALUE"},
            "area": {"reason": "NO_PUBLISHED_AREA_EXPERT_VALUE"},
        }
        assert c["top_level_weight_coverage"] == "0.4"
        assert c["effective_grain_weights"] == {"unit": "1.000000"}
        assert c["configured_grain_weights"] == {"market": 0.10, "project": 0.25, "area": 0.25, "unit": 0.40}
        assert c["disclosure"] == "Unit-only hierarchical score — Market, Project, and Area context unavailable."
        assert c["legal_gate"] == {"status": None, "gated": False}
        assert c["cutoff_at"]
        assert c["grains"]["market"] == {
            "eligible": False, "score": None, "coverage": None, "exclusion_reason": "NO_PUBLISHED_MARKET_VALUE"
        }
        assert "snapshot_id" not in c["grains"]["market"], "no snapshot fields for a never-eligible grain"
        assert c["grains"]["project"]["eligible"] is False
        assert c["grains"]["project"]["exclusion_reason"] == "NO_PUBLISHED_PROJECT_VALUE"
        assert "snapshot_id" not in c["grains"]["project"], "no snapshot fields for a never-eligible grain"
        assert c["grains"]["unit"]["eligible"] is True
        assert c["grains"]["unit"]["exclusion_reason"] is None


async def test_configured_grain_weights_are_never_mutated(factory):
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    async with factory() as session:
        stored = (
            await session.execute(
                sa.select(ranking_configs.c.hierarchical_weights).where(
                    ranking_configs.c.id == result.config_version_id
                )
            )
        ).scalar_one()
    assert stored["grain_weights"] == VALID_HIERARCHICAL_WEIGHTS["grain_weights"]


async def test_effective_weights_sum_to_one_for_scored_non_gated_output(factory):
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    total = sum(Decimal(v) for v in row["hierarchical_contributions"]["effective_grain_weights"].values())
    assert total == Decimal("1.000000")


# --- Safety -------------------------------------------------------------------


async def test_no_legacy_row_is_a_safe_logged_no_op_not_a_crash(factory):
    """D37/§24.4.1: a unit whose legacy `U` is absent has NO `ranking_scores`
    row for this run at all — simulated here by deleting one unit's row after
    a normal run, the same end-state a coverage-skip produces via
    `_persist_scores()`'s own `to_insert` filter."""
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    async with factory() as session:
        await session.execute(sa.delete(ranking_scores).where(ranking_scores.c.unit_id == UNIT_IDS["u1"]))
        await session.commit()

    hr = await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    assert hr.no_op_absent == 1
    assert hr.attempted == 4
    assert hr.written == 4
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row is None


async def test_hierarchical_step_writes_only_the_two_new_columns(factory):
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    before = {
        key: {
            k: v
            for k, v in (await _score_row(factory, UNIT_IDS[key])).items()
            if k not in ("hierarchical_score", "hierarchical_contributions")
        }
        for key in UNIT_IDS
    }
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    for key in UNIT_IDS:
        after_row = await _score_row(factory, UNIT_IDS[key])
        after = {k: v for k, v in after_row.items() if k not in ("hierarchical_score", "hierarchical_contributions")}
        assert after == before[key], f"{key}: a legacy column changed — hierarchical step must touch only the two new ones"


async def test_repeated_invocation_is_deterministic_and_idempotent(factory):
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    first = await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    second = await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    assert (first.written, first.attempted) == (second.written, second.attempted) == (5, 5)
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.5900")


async def test_update_cannot_modify_a_different_runs_row(factory):
    """Outcome-level: computing hierarchical scores for a run_id that a LATER
    `run_ranking()` call has already superseded (delete-and-reinsert per
    project, `_persist_scores()`) must never touch the newer run's row —
    whether that surfaces as a 0-row UPDATE or as 'no legacy row found for
    this run_id' depends on exactly when the supersession happened relative to
    this call; both are safe. See the surgical test below for the narrower
    UPDATE-scoping mechanism itself, tested deterministically."""
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    stale_run_id = result.run_id

    second = await run_ranking(PROJECT_ID, session_factory=factory)
    assert second.run_id != stale_run_id

    hr = await compute_hierarchical_scores_for_run(
        PROJECT_ID, stale_run_id, result.config_version_id, session_factory=factory
    )
    assert hr.written == 0

    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["ranking_run_id"] == second.run_id
    assert row["hierarchical_score"] is None


async def test_update_scoped_by_both_unit_and_run_id_prevents_cross_run_write(factory):
    """T20's actual mechanism, tested directly rather than via true
    concurrency: a genuinely concurrent `run_ranking()` / hierarchical-step
    interleaving is not practical to make deterministic in this test
    infrastructure (both share one process/event loop in this suite) — this
    proves the SQL-level guarantee the production UPDATE relies on instead.
    Documented gap: a real concurrent-workers integration test is deferred."""
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    stale_run_id = result.run_id

    second = await run_ranking(PROJECT_ID, session_factory=factory)

    async with factory() as session:
        update = await session.execute(
            sa.update(ranking_scores)
            .where(ranking_scores.c.unit_id == UNIT_IDS["u1"], ranking_scores.c.ranking_run_id == stale_run_id)
            .values(hierarchical_score=Decimal("0.9999"), hierarchical_contributions={"poisoned": True})
        )
        await session.commit()
    assert update.rowcount == 0, "UPDATE scoped by (unit_id, ranking_run_id) must affect 0 rows once superseded"

    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["ranking_run_id"] == second.run_id
    assert row["hierarchical_score"] is None
    assert row["hierarchical_contributions"] is None


# --- Feature flag / run_ranking() integration --------------------------------


async def test_feature_flag_off_by_default_hierarchical_columns_stay_null(factory):
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] is None
    assert result.run_id is not None  # legacy run still completed normally


async def test_feature_flag_on_runs_the_hierarchical_step_automatically(factory, monkeypatch):
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("_S", (), {"hierarchical_ranking_enabled": True})(),
    )
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.5900")


async def test_hierarchical_step_failure_never_fails_the_already_completed_legacy_run(factory, monkeypatch):
    """Malformed hierarchical_weights + flag on: `run_ranking()` itself must
    still return its normal successful result and leave the run 'completed' —
    the hierarchical step's exception is caught and logged at the call site,
    never re-raised through `run_ranking()`."""
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("_S", (), {"hierarchical_ranking_enabled": True})(),
    )
    bad = copy.deepcopy(VALID_HIERARCHICAL_WEIGHTS)
    bad["grain_weights"]["unit"]["weight"] = 0.10  # sum != 1.0 -> HierarchicalConfigError

    result = await _run_with(factory, hierarchical_weights=bad)
    assert result.units_ranked == 5

    async with factory() as session:
        from src.models.tables import ranking_runs

        status = await session.scalar(
            sa.select(ranking_runs.c.status).where(ranking_runs.c.id == result.run_id)
        )
    assert status == "completed"

    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["score"] == Decimal("0.5900")
    assert row["hierarchical_score"] is None


async def test_legacy_fields_are_byte_identical_with_hierarchy_disabled_vs_enabled(factory, monkeypatch):
    """PR-1..PR-4 integration hardening, item 7: `score`/`rank_in_area`/
    `rank_in_project`/`weight_coverage`/`contributions` (legacy `band` is a
    pure function of `score` via `src/ranking/bands.py::band_for()`, not a
    stored column — byte-identical `score` subsumes it) must be identical
    whether the hierarchical post-run step ran or not, and whether Project/
    Market values are published or not. Same dataset/config, two separate
    `run_ranking()` calls — one with the flag off (default), one on, with a
    published Project value in between so the SECOND run actually exercises
    the full M/P/U hierarchical path, not just an absent-config no-op."""
    legacy_fields = ("score", "rank_in_area", "rank_in_project", "weight_coverage", "contributions")

    first = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    before = {key: {f: (await _score_row(factory, UNIT_IDS[key]))[f] for f in legacy_fields} for key in UNIT_IDS}
    assert (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_score"] is None, "flag off: no hierarchical write at all"

    await _publish_project_value_assertion(factory, normalized_value="0.80")
    await _publish_market_value_assertion(factory, normalized_value="0.60")

    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("_S", (), {"hierarchical_ranking_enabled": True})(),
    )
    second = await run_ranking(PROJECT_ID, session_factory=factory)
    assert second.run_id != first.run_id

    for key in UNIT_IDS:
        after = await _score_row(factory, UNIT_IDS[key])
        for field in legacy_fields:
            assert after[field] == before[key][field], (
                f"unit {key}: legacy field {field!r} changed ({before[key][field]!r} -> {after[field]!r}) "
                "between hierarchy-disabled and hierarchy-enabled runs of the SAME dataset"
            )
        # And the hierarchical step DID actually run and DID find M+P eligible
        # this time — proving the byte-identical legacy result above isn't
        # simply because the hierarchical step was a no-op both times.
        assert after["hierarchical_score"] is not None
        assert after["hierarchical_contributions"]["score_mode"] == "partial_hierarchical"


# --- Ordering / comparability -------------------------------------------------


async def test_hierarchical_step_never_changes_rank_in_project_or_rank_in_area(factory):
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    before = {key: dict(await _score_row(factory, UNIT_IDS[key])) for key in UNIT_IDS}
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    for key in UNIT_IDS:
        after = await _score_row(factory, UNIT_IDS[key])
        assert after["rank_in_project"] == before[key]["rank_in_project"]
        assert after["rank_in_area"] == before[key]["rank_in_area"]


async def test_comparability_warning_is_unset_when_every_unit_shares_the_same_eligibility(factory):
    """PR-1: Market/Project/Area are always excluded for every unit in a
    project (no source exists at all yet — §24.5), so every unit's
    eligible/excluded set is necessarily identical, and `comparability_warning`
    is always unset. The scenario this field exists to flag (two units in the
    same view with UNEQUAL eligibility) requires a real, per-unit-varying
    parent-grain source and is deferred to the PR that adds one."""
    result = await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    for key in LEGACY_SCORES:
        row = await _score_row(factory, UNIT_IDS[key])
        assert row["hierarchical_contributions"]["comparability_warning"] is None


# --- PR-3: Project grain end-to-end --------------------------------------------


async def _publish_project_value_assertion(
    factory,
    *,
    feature_key: str = "expert_location_score",
    normalized_value: str = "0.80",
    effective_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Full expert-draft -> evidence -> submit -> CEO-approve -> publish
    lifecycle, through the real `governance` service (not raw SQL) — this is
    exactly PR-2's shipped path, reused unmodified, feeding PR-3's
    materializer. Leaves the proposal at `status='published'`, which is what
    `build_project_feature_snapshot_for_run()` selects on."""
    from src.models.tables import ranking_feature_definitions
    from src.services import governance

    feature_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=feature_id,
                feature_key=feature_key,
                feature_version="v1",
                name="Expert location score",
                category="expert",
                grain="project",
                value_type="numeric",
                formula_id="expert_slider",
                normalization_method="identity",
                direction="positive",
                missing_policy="skip",
                status="active",
                definition_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    # `governance.py` uses its OWN `get_session_factory()` (the real, unpatched
    # one) — in production that's fine (it resolves to the same live DB this
    # test process points `DATABASE_URL`/`TEST_DATABASE_URL` at too), but this
    # test's fixture uses a scoped `factory` bound to `truncate_all`'s engine,
    # so route governance's calls through the SAME factory for this call.
    original_factory = governance.get_session_factory
    try:
        governance.get_session_factory = lambda: factory  # type: ignore[assignment]
        author = await governance.get_or_create_expert_profile(identity_subject="analyst@example.com")
        author_id = uuid.UUID(str(author["id"]))

        proposal = await governance.create_proposal(
            project_id=PROJECT_ID,
            created_by_expert_id=author_id,
            assertion_kind="value",
            scope_type="project",
        )
        justification = await governance.upsert_justification(
            proposal_id=uuid.UUID(str(proposal["id"])),
            feature_definition_id=feature_id,
            created_by_expert_id=author_id,
            assertion_kind="value",
            normalized_numeric=Decimal(normalized_value),
            rationale="Sales velocity has increased 20% QoQ per Q2 report.",
            methodology="Comparative analysis against 3 comparable projects.",
            evidence_summary="See attached Q2 2026 Market Analysis, page 4.",
            expected_effect="increase",
            confidence="medium",
            limitations="Single-quarter data, seasonal effect not isolated.",
            effective_at=effective_at,
            expires_at=expires_at,
            author_subject="analyst@example.com",
        )
        document = await governance.register_evidence_document(
            proposal_id=uuid.UUID(str(proposal["id"])),
            uploaded_by_expert_id=author_id,
            original_filename="q2-2026-market-analysis.pdf",
            mime_type="application/pdf",
            object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
            sha256_checksum="a" * 64,
            file_size_bytes=1024,
        )
        await governance.link_evidence_to_justification(
            document_id=uuid.UUID(str(document["id"])),
            feature_justification_id=uuid.UUID(str(justification["id"])),
        )
        await governance.submit_proposal(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=author_id)
        await governance.submit_review(
            proposal_id=uuid.UUID(str(proposal["id"])),
            decision="approved",
            comment="Approved — location premium confirmed.",
            reviewer_subject="ceo@example.com",
            reviewer_is_ceo=True,
        )
        published = await governance.mark_published(
            proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=author_id
        )
        assert published["status"] == "published"
    finally:
        governance.get_session_factory = original_factory


async def _publish_market_value_assertion(
    factory,
    *,
    feature_key: str = "market_interest_rate",
    normalized_value: str = "0.60",
    effective_at: datetime | None = None,
    expires_at: datetime | None = None,
    external_source_citation: str = "Central bank policy bulletin, Q2 2026",
) -> dict:
    """Same real-`governance`-service lifecycle as `_publish_project_value_assertion()`,
    scope_type='market'. `0040_market_grain_scope` seeds these four feature
    definitions in the real migrated schema, but this suite's `truncate_all`
    fixture (like every other DB-backed suite in this repo) truncates
    `ranking_feature_definitions` for test isolation — so, like
    `_publish_project_value_assertion()` does for its own feature, this
    inserts the definition itself (get-or-create by `feature_key`), matching
    `0040`'s own seed shape (30-day shelf life for `market_interest_rate`,
    90-day default for the other three) rather than assuming the migration's
    seed row survived truncation."""
    from src.models.tables import ranking_feature_definitions
    from src.services import governance

    max_shelf_life_days = {"market_interest_rate": 30}.get(feature_key, 90)
    direction = "negative" if feature_key == "market_interest_rate" else "positive"

    original_factory = governance.get_session_factory
    try:
        governance.get_session_factory = lambda: factory  # type: ignore[assignment]

        async with factory() as session:
            feature_id = await session.scalar(
                sa.select(ranking_feature_definitions.c.id).where(
                    ranking_feature_definitions.c.feature_key == feature_key
                )
            )
            if feature_id is None:
                feature_id = uuid.uuid4()
                now = datetime.now(UTC)
                await session.execute(
                    sa.insert(ranking_feature_definitions).values(
                        id=feature_id,
                        feature_key=feature_key,
                        feature_version="v1",
                        name=feature_key,
                        category="market",
                        grain="market",
                        value_type="numeric",
                        formula_id="expert_value_assertion",
                        normalization_method="identity",
                        direction=direction,
                        missing_policy="skip",
                        status="active",
                        definition_metadata={"max_shelf_life_days": max_shelf_life_days},
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.commit()
            else:
                await session.rollback()

        author = await governance.get_or_create_expert_profile(identity_subject="market-analyst@example.com")
        author_id = uuid.UUID(str(author["id"]))

        proposal = await governance.create_proposal(
            project_id=PROJECT_ID,
            created_by_expert_id=author_id,
            assertion_kind="value",
            scope_type="market",
        )
        justification = await governance.upsert_justification(
            proposal_id=uuid.UUID(str(proposal["id"])),
            feature_definition_id=feature_id,
            created_by_expert_id=author_id,
            assertion_kind="value",
            normalized_numeric=Decimal(normalized_value),
            rationale="Central bank held the policy rate steady this quarter.",
            methodology="Direct read of the published policy bulletin.",
            evidence_summary="See attached central bank bulletin, page 1.",
            expected_effect="increase",
            confidence="high",
            limitations="Single-source, not cross-verified against a second bulletin.",
            effective_at=effective_at or datetime.now(UTC),
            expires_at=expires_at,
            external_source_citation=external_source_citation,
            author_subject="market-analyst@example.com",
        )
        document = await governance.register_evidence_document(
            proposal_id=uuid.UUID(str(proposal["id"])),
            uploaded_by_expert_id=author_id,
            original_filename="central-bank-bulletin-q2-2026.pdf",
            mime_type="application/pdf",
            object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
            sha256_checksum="c" * 64,
            file_size_bytes=2048,
        )
        await governance.link_evidence_to_justification(
            document_id=uuid.UUID(str(document["id"])),
            feature_justification_id=uuid.UUID(str(justification["id"])),
        )
        await governance.submit_proposal(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=author_id)
        await governance.submit_review(
            proposal_id=uuid.UUID(str(proposal["id"])),
            decision="approved",
            comment="Approved — bulletin verified.",
            reviewer_subject="market-ceo@example.com",
            reviewer_is_ceo=True,
        )
        published = await governance.mark_published(
            proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=author_id
        )
        assert published["status"] == "published"
        return dict(justification)
    finally:
        governance.get_session_factory = original_factory


async def test_eligible_project_value_yields_partial_hierarchical_with_exact_decimal_composition(factory):
    # Seed project/dataset/config first (`_run_with`'s own `_insert_dataset`
    # call needs the project row to not already exist), THEN publish, THEN
    # run — a value's `published_at` (set to `now()` inside `mark_published()`)
    # must be <= the ranking run's `cutoff_at` (`ranking_runs.started_at`) to
    # be selected (§5.2's own predicate); publishing after a run has already
    # started correctly makes it invisible to THAT run (§3.2's exact
    # "approved-but-unpublished config is invisible to run_ranking() today"
    # precedent) — verified in `test_a_second_ranking_run_gets_its_own_pinned_snapshot_copy`.
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(factory, normalized_value="0.80")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    hr = await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    assert hr.written == 5

    # U (u1) = 0.5900 (LEGACY_SCORES), P = 0.80, W_P = 0.25, W_U = 0.40.
    # F = (0.25*0.80 + 0.40*0.59) / 0.65 = 0.436 / 0.65 = 0.67076923...
    # ROUND_HALF_UP to 4dp (engine.py's own rounding, unchanged) = 0.6708.
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.6708")
    assert row["hierarchical_score"] != row["score"], "partial composition must differ from legacy U alone"

    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "partial_hierarchical"
    assert c["eligible_grains"] == ["project"]
    assert c["excluded_grains"] == {
        "market": {"reason": "NO_PUBLISHED_MARKET_VALUE"},
        "area": {"reason": "NO_PUBLISHED_AREA_EXPERT_VALUE"},
    }
    assert c["grains"]["project"]["eligible"] is True
    assert c["grains"]["project"]["score"] == "0.8000"
    assert c["grains"]["project"]["coverage"] == "1.0"
    assert c["grains"]["project"]["snapshot_id"]
    assert len(c["grains"]["project"]["feature_value_ids"]) == 1
    assert len(c["grains"]["project"]["feature_justification_ids"]) == 1
    total_effective = sum(Decimal(v) for v in c["effective_grain_weights"].values())
    assert total_effective == Decimal("1.000000")


async def test_repeated_run_reuses_the_same_materialized_value_no_duplicate(factory):
    """Idempotency at the value level: calling the hierarchical step twice
    for the SAME run must not insert a second `ranking_feature_values` row —
    `build_project_feature_snapshot_for_run()`'s get-or-create must find the
    existing snapshot and read its pinned values back, never re-select from
    governance or re-materialize."""
    from src.models.tables import ranking_feature_values

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(factory)
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    async with factory() as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(ranking_feature_values))
        await session.rollback()
    assert count == 1

    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.6708")


async def test_a_second_ranking_run_gets_its_own_pinned_snapshot_copy(factory):
    """§5.6/§5.2: immutable per-run snapshots are COPIES, not a live view — a
    second `run_ranking()` call for the same project must produce its OWN
    `ranking_feature_snapshots`/`ranking_feature_values` rows, not reuse the
    first run's, even though both select the SAME published assertion."""
    from src.models.tables import ranking_feature_snapshots, ranking_feature_values

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(factory)

    # Two SEPARATE runs, both AFTER publish (so both are eligible) — `run_ranking`
    # itself is re-runnable (delete-and-reinsert `ranking_scores`/`feature_snapshots`
    # per project each call), no re-seeding needed.
    first = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, first.run_id, first.config_version_id, session_factory=factory
    )
    second = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, second.run_id, second.config_version_id, session_factory=factory
    )

    async with factory() as session:
        # Scoped to scope_type='project' — each run ALSO gets its own Market
        # snapshot row (empty/insufficient_data here, no Market assertion was
        # published in this test), which would otherwise double-count.
        snapshot_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ranking_feature_snapshots)
            .where(
                ranking_feature_snapshots.c.project_id == PROJECT_ID,
                ranking_feature_snapshots.c.scope_type == "project",
            )
        )
        value_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ranking_feature_values)
            .where(ranking_feature_values.c.scope_type == "project")
        )
        await session.rollback()
    assert snapshot_count == 2, "each run gets its own Project snapshot row, keyed by ranking_run_id"
    assert value_count == 2, "each run's snapshot pins its own copy of the value, not a shared reference"

    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.6708"), "replay is stable across repeated runs"


async def test_no_eligible_value_for_a_different_feature_key_stays_unit_only(factory):
    """The Project block only configures `expert_location_score` — publishing
    a value for a DIFFERENT feature key must not make Project eligible."""
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(factory, feature_key="expert_infrastructure_score")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["score_mode"] == "unit_only"


async def test_future_effective_project_value_is_excluded(factory):
    """Same cutoff predicate Market uses (§5.2) — Project's `effective_at`,
    when explicitly set in the future, must exclude it too (project doesn't
    require `effective_at`, but when supplied it is still enforced)."""
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(
        factory, effective_at=datetime.now(UTC) + timedelta(days=5)
    )
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["excluded_grains"]["project"] == {
        "reason": "PROJECT_VALUE_NOT_EFFECTIVE"
    }


async def test_project_value_with_explicit_past_expiry_is_excluded(factory):
    """Project has no shelf-life *policy* (unlike Market's 30/90-day
    default) — but an assertion's OWN explicit `expires_at`, if supplied,
    is still honored: never falls back to 'ignore expiry' just because
    Project has no metadata-derived ceiling."""
    now = datetime.now(UTC)
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(
        factory, effective_at=now - timedelta(days=10), expires_at=now - timedelta(days=1)
    )
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["excluded_grains"]["project"] == {"reason": "PROJECT_VALUE_EXPIRED"}


async def test_project_snapshot_replay_is_idempotent_and_unaffected_by_later_publish(factory):
    """§5.6, Project-grain equivalent of the Market replay test below: a
    run's pinned Project snapshot must be unaffected by a NEWER value
    assertion approved/published after it was built."""
    from src.models.tables import ranking_feature_values

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(factory, feature_key="expert_location_score", normalized_value="0.80")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    first_score = (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_score"]

    # A second, later Project assertion for a DIFFERENT feature is published
    # after this run's snapshot was already built — must not alter it.
    await _publish_project_value_assertion(
        factory, feature_key="expert_infrastructure_score", normalized_value="0.20"
    )
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    second_score = (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_score"]
    assert first_score == second_score

    async with factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ranking_feature_values)
            .where(ranking_feature_values.c.scope_type == "project")
        )
        await session.rollback()
    assert count == 1, "the later assertion was never materialized into this run's already-built snapshot"


# --- PR-3: materializer defense-in-depth (direct calls, bypassing the ------
# snapshot builder's own SQL filter, to prove the function itself refuses --
# what a caller who skipped that filter might otherwise pass it) -----------


async def test_materializer_rejects_a_market_scope_assertion_even_if_called_directly(factory):
    """`_select_eligible_project_justifications()` already filters
    `scope_type='project'` — no market/area candidate is ever produced. This
    proves `materialize_published_feature_value()` ALSO refuses one itself,
    if ever called out-of-band with a non-project justification id."""
    from src.models.tables import ranking_feature_definitions
    from src.ranking.service import RankingError, materialize_published_feature_value
    from src.services import governance

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)

    feature_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=feature_id,
                feature_key="market_interest_rate",
                feature_version="v1",
                name="Interest rate",
                category="market",
                grain="market",
                value_type="numeric",
                formula_id="f",
                normalization_method="identity",
                direction="negative",
                missing_policy="skip",
                status="active",
                definition_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    original_factory = governance.get_session_factory
    try:
        governance.get_session_factory = lambda: factory  # type: ignore[assignment]
        author = await governance.get_or_create_expert_profile(identity_subject="analyst-market@example.com")
        author_id = uuid.UUID(str(author["id"]))
        proposal = await governance.create_proposal(
            project_id=PROJECT_ID, created_by_expert_id=author_id, assertion_kind="value", scope_type="market"
        )
        justification = await governance.upsert_justification(
            proposal_id=uuid.UUID(str(proposal["id"])),
            feature_definition_id=feature_id,
            created_by_expert_id=author_id,
            assertion_kind="value",
            normalized_numeric=Decimal("0.5"),
            rationale="r", methodology="m", evidence_summary="e",
            expected_effect="increase", confidence="medium", limitations="l",
            effective_at=now, external_source_citation="Central bank bulletin Q2 2026",
            author_subject="analyst-market@example.com",
        )
        document = await governance.register_evidence_document(
            proposal_id=uuid.UUID(str(proposal["id"])),
            uploaded_by_expert_id=author_id,
            original_filename="rate.pdf",
            mime_type="application/pdf",
            object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
            sha256_checksum="b" * 64,
            file_size_bytes=512,
        )
        await governance.link_evidence_to_justification(
            document_id=uuid.UUID(str(document["id"])), feature_justification_id=uuid.UUID(str(justification["id"]))
        )
        await governance.submit_proposal(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=author_id)
        await governance.submit_review(
            proposal_id=uuid.UUID(str(proposal["id"])),
            decision="approved", comment="Approved.",
            reviewer_subject="ceo-market@example.com", reviewer_is_ceo=True,
        )
        await governance.mark_published(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=author_id)
    finally:
        governance.get_session_factory = original_factory

    async with factory() as session:
        with pytest.raises(RankingError) as exc:
            await materialize_published_feature_value(
                feature_justification_id=uuid.UUID(str(justification["id"])),
                ranking_run_id=uuid.uuid4(),
                project_id=PROJECT_ID,
                snapshot_id=uuid.uuid4(),
                cutoff_at=datetime.now(UTC),
                session=session,
            )
        await session.rollback()
    assert exc.value.code == "UNEXPECTED_SCOPE_TYPE"


async def test_materializer_rejects_a_non_approved_assertion(factory):
    """Re-verification at materialization time, independent of whatever the
    snapshot builder's own SQL selection already filtered on — a draft
    (never submitted/approved/published) justification id must be refused."""
    from src.models.tables import ranking_feature_definitions
    from src.ranking.service import materialize_published_feature_value
    from src.services import governance

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    feature_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=feature_id,
                feature_key="expert_location_score",
                feature_version="v1",
                name="Location",
                category="expert",
                grain="project",
                value_type="numeric",
                formula_id="f",
                normalization_method="identity",
                direction="positive",
                missing_policy="skip",
                status="active",
                definition_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    original_factory = governance.get_session_factory
    try:
        governance.get_session_factory = lambda: factory  # type: ignore[assignment]
        author = await governance.get_or_create_expert_profile(identity_subject="analyst-draft@example.com")
        author_id = uuid.UUID(str(author["id"]))
        proposal = await governance.create_proposal(
            project_id=PROJECT_ID, created_by_expert_id=author_id, assertion_kind="value", scope_type="project"
        )
        justification = await governance.upsert_justification(
            proposal_id=uuid.UUID(str(proposal["id"])),
            feature_definition_id=feature_id,
            created_by_expert_id=author_id,
            assertion_kind="value",
            normalized_numeric=Decimal("0.5"),
            rationale="r", methodology="m", evidence_summary="e",
            expected_effect="increase", confidence="medium", limitations="l",
            author_subject="analyst-draft@example.com",
        )
        # Never submitted/reviewed/published — still `draft`.
    finally:
        governance.get_session_factory = original_factory

    async with factory() as session:
        with pytest.raises(governance.GovernanceError) as exc:
            await materialize_published_feature_value(
                feature_justification_id=uuid.UUID(str(justification["id"])),
                ranking_run_id=uuid.uuid4(),
                project_id=PROJECT_ID,
                snapshot_id=uuid.uuid4(),
                cutoff_at=datetime.now(UTC),
                session=session,
            )
        await session.rollback()
    assert exc.value.code == "NOT_APPROVED"


# --- PR-4: Market grain end-to-end ----------------------------------------------


async def test_eligible_market_value_composes_with_project_u_m_p(factory):
    """U + M + P, all three eligible — exact Decimal composition, D37 partial
    composition over three of four grains."""
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(factory, normalized_value="0.80")
    await _publish_market_value_assertion(factory, normalized_value="0.60")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )

    # VALID_HIERARCHICAL_WEIGHTS' market block orients `market_interest_rate`
    # as "negative" (a higher rate is worse) — M = oriented(0.60, "negative")
    # = 1 - 0.60 = 0.40, computed by `engine.score_unit()` unchanged, same as
    # every other grain call.
    # U=0.5900, P=0.80, M=0.40, W_U=0.40, W_P=0.25, W_M=0.10.
    # F = (0.10*0.40 + 0.25*0.80 + 0.40*0.59) / 0.75
    #   = (0.04 + 0.20 + 0.236) / 0.75 = 0.476 / 0.75 = 0.63466666...
    # ROUND_HALF_UP to 4dp = 0.6347.
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.6347")

    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "partial_hierarchical"
    assert set(c["eligible_grains"]) == {"market", "project"}
    assert c["excluded_grains"] == {"area": {"reason": "NO_PUBLISHED_AREA_EXPERT_VALUE"}}
    assert c["grains"]["market"]["eligible"] is True
    assert c["grains"]["market"]["score"] == "0.4000"
    assert c["grains"]["market"]["snapshot_id"]
    assert len(c["grains"]["market"]["feature_value_ids"]) == 1
    # 0.10/0.75 and 0.40/0.75 are repeating thirds — each term is
    # independently quantized to 6dp (pre-existing PR-1 disclosure-only
    # rounding, not a scoring bug: the actual `hierarchical_score` above is
    # exact), so the disclosed sum can land a single unit of the last
    # decimal place away from 1 for this particular three-grain weight
    # split. `test_effective_weights_sum_to_one_for_scored_non_gated_output`
    # (PR-1) already covers the exact-sum case with a weight split that
    # doesn't hit repeating thirds.
    total_effective = sum(Decimal(v) for v in c["effective_grain_weights"].values())
    assert abs(total_effective - Decimal("1")) <= Decimal("0.000001")


async def test_eligible_market_value_alone_composes_u_plus_m(factory):
    """U + M only (no Project value published) — still `partial_hierarchical`."""
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(factory, normalized_value="0.60")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )

    # M = oriented(0.60, "negative") = 0.40 (same orientation as the combined
    # test above). U=0.5900, M=0.40, W_U=0.40, W_M=0.10.
    # F = (0.10*0.40 + 0.40*0.59) / 0.50 = (0.04 + 0.236) / 0.50 = 0.552.
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.5520")
    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "partial_hierarchical"
    assert c["eligible_grains"] == ["market"]
    assert c["excluded_grains"]["project"] == {"reason": "NO_PUBLISHED_PROJECT_VALUE"}


async def test_market_value_expired_via_explicit_expires_at_is_excluded(factory):
    """An assertion whose OWN `expires_at` has already passed at cutoff is
    excluded — never zero-filled, and the run stays `unit_only` if nothing
    else is eligible."""
    now = datetime.now(UTC)
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(
        factory,
        normalized_value="0.60",
        effective_at=now - timedelta(days=10),
        expires_at=now - timedelta(days=1),
    )
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "unit_only"
    assert c["excluded_grains"]["market"] == {"reason": "MARKET_VALUE_EXPIRED"}


async def test_market_interest_rate_expires_after_30_days_via_implicit_shelf_life(factory):
    """No explicit `expires_at` on the justification — `market_interest_rate`'s
    definition_metadata (`0040`'s seed, 30-day shelf life) derives it. 31 days
    after `effective_at`, the assertion must be excluded as expired, never
    treated as 'still fresh' by falling back to 'never expires' (that
    fallback is Project's rule, not Market's)."""
    now = datetime.now(UTC)
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(
        factory,
        feature_key="market_interest_rate",
        normalized_value="0.60",
        effective_at=now - timedelta(days=31),
        expires_at=None,
    )
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["excluded_grains"]["market"] == {"reason": "MARKET_VALUE_EXPIRED"}


async def test_market_interest_rate_still_fresh_at_29_days_via_implicit_shelf_life(factory):
    """Same derivation, one day inside the 30-day ceiling — still eligible."""
    now = datetime.now(UTC)
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(
        factory,
        feature_key="market_interest_rate",
        normalized_value="0.60",
        effective_at=now - timedelta(days=29),
        expires_at=None,
    )
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "partial_hierarchical"
    assert c["grains"]["market"]["eligible"] is True


async def test_market_credit_policy_uses_the_90_day_default_shelf_life(factory):
    """`market_credit_policy` has no per-feature override in `_MARKET_MAX_SHELF_LIFE_DAYS`
    (only `market_interest_rate` does) — falls back to the 90-day default
    seeded in its own `definition_metadata`, not interest rate's 30. Uses its
    own hierarchical config (VALID_HIERARCHICAL_WEIGHTS only scores
    `market_interest_rate`) so this stays isolated from the other
    composition tests' arithmetic."""
    now = datetime.now(UTC)
    credit_policy_weights = copy.deepcopy(VALID_HIERARCHICAL_WEIGHTS)
    credit_policy_weights["market"] = {
        "market_credit_policy": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"},
    }
    await _run_with(factory, hierarchical_weights=credit_policy_weights)
    await _publish_market_value_assertion(
        factory,
        feature_key="market_credit_policy",
        normalized_value="0.70",
        effective_at=now - timedelta(days=60),
        expires_at=None,
    )
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_contributions"]["grains"]["market"]["eligible"] is True
    assert row["hierarchical_contributions"]["grains"]["market"]["score"] == "0.7000"


async def test_future_effective_market_value_is_excluded(factory):
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(
        factory, normalized_value="0.60", effective_at=datetime.now(UTC) + timedelta(days=5)
    )
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["excluded_grains"]["market"] == {"reason": "MARKET_VALUE_NOT_EFFECTIVE"}


async def test_market_value_published_after_cutoff_is_excluded_from_that_run(factory):
    """Same ordering property PR-3 already proved for Project — publishing
    after a run's cutoff makes the value invisible to THAT run."""
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await _publish_market_value_assertion(factory, normalized_value="0.60")
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["score_mode"] == "unit_only"


async def test_market_snapshot_replay_is_idempotent_and_unaffected_by_later_publish(factory):
    """§5.6: a run's pinned Market snapshot is unaffected by a NEWER value
    assertion approved/published after it was built."""
    from src.models.tables import ranking_feature_values

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(factory, feature_key="market_liquidity", normalized_value="0.55")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    first_score = (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_score"]

    # A second, later Market assertion for a DIFFERENT feature is published —
    # must not alter the already-built snapshot/score for this same run.
    await _publish_market_value_assertion(factory, feature_key="market_demand", normalized_value="0.90")
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    second_score = (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_score"]
    assert first_score == second_score

    async with factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ranking_feature_values)
            .where(ranking_feature_values.c.scope_type == "market")
        )
        await session.rollback()
    assert count == 1, "the later assertion was never materialized into this run's already-built snapshot"


async def test_comparability_warning_stays_unset_with_market_eligible_for_every_unit(factory):
    """Same reasoning as PR-1's own equivalent test — Market, like Project, is
    project-level, not unit-level, so every unit in the project necessarily
    shares the same eligibility set; `comparability_warning` stays unset."""
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(factory, normalized_value="0.60")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    for key in LEGACY_SCORES:
        row = await _score_row(factory, UNIT_IDS[key])
        assert row["hierarchical_contributions"]["comparability_warning"] is None


async def test_market_materializer_rejects_a_project_scope_assertion(factory):
    """The generalized `materialize_published_feature_value(expected_scope_type=...)`
    still refuses a scope mismatch — a Project-scope justification id can
    never be materialized as Market."""
    from src.models.tables import ranking_feature_justifications
    from src.ranking.service import RankingError, materialize_published_feature_value

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(factory)

    async with factory() as session:
        justification_id = await session.scalar(
            sa.select(ranking_feature_justifications.c.id).where(
                ranking_feature_justifications.c.assertion_kind == "value"
            )
        )
        await session.rollback()
    assert justification_id is not None

    async with factory() as session:
        with pytest.raises(RankingError) as exc:
            await materialize_published_feature_value(
                feature_justification_id=justification_id,
                ranking_run_id=uuid.uuid4(),
                project_id=PROJECT_ID,
                snapshot_id=uuid.uuid4(),
                cutoff_at=datetime.now(UTC),
                session=session,
                expected_scope_type="market",
            )
        await session.rollback()
    assert exc.value.code == "UNEXPECTED_SCOPE_TYPE"


# --- PR-5: Area grain end-to-end ------------------------------------------------

# CRM-only Area config — `VALID_HIERARCHICAL_WEIGHTS`'s own default "area"
# block deliberately uses an EXPERT key (see its own comment) so every
# Market/Project-focused test above stays area-excluded without any Area
# assertion ever being published; these two variants opt IN to the CRM path
# explicitly, one test at a time, rather than changing the shared default.
CRM_ONLY_AREA_WEIGHTS = copy.deepcopy(VALID_HIERARCHICAL_WEIGHTS)
CRM_ONLY_AREA_WEIGHTS["area"] = {
    "area_velocity_norm": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"},
}

CRM_AND_EXPERT_AREA_WEIGHTS = copy.deepcopy(VALID_HIERARCHICAL_WEIGHTS)
CRM_AND_EXPERT_AREA_WEIGHTS["area"] = {
    "area_velocity_norm": {"weight": 0.5, "direction": "positive", "missing_value_policy": "skip"},
    "area_accessibility": {"weight": 0.5, "direction": "positive", "missing_value_policy": "skip"},
}


async def _publish_area_value_assertion(
    factory,
    *,
    area_id=None,
    feature_key: str = "area_accessibility",
    normalized_value: str = "0.70",
    effective_at: datetime | None = None,
    expires_at: datetime | None = None,
    author_subject: str = "analyst-area@example.com",
) -> dict:
    """Same real-`governance`-service lifecycle as
    `_publish_project_value_assertion()`/`_publish_market_value_assertion()`,
    scope_type='area'. Get-or-create the feature definition by `feature_key`
    (like Market's helper — `truncate_all` wipes `ranking_feature_definitions`
    every test, and some tests below publish twice, under different keys, in
    the same test)."""
    from src.models.tables import ranking_feature_definitions
    from src.services import governance

    area_id = area_id if area_id is not None else AREA_ID

    original_factory = governance.get_session_factory
    try:
        governance.get_session_factory = lambda: factory  # type: ignore[assignment]

        async with factory() as session:
            feature_id = await session.scalar(
                sa.select(ranking_feature_definitions.c.id).where(
                    ranking_feature_definitions.c.feature_key == feature_key
                )
            )
            if feature_id is None:
                feature_id = uuid.uuid4()
                now = datetime.now(UTC)
                await session.execute(
                    sa.insert(ranking_feature_definitions).values(
                        id=feature_id,
                        feature_key=feature_key,
                        feature_version="v1",
                        name=feature_key,
                        category="area",
                        grain="area",
                        value_type="numeric",
                        formula_id="expert_value_assertion",
                        normalization_method="identity",
                        direction="positive",
                        missing_policy="skip",
                        status="active",
                        definition_metadata={},
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.commit()
            else:
                await session.rollback()

        author = await governance.get_or_create_expert_profile(identity_subject=author_subject)
        author_id = uuid.UUID(str(author["id"]))

        proposal = await governance.create_proposal(
            project_id=PROJECT_ID,
            created_by_expert_id=author_id,
            assertion_kind="value",
            scope_type="area",
            area_id=area_id,
        )
        justification = await governance.upsert_justification(
            proposal_id=uuid.UUID(str(proposal["id"])),
            feature_definition_id=feature_id,
            created_by_expert_id=author_id,
            assertion_kind="value",
            normalized_numeric=Decimal(normalized_value),
            rationale="Accessibility improved after new road opened.",
            methodology="On-site survey against 3 comparable areas.",
            evidence_summary="See attached accessibility survey, page 2.",
            expected_effect="increase",
            confidence="medium",
            limitations="Single-surveyor assessment.",
            effective_at=effective_at,
            expires_at=expires_at,
            author_subject=author_subject,
        )
        document = await governance.register_evidence_document(
            proposal_id=uuid.UUID(str(proposal["id"])),
            uploaded_by_expert_id=author_id,
            original_filename="area-accessibility-survey.pdf",
            mime_type="application/pdf",
            object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
            sha256_checksum="c" * 64,
            file_size_bytes=1024,
        )
        await governance.link_evidence_to_justification(
            document_id=uuid.UUID(str(document["id"])),
            feature_justification_id=uuid.UUID(str(justification["id"])),
        )
        await governance.submit_proposal(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=author_id)
        await governance.submit_review(
            proposal_id=uuid.UUID(str(proposal["id"])),
            decision="approved",
            comment="Approved — accessibility improvement confirmed.",
            reviewer_subject="ceo-area@example.com",
            reviewer_is_ceo=True,
        )
        published = await governance.mark_published(
            proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=author_id
        )
        assert published["status"] == "published"
        return {"proposal": proposal, "justification": justification}
    finally:
        governance.get_session_factory = original_factory


async def _insert_second_area_with_one_unit(factory, *, area_id: uuid.UUID, unit_id: uuid.UUID) -> None:
    """A second, independent area in the SAME project, with its own single
    unit and no deals at all (so it has neither CRM nor expert Area data
    unless a test explicitly publishes one) — used for the tests proving
    Area genuinely varies per area within one project/run."""
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(areas).values(
                id=area_id,
                project_id=PROJECT_ID,
                area_name="Tower B",
                unit_type="2PN",
                bedrooms=2,
                area_sqm=Decimal("60"),
                total_units=1,
                created_at=now,
                external_id=f"A-AGENT-TEST-{uuid.uuid4().hex[:8]}",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(units).values(
                id=unit_id,
                source_system="mini_crm",
                source_instance_id="test",
                external_unit_id=f"u-{uuid.uuid4().hex[:8]}",
                area_id=area_id,
                unit_code="B1",
                unit_type="2PN",
                status="available",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def test_crm_only_area_value_yields_partial_hierarchical_u_plus_a(factory):
    """`_insert_dataset()` already seeds live deals for `AREA_ID` (u3
    reserved, u4 sold within 30 days, 5 live units) — with a CRM-configured
    Area block and NO expert assertion ever published, Area must still be
    eligible: CRM-only IS a valid eligible state (PR-5's whole point), never
    'excluded because nobody published anything'."""
    await _run_with(factory, hierarchical_weights=CRM_ONLY_AREA_WEIGHTS)
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )

    # area_velocity_norm: sold_30d=1 (u4), inventory=5 (live units), saturation=0.20
    # -> min((1/5)/0.20, 1) = min(1.0, 1) = 1.0000 exactly.
    # A = 1.0000, W_A=0.25, U(u1)=0.5900, W_U=0.40.
    # F = (0.25*1.0 + 0.40*0.59) / 0.65 = (0.25 + 0.236) / 0.65 = 0.486/0.65
    #   = 0.747692... -> ROUND_HALF_UP 4dp = 0.7477.
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.7477")
    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "partial_hierarchical"
    assert c["eligible_grains"] == ["area"]
    assert c["grains"]["area"]["eligible"] is True
    assert c["grains"]["area"]["score"] == "1.0000"
    assert c["grains"]["area"]["crm_feature_keys"] == ["area_velocity_norm"]
    assert c["grains"]["area"]["expert_feature_keys"] == []
    assert c["grains"]["area"]["snapshot_id"] is None, "CRM-only: no Area expert snapshot content exists"
    assert c["grains"]["area"]["feature_value_ids"] == []


async def test_eligible_area_expert_value_composes_u_plus_a(factory):
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_area_value_assertion(factory, normalized_value="0.70")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )

    # A=0.70, W_A=0.25, U=0.59, W_U=0.40.
    # F = (0.25*0.70 + 0.40*0.59)/0.65 = (0.175+0.236)/0.65 = 0.411/0.65
    #   = 0.632307... -> 0.6323.
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.6323")
    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "partial_hierarchical"
    assert c["eligible_grains"] == ["area"]
    assert c["grains"]["area"]["score"] == "0.7000"
    assert c["grains"]["area"]["crm_feature_keys"] == []
    assert c["grains"]["area"]["expert_feature_keys"] == ["area_accessibility"]
    assert c["grains"]["area"]["snapshot_id"]
    assert len(c["grains"]["area"]["feature_value_ids"]) == 1
    assert len(c["grains"]["area"]["feature_justification_ids"]) == 1


async def test_missing_expert_area_value_does_not_erase_crm_only_area_score(factory):
    """CRM-only A (1.0000, no expert published) versus CRM+expert A (0.85,
    after publishing `area_accessibility=0.70`) — same config throughout,
    same weights: the two runs must produce DIFFERENT `A`, and the SECOND
    run must not be missing CRM's contribution just because expert data
    arrived (no-override merge, both keys resolve simultaneously)."""
    await _run_with(factory, hierarchical_weights=CRM_AND_EXPERT_AREA_WEIGHTS)
    first_result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, first_result.run_id, first_result.config_version_id, session_factory=factory
    )
    first_area_score = (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_contributions"]["grains"]["area"][
        "score"
    ]
    # velocity=1.0, weight 0.5, nothing else configured resolves -> A = 1.0/0.5*0.5 = 1.0.
    assert first_area_score == "1.0000"

    await _publish_area_value_assertion(factory, feature_key="area_accessibility", normalized_value="0.70")
    second_result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, second_result.run_id, second_result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    c = row["hierarchical_contributions"]
    # A = (0.5*1.0 + 0.5*0.70)/(0.5+0.5) = (0.5+0.35)/1.0 = 0.85.
    assert c["grains"]["area"]["score"] == "0.8500"
    assert c["grains"]["area"]["crm_feature_keys"] == ["area_velocity_norm"]
    assert c["grains"]["area"]["expert_feature_keys"] == ["area_accessibility"]
    assert c["grains"]["area"]["score"] != first_area_score


async def test_u_plus_project_plus_area_partial_composition(factory):
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_project_value_assertion(factory, normalized_value="0.80")
    await _publish_area_value_assertion(factory, normalized_value="0.70")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    # P=0.80,W_P=0.25; A=0.70,W_A=0.25; U=0.59,W_U=0.40. Sum=0.90.
    # F=(0.20+0.175+0.236)/0.90=0.611/0.90=0.678888... -> 0.6789.
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.6789")
    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "partial_hierarchical"
    assert set(c["eligible_grains"]) == {"project", "area"}
    assert c["excluded_grains"] == {"market": {"reason": "NO_PUBLISHED_MARKET_VALUE"}}


async def test_u_plus_market_plus_area_partial_composition(factory):
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(factory, normalized_value="0.60")
    await _publish_area_value_assertion(factory, normalized_value="0.70")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    # M=0.40 (oriented),W_M=0.10; A=0.70,W_A=0.25; U=0.59,W_U=0.40. Sum=0.75.
    # F=(0.04+0.175+0.236)/0.75=0.451/0.75=0.601333... -> 0.6013.
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.6013")
    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "partial_hierarchical"
    assert set(c["eligible_grains"]) == {"market", "area"}
    assert c["excluded_grains"] == {"project": {"reason": "NO_PUBLISHED_PROJECT_VALUE"}}


async def test_full_hierarchical_composition_u_plus_m_plus_p_plus_a(factory):
    """D37's `full_hierarchical` — all four grains eligible at once, the
    first time any PR reaches this score_mode."""
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_market_value_assertion(factory, normalized_value="0.60")
    await _publish_project_value_assertion(factory, normalized_value="0.80")
    await _publish_area_value_assertion(factory, normalized_value="0.70")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    # M=0.40,W_M=0.10; P=0.80,W_P=0.25; A=0.70,W_A=0.25; U=0.59,W_U=0.40. Sum=1.0.
    # F=0.04+0.20+0.175+0.236=0.651 -> exactly 0.6510 (no repeating decimal
    # since the weight split sums exactly to 1 here).
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == Decimal("0.6510")
    c = row["hierarchical_contributions"]
    assert c["score_mode"] == "full_hierarchical"
    assert set(c["eligible_grains"]) == {"market", "project", "area"}
    assert c["excluded_grains"] == {}
    total_effective = sum(Decimal(v) for v in c["effective_grain_weights"].values())
    assert total_effective == Decimal("1.000000")
    assert c["comparability_warning"] is None, "single area, every unit shares the same eligibility set"


async def test_duplicate_crm_expert_feature_key_is_a_hard_error_not_last_write_wins(factory):
    """Pure unit test of the merge helper itself — a duplicate key between
    CRM and expert maps must never be resolved by silently preferring one
    side; it is always a hard error (defense-in-depth backstop; the normal
    path can never produce this because `governance.upsert_justification()`
    already rejects an expert assertion for a CRM-owned key at authoring
    time — see `test_expert_cannot_assert_crm_owned_area_velocity_norm`)."""
    from src.ranking.service import DUPLICATE_CRM_EXPERT_FEATURE_KEY, RankingError, _merge_area_values

    with pytest.raises(RankingError) as exc:
        _merge_area_values({"area_velocity_norm": Decimal("0.5")}, {"area_velocity_norm": Decimal("0.7")})
    assert exc.value.code == DUPLICATE_CRM_EXPERT_FEATURE_KEY

    # No collision: distinct keys merge cleanly, both sides preserved.
    merged = _merge_area_values(
        {"area_velocity_norm": Decimal("0.5")}, {"area_accessibility": Decimal("0.7")}
    )
    assert merged == {"area_velocity_norm": Decimal("0.5"), "area_accessibility": Decimal("0.7")}


async def test_future_effective_area_value_is_excluded(factory):
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_area_value_assertion(factory, effective_at=datetime.now(UTC) + timedelta(days=5))
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["excluded_grains"]["area"] == {
        "reason": "AREA_EXPERT_VALUE_NOT_EFFECTIVE"
    }


async def test_area_value_with_explicit_past_expiry_is_excluded(factory):
    now = datetime.now(UTC)
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_area_value_assertion(
        factory, effective_at=now - timedelta(days=10), expires_at=now - timedelta(days=1)
    )
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["excluded_grains"]["area"] == {"reason": "AREA_EXPERT_VALUE_EXPIRED"}


async def test_area_value_published_after_cutoff_is_excluded_from_that_run(factory):
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await _publish_area_value_assertion(factory, normalized_value="0.70")
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    row = await _score_row(factory, UNIT_IDS["u1"])
    assert row["hierarchical_score"] == row["score"]
    assert row["hierarchical_contributions"]["score_mode"] == "unit_only"


async def test_area_snapshot_replay_is_idempotent_and_unaffected_by_later_publish(factory):
    from src.models.tables import ranking_feature_values

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_area_value_assertion(factory, feature_key="area_accessibility", normalized_value="0.70")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    first_score = (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_score"]

    # A second, later Area assertion for a DIFFERENT feature is published
    # after this run's snapshot was already built — must not alter it.
    await _publish_area_value_assertion(
        factory, feature_key="area_current_infrastructure", normalized_value="0.20"
    )
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    second_score = (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_score"]
    assert first_score == second_score

    async with factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ranking_feature_values)
            .where(ranking_feature_values.c.scope_type == "area")
        )
        await session.rollback()
    assert count == 1, "the later assertion was never materialized into this run's already-built snapshot"


async def test_repeated_invocation_reuses_the_same_area_snapshot_no_duplicate(factory):
    from src.models.tables import ranking_feature_snapshots

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_area_value_assertion(factory)
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    async with factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ranking_feature_snapshots)
            .where(ranking_feature_snapshots.c.scope_type == "area")
        )
        await session.rollback()
    assert count == 1


async def test_wrong_project_area_assertion_never_materializes(factory):
    """`copy_published_area_assertions_to_run_snapshot()` rejects/asserts the
    project-area relationship BEFORE writing anything — direct call, an
    `area_id` that does not belong to `project_id`."""
    from src.ranking.service import (
        AREA_SCOPE_PROJECT_MISMATCH,
        RankingError,
        copy_published_area_assertions_to_run_snapshot,
    )

    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    other_project_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        from src.models.tables import projects

        await session.execute(
            sa.insert(projects).values(
                id=other_project_id,
                name="Other",
                launch_date=now.date(),
                created_at=now,
                updated_at=now,
                absorption_calculator="legacy_aggregate",
                external_id=f"P-OTHER-{uuid.uuid4().hex[:8]}",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.commit()

    async with factory() as session:
        with pytest.raises(RankingError) as exc:
            await copy_published_area_assertions_to_run_snapshot(
                uuid.uuid4(), other_project_id, AREA_ID, datetime.now(UTC), session
            )
        await session.rollback()
    assert exc.value.code == AREA_SCOPE_PROJECT_MISMATCH


async def test_two_areas_in_the_same_project_have_independent_area_scores_and_trigger_comparability_warning(factory):
    """The core Area-specific behavior no other grain has: two areas in the
    SAME project/run may legitimately disagree on Area eligibility/score —
    unlike Market/Project (project-wide constants), and unlike every PR-1..4
    scenario (where `comparability_warning` could never actually fire within
    one run)."""
    area_2 = uuid.uuid4()
    unit_2 = uuid.uuid4()
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _insert_second_area_with_one_unit(factory, area_id=area_2, unit_id=unit_2)
    await _publish_area_value_assertion(factory, area_id=AREA_ID, normalized_value="0.90")
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )

    row_area1 = await _score_row(factory, UNIT_IDS["u1"])
    row_area2 = await _score_row(factory, unit_2)

    c1 = row_area1["hierarchical_contributions"]
    c2 = row_area2["hierarchical_contributions"]
    assert c1["grains"]["area"]["eligible"] is True
    assert c1["grains"]["area"]["score"] == "0.9000"
    assert c2["grains"]["area"]["eligible"] is False
    assert c2["grains"]["area"]["exclusion_reason"] == "NO_PUBLISHED_AREA_EXPERT_VALUE"

    # Eligibility diverges across areas in this run -> both units' rows must
    # disclose the comparability warning, not just the "different" one.
    assert c1["comparability_warning"] == (
        "Area eligibility/coverage differs across areas in this project's run — "
        "scores in different areas are not directly comparable (T18, §24.4.4)."
    )
    assert c2["comparability_warning"] == c1["comparability_warning"]


async def test_same_area_ordering_is_invariant_to_a_shared_area_score_change(factory):
    """T18 same-area invariance: u1 (U=0.59) and u2 (U=0.84) share ONE area —
    adding an Area value that is a CONSTANT for both must shift both by the
    same proportional amount and never reorder them relative to each other,
    exactly like Market/Project's existing same-project invariance."""
    await _run_with(factory, hierarchical_weights=VALID_HIERARCHICAL_WEIGHTS)
    await _publish_area_value_assertion(factory, normalized_value="0.10")  # a LOW area score
    result = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, result.run_id, result.config_version_id, session_factory=factory
    )
    u1 = (await _score_row(factory, UNIT_IDS["u1"]))["hierarchical_score"]
    u2 = (await _score_row(factory, UNIT_IDS["u2"]))["hierarchical_score"]
    # Legacy U already has u2 (0.84) > u1 (0.59); a shared, even a LOW, Area
    # score must not flip that within the same area.
    assert u2 > u1


# --- Legal gate (D27) — documented, deferred -----------------------------------


@pytest.mark.skip(
    reason=(
        "No legal-status source or seam exists anywhere in this repository yet "
        "(D27's HIGH_RISK gate) -- _legal_status_for_project() is a documented "
        "stub that always returns NOT_AVAILABLE; PR-1 ships no writer that could "
        "ever set HIGH_RISK. Real coverage is deferred to the PR that adds a "
        "legal-status source, per the owner instruction not to fabricate one here."
    )
)
async def test_high_risk_legal_status_yields_null_hierarchical_score_without_changing_legacy(factory):
    ...
