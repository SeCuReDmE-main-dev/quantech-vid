from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedClaimModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimEvidence(ClosedClaimModel):
    source_asset_id: str = Field(pattern=r"^src_[a-f0-9]{32}$")
    locator: str = Field(min_length=1, max_length=160)


class Claim(ClosedClaimModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    text: str = Field(min_length=1, max_length=1000)
    status: Literal["reported", "observed", "hypothesis", "disputed", "suspended"]
    rationale: str = Field(min_length=1, max_length=500)
    evidence: list[ClaimEvidence] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def observed_has_evidence(self) -> "Claim":
        if self.status == "observed" and not self.evidence:
            raise ValueError("observed claims require at least one evidence reference")
        return self


VISIBLE_WARNING_STATUSES = frozenset({"hypothesis", "disputed", "suspended"})
CLAIM_NOTICE_LIMIT = 500


def claim_notice(claims: list[Claim]) -> str:
    warnings = [claim for claim in claims if claim.status in VISIBLE_WARNING_STATUSES]
    if not warnings:
        return ""
    counts = {status: sum(claim.status == status for claim in warnings)
              for status in ("hypothesis", "disputed", "suspended")}
    entries = [f"{status}={count}" for status, count in counts.items() if count]
    notice = ("[Claim notice: author-declared; independent verification not performed] "
              + "; ".join(entries) + ". See the claim-provenance sidecar for full statements.")
    if len(notice) <= CLAIM_NOTICE_LIMIT:
        return notice
    return notice[: CLAIM_NOTICE_LIMIT - 1].rstrip() + "…"


def append_claim_notice(body: str, claims: list[Claim]) -> str:
    notice = claim_notice(claims)
    return f"{body}\n\n{notice}" if body and notice else notice or body


class ClaimScene(Protocol):
    id: str
    claims: list[Claim]


def build_claim_sidecar(*, project_id: str, revision: int, project_hash: str,
                        scenes: list[ClaimScene], source_hashes: dict[str, str]) -> dict:
    scene_entries = []
    for scene in scenes:
        claims = []
        for claim in scene.claims:
            payload = claim.model_dump(mode="json")
            payload["evidence"] = [
                {**evidence, "source_sha256": source_hashes[evidence["source_asset_id"]]}
                for evidence in payload["evidence"]
            ]
            claims.append(payload)
        if claims:
            scene_entries.append({"scene_id": scene.id, "claims": claims})
    return {
        "schema_version": "quantech.claim-provenance.v1",
        "project": {"id": project_id, "revision": revision, "sha256": project_hash},
        "limitation": {
            "independent_verification": False,
            "status_meaning": "Author-declared classification, not an independent truth or license finding.",
        },
        "scenes": scene_entries,
    }
