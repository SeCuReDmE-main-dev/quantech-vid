from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .claims import Claim
from .scene3d import Visual3DConfig
from .transcripts import TranscriptSegment, validate_track_segments


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


SourceId = Annotated[str, Field(pattern=r"^src_[a-f0-9]{32}$")]


class DeclaredSourceProvenance(ClosedModel):
    origin: str = Field(min_length=1, max_length=240)
    collected_by: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class OriginalSourceDescriptor(ClosedModel):
    name: str = Field(min_length=1, max_length=180)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=1, le=50_000_000)
    media_type: Literal["image/png", "image/jpeg", "image/webp", "text/plain", "text/markdown", "model/gltf-binary", "audio/wav"]
    transformation: Literal["literal-text-preview-v1", "rgb-png-v1", "glb-four-view-png-v1", "pcm16-waveform-png-v1"]

    @field_validator("name")
    @classmethod
    def filename_only(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("original name must be a filename")
        return value


class SourceProvenance(DeclaredSourceProvenance):
    original: OriginalSourceDescriptor | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class SourceRights(ClosedModel):
    basis: Literal["owned", "licensed", "public-domain", "permission"]
    reference: str = Field(min_length=1, max_length=500)


class SourceAsset(ClosedModel):
    id: str
    sha256: str
    media_type: str
    size: int
    provenance: SourceProvenance
    rights: SourceRights
    allowed_operations: list[Literal["render", "analyze"]]
    created_at: str


class AsrProposalRequest(ClosedModel):
    project_id: str = Field(pattern=r"^prj_[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    locale: Literal["en"]
    expected_asset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_machine_proposal_only: Literal[True]


class AsrProposalLimitations(ClosedModel):
    machine_proposal_only: Literal[True] = True
    human_review_required: Literal[True] = True
    speaker_identity_inferred: Literal[False] = False
    vad_performed: Literal[False] = False
    exact_zero_energy_rejected: Literal[True] = True
    independent_verification: Literal[False] = False


class AsrProposal(ClosedModel):
    schema_version: Literal["quantech.asr-proposal.v1"]
    proposal_id: str = Field(pattern=r"^asrp_[a-f0-9]{32}$")
    effect: Literal["proposal_only"]
    project_id: str = Field(pattern=r"^prj_[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    project_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    locale: Literal["en"]
    source_asset_id: SourceId
    asset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_audio_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    audio_duration_ms: int = Field(ge=1, le=300_000)
    resource_binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    segments: list[TranscriptSegment] = Field(max_length=128)
    limitations: AsrProposalLimitations


class AdmitSourceRequest(ClosedModel):
    selection_token: str = Field(min_length=16, max_length=200)
    provenance: DeclaredSourceProvenance
    rights: SourceRights
    allowed_operations: list[Literal["render", "analyze"]] = Field(min_length=1)


class SceneV2(ClosedModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    duration: float = Field(gt=0, le=60)
    source_asset_id: str
    title: dict[Literal["fr", "en"], str]
    body: dict[Literal["fr", "en"], str] = Field(default_factory=dict)
    fit: Literal["cover", "contain"] = "cover"
    claims: list[Claim] = Field(default_factory=list, max_length=16,
                                exclude_if=lambda value: not value)
    visual_3d: Visual3DConfig | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def bounded_copy(self) -> "SceneV2":
        if not self.title or any(not value or len(value) > 200 for value in self.title.values()):
            raise ValueError("scene titles must contain 1 to 200 characters")
        if any(len(value) > 1000 for value in self.body.values()):
            raise ValueError("scene bodies may contain at most 1000 characters")
        return self


class TrackV2(ClosedModel):
    locale: Literal["fr", "en"]
    title: str = Field(min_length=1, max_length=200)
    narration: str = Field(min_length=1, max_length=8000)
    voice: str | None = Field(default=None, max_length=120)
    segments: list[TranscriptSegment] = Field(
        default_factory=list, max_length=128, exclude_if=lambda value: not value
    )


class OutputProfileV2(ClosedModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    width: int = Field(ge=320, le=4096)
    height: int = Field(ge=320, le=4096)
    fps: int = Field(default=30, ge=12, le=60)


class SceneProjectV2(ClosedModel):
    schema_version: Literal["2.0"] = "2.0"
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=200)
    disclosure: str = Field(default="AI-assisted production reviewed by a human.", max_length=500)
    sources: list[str] = Field(min_length=1, max_length=128)
    scenes: list[SceneV2] = Field(min_length=1, max_length=128)
    tracks: list[TrackV2] = Field(min_length=1, max_length=2)
    output_profiles: list[OutputProfileV2] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_document(self) -> "SceneProjectV2":
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("sources must be unique")
        if any(scene.source_asset_id not in self.sources for scene in self.scenes):
            raise ValueError("every scene source must be declared")
        if len({scene.id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("scene ids must be unique")
        for scene in self.scenes:
            if len({claim.id for claim in scene.claims}) != len(scene.claims):
                raise ValueError("claim ids must be unique within each scene")
            if any(evidence.source_asset_id not in self.sources
                   for claim in scene.claims for evidence in claim.evidence):
                raise ValueError("claim evidence sources must be declared")
        if len({track.locale for track in self.tracks}) != len(self.tracks):
            raise ValueError("track locales must be unique")
        declared_locales = {track.locale for track in self.tracks}
        if any(not declared_locales.issubset(scene.title) for scene in self.scenes):
            raise ValueError("each scene needs a title for every declared locale")
        if len({profile.name for profile in self.output_profiles}) != len(self.output_profiles):
            raise ValueError("output profiles must be unique")
        duration = sum(scene.duration for scene in self.scenes)
        for track in self.tracks:
            validate_track_segments(track.segments, duration=duration, source_ids=set(self.sources))
        return self


class CreateProjectRequest(ClosedModel):
    document: SceneProjectV2


class MigrateV1Request(ClosedModel):
    provenance: DeclaredSourceProvenance
    rights: SourceRights


class ReviseProjectRequest(ClosedModel):
    base_revision: int = Field(ge=1)
    document: SceneProjectV2


class ProjectRevision(ClosedModel):
    project_id: str
    revision: int
    document_hash: str
    document: SceneProjectV2
    created_at: str


class RegisterAgentRequest(ClosedModel):
    agent_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,79}$")
    label: str = Field(min_length=1, max_length=120)
    project_id: str = Field(pattern=r"^prj_[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    source_ids: list[SourceId] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def unique_sources(self) -> "RegisterAgentRequest":
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("scope sources must be unique")
        return self


class PrepareRenderPlanRequest(ClosedModel):
    project_id: str
    revision: int = Field(ge=1)
    locale: Literal["fr", "en"]
    profile: str
    narration_mode: Literal["silent", "local_kokoro_cpu"] = "silent"
    max_duration_seconds: float = Field(default=600, gt=0, le=600)
    max_output_bytes: int = Field(default=500_000_000, ge=1_000_000, le=2_000_000_000)


class RenderPlan(ClosedModel):
    id: str
    project_id: str
    revision: int
    project_hash: str
    asset_hashes: dict[str, str]
    original_hashes: dict[str, str] = Field(
        default_factory=dict, max_length=128, exclude_if=lambda value: not value
    )
    locale: Literal["fr", "en"]
    profile: str
    narration_mode: Literal["silent", "local_kokoro_cpu"]
    provider_resource_modes: dict[str, str]
    limits: dict[str, int | float]
    plan_hash: str
    created_at: str


class AuthorizePlanRequest(ClosedModel):
    agent_id: str
    project_id: str = Field(pattern=r"^prj_[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    expires_in_seconds: int = Field(default=900, ge=30, le=3600)


class RunApprovedRenderRequest(ClosedModel):
    plan_id: str


class ArtifactReceipt(ClosedModel):
    name: str
    role: str
    media_type: str
    size: int
    sha256: str


class ProductionJob(ClosedModel):
    id: str
    project_id: str
    revision: int
    plan_id: str
    status: Literal["queued", "running", "complete", "failed", "cancelled"]
    progress: int = Field(ge=0, le=100)
    error_code: str | None = None
    receipts: list[ArtifactReceipt] = Field(default_factory=list)
    created_at: str
    updated_at: str
