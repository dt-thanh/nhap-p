"""`POST /governance/evidence/upload` and `GET /governance/evidence` — the
real multipart upload route and the document-list route, both new (closing
the "no multipart upload route" gap recorded in `ranking_consultant.md` §21.1
and `pipeline_status.md`).

Same real-Postgres/`http`-fixture-with-ASGITransport style as
`tests/test_api/test_ranking_hierarchical.py` (this file's sibling).

D18 close-out (this pass): every actor-identity field (`uploaded_by_expert_id`,
`created_by_expert_id`, `actor_expert_id`, `reviewer_expert_id`) is no longer a
request field anywhere in this router — it is always derived from the
authenticated principal's verified OIDC `subject`. Static dashboard tokens
(`DASHBOARD_ADMIN_TOKEN` etc.) carry no `subject` (`DashboardPrincipal`'s own
docstring), so every write test here authenticates with a real, self-signed
test JWT instead (same technique as `tests/auth/test_ceo_authorization.py`) —
`VIEWER_HEADER`-style static tokens remain valid ONLY for read-only routes
that never call `_resolve_expert_id`.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, date, datetime

import jwt
import pytest
import pytest_asyncio
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.main import app
from src.models.tables import projects, ranking_feature_definitions
from src.services import governance, oidc, ranking_config
from tests.conftest import DASHBOARD_ADMIN_TOKEN, DASHBOARD_VIEWER_TOKEN, db_skip_reason

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

UPLOAD_API = "/api/v1/governance/evidence/upload"
LIST_API = "/api/v1/governance/evidence"
VIEWER_HEADER = {"Authorization": f"Bearer {DASHBOARD_VIEWER_TOKEN}"}

PROJECT_ID = uuid.uuid4()
PROJECT_EXTERNAL_ID = "P-GOV-UPLOAD-TEST"
PDF_BYTES = b"%PDF-1.4\n%mock pdf for a real API test\n%%EOF"

PUBLIC_ISS = "http://localhost:9090/realms/p100"
CLIENT = "absorbiq-client"
DISCOVERY = {
    "issuer": PUBLIC_ISS,
    "authorization_endpoint": f"{PUBLIC_ISS}/protocol/openid-connect/auth",
    "token_endpoint": f"{PUBLIC_ISS}/protocol/openid-connect/token",
    "jwks_uri": f"{PUBLIC_ISS}/protocol/openid-connect/certs",
    "end_session_endpoint": f"{PUBLIC_ISS}/protocol/openid-connect/logout",
}


@pytest.fixture
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest_asyncio.fixture
async def jwks(keypair, monkeypatch):
    """Same technique as `tests/auth/test_ceo_authorization.py`: a self-signed
    RSA keypair stands in for Keycloak's JWKS, so `authenticate_dashboard()`'s
    direct-JWT branch verifies real tokens without a live Keycloak."""
    from src.config import get_settings

    monkeypatch.setenv("OIDC_ISSUER", PUBLIC_ISS)
    monkeypatch.setenv("OIDC_INTERNAL_BASE_URL", "http://keycloak:8080/realms/p100")
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "local-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:8000/api/v1/auth/callback")
    monkeypatch.setenv("SESSION_SECRET", "x" * 40)
    monkeypatch.setenv(
        "OIDC_PROJECT_SCOPE",
        json.dumps({"CRM.Admin": "ALL", "CRM.SALES": "ALL", "CRM.CEO": "ALL", "CRM.Viewer": "ALL", "CRM.ADVISOR": [PROJECT_EXTERNAL_ID]}),
    )
    get_settings.cache_clear()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "get_discovery", lambda: DISCOVERY)

    _, public = keypair

    class _FakeSigningKey:
        key = public

    class _FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey()

    monkeypatch.setattr(oidc, "_jwk_client", lambda: _FakeJwkClient())
    yield keypair
    get_settings.cache_clear()
    oidc.reset_caches()


def _token(private, *, roles, subject):
    now = int(time.time())
    claims = {
        "iss": PUBLIC_ISS,
        "aud": CLIENT,
        "sub": subject,
        "preferred_username": f"{subject}@example.com",
        "realm_access": {"roles": list(roles)},
        "iat": now,
        "exp": now + 600,
    }
    return jwt.encode(claims, private, algorithm="RS256")


def _header(private, *, roles, subject) -> dict:
    return {"Authorization": f"Bearer {_token(private, roles=roles, subject=subject)}"}


def advisor_header(private, subject: str) -> dict:
    return _header(private, roles=["CRM.ADVISOR"], subject=subject)


@pytest_asyncio.fixture
async def http(truncate_all, monkeypatch, tmp_path, jwks):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    for target in (
        "src.services.governance.get_session_factory",
        "src.api.governance.get_session_factory",
        "src.services.ranking_config.get_session_factory",
        "src.services.evidence_extraction.get_session_factory",
    ):
        monkeypatch.setattr(target, lambda factory=factory: factory, raising=False)
    monkeypatch.setattr(
        "src.services.evidence_upload.get_settings",
        lambda: type("_S", (), {"upload_dir": str(tmp_path), "upload_max_size": 10 * 1024 * 1024})(),
    )
    monkeypatch.setattr(
        "src.api.governance.get_settings",
        lambda: type("_S", (), {"upload_dir": str(tmp_path)})(),
    )

    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(projects).values(
                id=PROJECT_ID,
                name="Governance Upload Test Project",
                launch_date=date(2026, 1, 1),
                created_at=now,
                updated_at=now,
                absorption_calculator="legacy_aggregate",
                external_id=PROJECT_EXTERNAL_ID,
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory  # type: ignore[attr-defined]
        client.tmp_upload_dir = tmp_path  # type: ignore[attr-defined]
        yield client


async def _expert_id(identity_subject: str) -> str:
    """Resolves the SAME expert id the server will resolve from a JWT bearing
    this `sub` — `get_or_create_expert_profile` is idempotent on
    `identity_subject`, so calling it directly here to learn the id for test
    assertions does not create a second, divergent profile."""
    row = await governance.get_or_create_expert_profile(identity_subject=identity_subject)
    return str(row["id"])


async def test_upload_stores_real_bytes_and_registers_metadata(http, jwks):
    private, _ = jwks
    response = await http.post(
        UPLOAD_API,
        files={"file": ("Q2 2026 Market Analysis.pdf", PDF_BYTES, "application/pdf")},
        data={"project_id": str(PROJECT_ID)},
        headers=advisor_header(private, "uploader-1"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reused"] is False
    assert body["mime_type"] == "application/pdf"
    assert body["file_size_bytes"] == len(PDF_BYTES)
    assert body["extraction_status"] == "not_requested"
    assert body["project_id"] == str(PROJECT_ID)
    assert body["uploaded_by_expert_id"] == await _expert_id("uploader-1")
    on_disk = http.tmp_upload_dir / body["object_storage_key"]
    assert on_disk.read_bytes() == PDF_BYTES


async def test_upload_rejects_non_pdf_signature_even_with_pdf_extension(http, jwks):
    private, _ = jwks
    response = await http.post(
        UPLOAD_API,
        files={"file": ("fake.pdf", b"not actually a pdf", "application/pdf")},
        data={"project_id": str(PROJECT_ID)},
        headers=advisor_header(private, "uploader-2"),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "FILE_SIGNATURE_MISMATCH"


async def test_upload_rejects_unsupported_extension(http, jwks):
    private, _ = jwks
    response = await http.post(
        UPLOAD_API,
        files={"file": ("spreadsheet.xlsx", b"whatever", "application/vnd.ms-excel")},
        data={"project_id": str(PROJECT_ID)},
        headers=advisor_header(private, "uploader-3"),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "UNSUPPORTED_FORMAT"


async def test_uploading_identical_bytes_twice_reuses_the_existing_document(http, jwks):
    private, _ = jwks
    headers = advisor_header(private, "uploader-4")
    first = await http.post(
        UPLOAD_API,
        files={"file": ("a.pdf", PDF_BYTES, "application/pdf")},
        data={"project_id": str(PROJECT_ID)},
        headers=headers,
    )
    second = await http.post(
        UPLOAD_API,
        files={"file": ("a-renamed-copy.pdf", PDF_BYTES, "application/pdf")},
        data={"project_id": str(PROJECT_ID)},
        headers=headers,
    )
    assert first.json()["reused"] is False
    assert second.json()["reused"] is True
    assert second.json()["id"] == first.json()["id"]
    # The second upload's bytes must not have been left orphaned on disk.
    on_disk_files = list((http.tmp_upload_dir / "governance" / "evidence").glob("*.pdf"))
    assert len(on_disk_files) == 1


async def test_upload_requires_advisor_role(http, jwks):
    private, _ = jwks
    response = await http.post(
        UPLOAD_API,
        files={"file": ("a.pdf", PDF_BYTES, "application/pdf")},
        data={"project_id": str(PROJECT_ID)},
        headers=_header(private, roles=["CRM.Viewer"], subject="viewer-1"),
    )
    assert response.status_code == 403


async def test_upload_requires_an_authenticated_identity_not_a_static_token(http):
    """D18: a static dashboard token (no `subject`) can still hold the
    `pipeline_operator`/`admin` ROLE, but it structurally cannot upload —
    there is no client-suppliable identity field left to fall back to."""
    response = await http.post(
        UPLOAD_API,
        files={"file": ("a.pdf", PDF_BYTES, "application/pdf")},
        data={"project_id": str(PROJECT_ID)},
        headers={"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


async def test_list_by_project_returns_directly_owned_and_proposal_linked_documents(http, jwks):
    private, _ = jwks
    headers = advisor_header(private, "lister-1")
    draft = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="base",
        created_by="test",
    )
    proposal = (
        await http.post(
            "/api/v1/governance/proposals",
            json={"project_id": str(PROJECT_ID), "base_config_id": str(draft["id"])},
            headers=headers,
        )
    ).json()
    await http.post(
        UPLOAD_API,
        files={"file": ("linked.pdf", PDF_BYTES, "application/pdf")},
        data={"project_id": str(PROJECT_ID), "proposal_id": proposal["id"]},
        headers=headers,
    )
    await http.post(
        UPLOAD_API,
        files={"file": ("standalone.pdf", PDF_BYTES + b"x", "application/pdf")},
        data={"project_id": str(PROJECT_ID)},
        headers=headers,
    )

    response = await http.get(LIST_API, params={"project_id": str(PROJECT_ID)}, headers=headers)
    assert response.status_code == 200
    names = {row["original_filename"] for row in response.json()}
    assert names == {"linked.pdf", "standalone.pdf"}


async def test_upload_requires_a_project_scope(http, jwks):
    private, _ = jwks
    response = await http.post(
        UPLOAD_API,
        files={"file": ("missing-project.pdf", PDF_BYTES, "application/pdf")},
        headers=advisor_header(private, "missing-project"),
    )
    assert response.status_code == 422


async def test_list_by_expert_includes_standalone_uploads(http, jwks):
    private, _ = jwks
    headers = advisor_header(private, "lister-2")
    await http.post(
        UPLOAD_API,
        files={"file": ("mine.pdf", PDF_BYTES, "application/pdf")},
        data={"project_id": str(PROJECT_ID)},
        headers=headers,
    )
    expert_id = await _expert_id("lister-2")
    response = await http.get(LIST_API, params={"uploaded_by_expert_id": expert_id}, headers=headers)
    assert response.status_code == 200
    assert [row["original_filename"] for row in response.json()] == ["mine.pdf"]


async def test_list_requires_exactly_one_scope_filter(http, jwks):
    private, _ = jwks
    headers = advisor_header(private, "scope-check")
    neither = await http.get(LIST_API, headers=headers)
    assert neither.status_code == 422
    assert neither.json()["detail"]["error_code"] == "SCOPE_REQUIRED"

    both = await http.get(
        LIST_API,
        params={"project_id": str(PROJECT_ID), "uploaded_by_expert_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert both.status_code == 422
    assert both.json()["detail"]["error_code"] == "SCOPE_REQUIRED"


async def test_list_by_project_requires_verified_advisor(http):
    response = await http.get(LIST_API, params={"project_id": str(PROJECT_ID)}, headers=VIEWER_HEADER)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


async def test_list_by_project_404s_on_unknown_project(http, jwks):
    private, _ = jwks
    response = await http.get(
        LIST_API, params={"project_id": str(uuid.uuid4())}, headers=advisor_header(private, "lister-3")
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "PROJECT_NOT_FOUND"


ASK_API = "/api/v1/governance/evidence/ask"
_VECTOR = [0.001] * 1536


async def test_ask_answers_with_citation_scoped_to_the_project(http, jwks, monkeypatch):
    from src.agents import advisory_tools
    from src.services import evidence_extraction, ranking_config

    private, _ = jwks
    headers = advisor_header(private, "asker-1")
    draft = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="base",
        created_by="test",
    )
    proposal = (
        await http.post(
            "/api/v1/governance/proposals",
            json={"project_id": str(PROJECT_ID), "base_config_id": str(draft["id"])},
            headers=headers,
        )
    ).json()
    upload = (
        await http.post(
            UPLOAD_API,
            files={"file": ("evidence.pdf", PDF_BYTES, "application/pdf")},
            data={"project_id": str(PROJECT_ID), "proposal_id": proposal["id"]},
            headers=headers,
        )
    ).json()
    monkeypatch.setattr(evidence_extraction, "embed_texts", lambda texts: [_VECTOR for _ in texts])
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        uuid.UUID(upload["id"]),
        [
            {
                "chunk_index": 0,
                "page_number": 2,
                "content": "Toc do ban hang tang 15% so voi quy truoc.",
                "token_count": 8,
                "embedding_model": "text-embedding-3-small",
                "embedding": _VECTOR,
            }
        ],
    )

    documents = await http.get(LIST_API, params={"project_id": str(PROJECT_ID)}, headers=headers)
    assert documents.status_code == 200, documents.text
    listed = next(row for row in documents.json() if row["id"] == upload["id"])
    assert listed["extraction_status"] == "succeeded"
    assert listed["registration_extraction_status"] == "not_requested"

    async def _fake_generate(prompt):
        import json

        return (
            json.dumps(
                {
                    "answer": "Tốc độ bán hàng tăng 15% so với quý trước [D1:p2].",
                    "citations": [
                        {
                            "marker": "D1:p2",
                            "document_id": upload["id"],
                            "document_title": "evidence.pdf",
                            "page": 2,
                            "quote": "Toc do ban hang tang 15% so voi quy truoc.",
                        }
                    ],
                    "insufficient_evidence": False,
                }
            ),
            {},
        )

    monkeypatch.setattr(advisory_tools, "generate_content", _fake_generate)

    response = await http.post(
        ASK_API,
        json={"project_id": str(PROJECT_ID), "question": "Tốc độ bán hàng quý này thế nào?"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["insufficient_evidence"] is False
    assert body["citations"][0]["document_id"] == upload["id"]
    assert body["citations"][0]["page"] == 2


async def test_ask_is_denied_outside_the_callers_project_scope(http):
    response = await http.post(
        ASK_API, json={"project_id": str(PROJECT_ID), "question": "anything"}, headers=VIEWER_HEADER
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


# --- GET /governance/feature-definitions ------------------------------------
# Read-only catalog route added so the frontend rubric editor can resolve a
# `feature_definition_id` for a canonical feature key without ever hardcoding
# a UUID — `list_feature_definitions()` (src/services/governance.py).

FEATURE_DEFINITIONS_API = "/api/v1/governance/feature-definitions"


async def test_list_feature_definitions_returns_only_active_rows_ordered_by_grain_then_key(http, jwks):
    now = datetime.now(UTC)
    async with http.session_factory() as session:
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                [
                    dict(
                        id=uuid.uuid4(), feature_key="area_accessibility", feature_version="v1",
                        name="Area accessibility", category="expert", grain="area", value_type="numeric",
                        formula_id="expert_value_assertion", normalization_method="identity",
                        direction="positive", missing_policy="skip", status="active",
                        definition_metadata={}, created_at=now, updated_at=now,
                    ),
                    dict(
                        id=uuid.uuid4(), feature_key="market_interest_rate", feature_version="v1",
                        name="Market interest rate", category="market", grain="market", value_type="numeric",
                        formula_id="expert_value_assertion", normalization_method="identity",
                        direction="negative", missing_policy="skip", status="active",
                        definition_metadata={}, created_at=now, updated_at=now,
                    ),
                    dict(
                        id=uuid.uuid4(), feature_key="retired_feature", feature_version="v1",
                        name="Retired", category="expert", grain="area", value_type="numeric",
                        formula_id="expert_value_assertion", normalization_method="identity",
                        direction="positive", missing_policy="skip", status="retired",
                        definition_metadata={}, created_at=now, updated_at=now,
                    ),
                ]
            )
        )
        await session.commit()

    private, _ = jwks
    response = await http.get(FEATURE_DEFINITIONS_API, headers=advisor_header(private, "feature-list"))
    assert response.status_code == 200, response.text
    body = response.json()
    keys = [row["feature_key"] for row in body]
    assert keys == ["area_accessibility", "market_interest_rate"], "retired row excluded; area before market"


async def test_list_feature_definitions_filters_by_grain(http, jwks):
    now = datetime.now(UTC)
    async with http.session_factory() as session:
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                [
                    dict(
                        id=uuid.uuid4(), feature_key="area_accessibility", feature_version="v1",
                        name="Area accessibility", category="expert", grain="area", value_type="numeric",
                        formula_id="expert_value_assertion", normalization_method="identity",
                        direction="positive", missing_policy="skip", status="active",
                        definition_metadata={}, created_at=now, updated_at=now,
                    ),
                    dict(
                        id=uuid.uuid4(), feature_key="market_demand", feature_version="v1",
                        name="Market demand", category="market", grain="market", value_type="numeric",
                        formula_id="expert_value_assertion", normalization_method="identity",
                        direction="positive", missing_policy="skip", status="active",
                        definition_metadata={}, created_at=now, updated_at=now,
                    ),
                ]
            )
        )
        await session.commit()

    private, _ = jwks
    response = await http.get(f"{FEATURE_DEFINITIONS_API}?grain=market", headers=advisor_header(private, "feature-filter"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["feature_key"] for row in body] == ["market_demand"]
