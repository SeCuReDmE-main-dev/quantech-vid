from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from .claims import Claim
from .production_schemas import ClosedModel, SceneV2
from .scene3d import Visual3DConfig


class ProjectRevisionInput(ClosedModel):
    project_id: str = Field(pattern=r"^prj_[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    timeout_ms: int = Field(default=5000, ge=100, le=30_000)


class InspectProjectInput(ProjectRevisionInput):
    include_document: bool = False


class StageSourceImportInput(ProjectRevisionInput):
    source_asset_id: str = Field(pattern=r"^src_[a-f0-9]{32}$")
    mode: Literal["admitted_source"] = "admitted_source"


class AnalyzeVisualAssetInput(ProjectRevisionInput):
    source_asset_id: str = Field(pattern=r"^src_[a-f0-9]{32}$")


class StageStoryboardInput(ProjectRevisionInput):
    mode: Literal["replace", "append"] = "replace"
    scenes: list[SceneV2] = Field(min_length=1, max_length=128)


class ScenePatch(ClosedModel):
    scene_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    duration: float | None = Field(default=None, gt=0, le=60)
    title: dict[Literal["fr", "en"], str] | None = None
    body: dict[Literal["fr", "en"], str] | None = None
    source_asset_id: str | None = Field(default=None, pattern=r"^src_[a-f0-9]{32}$")
    fit: Literal["cover", "contain"] | None = None
    claims: list[Claim] | None = Field(default=None, max_length=16)
    visual_3d: Visual3DConfig | None = None

    @model_validator(mode="after")
    def has_change(self) -> "ScenePatch":
        if all(getattr(self, name) is None
               for name in ("duration", "title", "body", "source_asset_id", "fit", "claims",
                            "visual_3d")):
            raise ValueError("patch must contain a change")
        if self.title is not None and any(not value or len(value) > 200 for value in self.title.values()):
            raise ValueError("scene titles must contain 1 to 200 characters")
        if self.body is not None and any(len(value) > 1000 for value in self.body.values()):
            raise ValueError("scene bodies may contain at most 1000 characters")
        return self


class StageSceneChangesInput(ProjectRevisionInput):
    patches: list[ScenePatch] = Field(min_length=1, max_length=128)


class StageRenderInput(ProjectRevisionInput):
    locale: Literal["fr", "en"]
    profile: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    narration_mode: Literal["silent"] = "silent"
    max_duration_seconds: float = Field(default=600, gt=0, le=600)
    max_output_bytes: int = Field(default=500_000_000, ge=1_000_000, le=2_000_000_000)


class RunApprovedRenderInput(ProjectRevisionInput):
    plan_id: str = Field(pattern=r"^plan_[a-f0-9]{32}$")
    idempotency_key: str = Field(min_length=16, max_length=200)


class InspectProductionResultInput(ProjectRevisionInput):
    job_id: str = Field(pattern=r"^job_[a-f0-9]{32}$")


COMMON_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "tool": {"type": "string"},
        "result": {"type": ["object", "null"]},
        "error": {
            "type": ["object", "null"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "retryable": {"type": "boolean"},
            },
            "required": ["code", "message", "retryable"],
            "additionalProperties": False,
        },
    },
    "required": ["ok", "tool", "result", "error"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[ClosedModel]
    read_only: bool
    idempotent: bool

    @property
    def input_schema(self) -> dict:
        return self.input_model.model_json_schema()

    def public(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema, "output_schema": COMMON_OUTPUT_SCHEMA,
                "annotations": {"read_only": self.read_only, "idempotent": self.idempotent,
                                "open_world": False}}


TOOL_DEFINITIONS = (
    ToolDefinition("quantech_inspect_project", "Inspect one authorized project revision without changing it.",
                   InspectProjectInput, True, True),
    ToolDefinition("quantech_stage_source_import", "Propose adding an already-admitted opaque source to a project; never reads a path or URL.",
                   StageSourceImportInput, True, True),
    ToolDefinition("quantech_analyze_visual_asset", "Read bounded structural image metadata; semantic vision remains unavailable.",
                   AnalyzeVisualAssetInput, True, True),
    ToolDefinition("quantech_stage_storyboard", "Return a storyboard proposal and diff without mutating the canonical revision.",
                   StageStoryboardInput, True, True),
    ToolDefinition("quantech_stage_scene_changes", "Return proposed scene patches without applying or approving them.",
                   StageSceneChangesInput, True, True),
    ToolDefinition("quantech_stage_render", "Prepare an immutable, bounded silent-render plan; does not authorize production.",
                   StageRenderInput, False, True),
    ToolDefinition("quantech_run_approved_render", "Queue only a matching server-approved plan with an idempotency key; cannot mint approval.",
                   RunApprovedRenderInput, False, True),
    ToolDefinition("quantech_inspect_production_result", "Inspect an authorized production job and byte receipts without returning paths or credentials.",
                   InspectProductionResultInput, True, True),
)

TOOL_BY_NAME = {item.name: item for item in TOOL_DEFINITIONS}


def public_catalog() -> list[dict]:
    return [item.public() for item in TOOL_DEFINITIONS]
