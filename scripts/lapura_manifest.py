"""Shared manifest helpers for the La Pura seed. A manifest has two passes:

Pass 1 (written by `derive_lapura_seed_fixture.py --write-fixture`): records
source-row -> fixture `external_key` for every entity, before anything is
sent to any API. `real_id`/`real_external_id` are `None` on every entity.

Pass 2 (written by `seed_lapura.py --confirm-seed`, after the real MiniCRM/
AbsorpIQ writes complete): fills in the REAL assigned `external_id` (MiniCRM)
and `id` (AbsorpIQ UUID, once synced) for every entity — the actual
old-to-new ID mapping this seed produced.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ManifestError(RuntimeError):
    pass


def load_manifest(path: Path) -> dict[str, Any]:
    import json

    if not path.exists():
        raise ManifestError(f"Manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def apply_real_ids(manifest: dict[str, Any], real_ids: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """`real_ids`: fixture_external_key -> {"real_external_id": ..., "real_id": ...}.
    Every entity in the manifest MUST be present in `real_ids` — a partial
    fill means the seed run didn't finish, which is exactly the "partial
    prior import" state `--mode resume` exists to detect and complete, never
    to paper over.
    """
    result = copy.deepcopy(manifest)
    missing = [
        e["fixture_external_key"] for e in result["entities"] if e["fixture_external_key"] not in real_ids
    ]
    if missing:
        raise ManifestError(f"apply_real_ids: missing real ids for {len(missing)} entities: {missing[:5]}...")
    for entity in result["entities"]:
        found = real_ids[entity["fixture_external_key"]]
        entity["real_external_id"] = found.get("real_external_id")
        entity["real_id"] = found.get("real_id")
    result["pass"] = 2
    result["pass_2_completed_at"] = datetime.now(UTC).isoformat()
    return result


def is_pass_2_complete(manifest: dict[str, Any]) -> bool:
    return manifest.get("pass") == 2 and all(e.get("real_id") is not None for e in manifest["entities"])


def real_id_by_fixture_key(manifest: dict[str, Any], kind: str) -> dict[str, str]:
    """fixture_external_key -> real_id, restricted to one entity kind
    ('project' | 'area' | 'unit' | 'deal')."""
    return {
        e["fixture_external_key"]: e["real_id"]
        for e in manifest["entities"]
        if e["kind"] == kind and e.get("real_id") is not None
    }
