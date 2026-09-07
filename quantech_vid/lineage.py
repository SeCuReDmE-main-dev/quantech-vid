from __future__ import annotations

from typing import Any

from .production_schemas import ProjectRevision, RenderPlan


def build_source_lineage_sidecar(
    *, plan: RenderPlan, revision: ProjectRevision,
    sources: list[Any], originals: list[Any],
) -> dict:
    """Build a path-free receipt payload for exact originals linked by the store."""
    derived = {row["id"]: row for row in sources}
    return {
        "schema_version": "1.0",
        "project": {
            "id": revision.project_id,
            "revision": revision.revision,
            "sha256": revision.document_hash,
        },
        "plan": {"id": plan.id, "sha256": plan.plan_hash},
        "sources": [
            {
                "source_asset_id": row["source_id"],
                "derived_sha256": derived[row["source_id"]]["sha256"],
                "original_sha256": row["sha256"],
                "original_size": row["size"],
                "original_media_type": row["media_type"],
                "transformation": row["transformation"],
            }
            for row in originals
        ],
        "limitation": {
            "rights_and_provenance_are_operator_declarations": True,
            "independent_content_verification": False,
        },
    }
