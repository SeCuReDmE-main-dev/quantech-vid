"""Closed conversion from a local ASR receipt to a non-authoritative draft."""
from __future__ import annotations

import hashlib
import json

from .local_asr import LocalAsrResult
from .production_schemas import (
    AsrProposal,
    AsrProposalLimitations,
    ProjectRevision,
)
from .transcripts import TranscriptSegment, validate_track_segments


class AsrProposalError(ValueError):
    pass


def build_asr_proposal(
    result: LocalAsrResult,
    *,
    revision: ProjectRevision,
    source_asset_id: str,
    asset_sha256: str,
    original_audio_sha256: str,
) -> AsrProposal:
    """Validate and project a worker receipt without mutating canonical state."""
    try:
        receipt = result.receipt
        audio = receipt["audio"]
        raw_segments = receipt["segments"]
        if not isinstance(audio, dict) or not isinstance(raw_segments, list):
            raise TypeError
        duration_ms = audio["duration_ms"]
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
            raise TypeError
        segments = []
        for raw in raw_segments:
            if not isinstance(raw, dict):
                raise TypeError
            start_ms, end_ms = raw["start_ms"], raw["end_ms"]
            if (isinstance(start_ms, bool) or not isinstance(start_ms, int)
                    or isinstance(end_ms, bool) or not isinstance(end_ms, int)):
                raise TypeError
            segments.append(TranscriptSegment(
                id=raw["id"], start=start_ms / 1000, end=end_ms / 1000,
                text=raw["text"], source_asset_id=source_asset_id,
                source_locator=(
                    f"ASR draft {start_ms / 1000:.3f}-{end_ms / 1000:.3f} s; "
                    "human review required"
                ),
            ))
        duration = sum(scene.duration for scene in revision.document.scenes)
        validate_track_segments(
            segments, duration=duration, source_ids=set(revision.document.sources)
        )
        identity = {
            "project_id": revision.project_id,
            "revision": revision.revision,
            "project_hash": revision.document_hash,
            "source_asset_id": source_asset_id,
            "asset_sha256": asset_sha256,
            "original_audio_sha256": original_audio_sha256,
            "binding_sha256": result.binding_sha256,
            "segments": [item.model_dump(mode="json") for item in segments],
        }
        digest = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        return AsrProposal(
            schema_version="quantech.asr-proposal.v1",
            proposal_id="asrp_" + digest[:32], effect="proposal_only",
            project_id=revision.project_id, revision=revision.revision,
            project_hash=revision.document_hash, locale="en",
            source_asset_id=source_asset_id, asset_sha256=asset_sha256,
            original_audio_sha256=original_audio_sha256,
            audio_duration_ms=duration_ms,
            resource_binding_sha256=result.binding_sha256,
            segments=segments, limitations=AsrProposalLimitations(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AsrProposalError("LOCAL_ASR_PROPOSAL_INVALID") from exc
