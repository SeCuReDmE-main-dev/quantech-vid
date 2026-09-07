from __future__ import annotations

import json
import time
from typing import Callable

from pydantic import ValidationError

from .production_schemas import SceneProjectV2
from .production_service import ProductionService
from .production_store import ContractError, ProductionStore, canonical_hash
from .tool_catalog import (
    AnalyzeVisualAssetInput,
    InspectProductionResultInput,
    InspectProjectInput,
    RunApprovedRenderInput,
    StageRenderInput,
    StageSceneChangesInput,
    StageSourceImportInput,
    StageStoryboardInput,
    TOOL_BY_NAME,
)
from .vision import VisionAdapter, VisionBoundaryError, decode_raster


class ToolService:
    def __init__(self, store: ProductionStore, production: ProductionService, actor: dict,
                 *, max_payload_bytes: int = 262_144,
                 clock: Callable[[], float] = time.monotonic,
                 vision: VisionAdapter | None = None) -> None:
        self.store = store
        self.production = production
        self.actor = actor
        self.max_payload_bytes = max_payload_bytes
        self.clock = clock
        self.vision = vision if vision is not None else VisionAdapter()

    def dispatch(self, name: str, arguments: object) -> dict:
        if self.actor.get("kind") != "agent":
            return self._error(name, "AGENT_CLIENT_REQUIRED", False)
        definition = TOOL_BY_NAME.get(name)
        if definition is None:
            return self._error(name, "TOOL_NOT_FOUND", False)
        if not isinstance(arguments, dict):
            return self._error(name, "INVALID_TOOL_INPUT", False)
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode()
        except (TypeError, ValueError):
            return self._error(name, "INVALID_TOOL_INPUT", False)
        if len(encoded) > self.max_payload_bytes:
            return self._error(name, "TOOL_INPUT_TOO_LARGE", False)
        try:
            payload = definition.input_model.model_validate(arguments)
        except ValidationError:
            return self._error(name, "INVALID_TOOL_INPUT", False)
        started = self.clock()
        try:
            result = self._execute(name, payload)
            if (self.clock() - started) * 1000 > payload.timeout_ms:
                if name in {"quantech_stage_render", "quantech_run_approved_render"}:
                    result["deadline_exceeded"] = True
                    result["deadline_reason_code"] = "TOOL_DEADLINE_EXCEEDED_AFTER_COMMIT"
                    return {"ok": True, "tool": name, "result": result, "error": None}
                return self._error(name, "TOOL_TIMEOUT", True)
            return {"ok": True, "tool": name, "result": result, "error": None}
        except ContractError as exc:
            return self._error(name, exc.code, exc.code in {"REVISION_CONFLICT", "STALE_PROJECT_REVISION"})
        except VisionBoundaryError as exc:
            return self._error(name, exc.code, False)
        except Exception:
            return self._error(name, "INTERNAL_TOOL_ERROR", False)

    @staticmethod
    def _error(name: str, code: str, retryable: bool) -> dict:
        return {"ok": False, "tool": name, "result": None,
                "error": {"code": code, "message": "Tool request could not be completed", "retryable": retryable}}

    def _execute(self, name: str, payload: object) -> dict:
        if name == "quantech_inspect_project":
            return self._inspect_project(payload)
        if name == "quantech_stage_source_import":
            return self._stage_source(payload)
        if name == "quantech_analyze_visual_asset":
            return self._analyze_visual(payload)
        if name == "quantech_stage_storyboard":
            return self._stage_storyboard(payload)
        if name == "quantech_stage_scene_changes":
            return self._stage_scene_changes(payload)
        if name == "quantech_stage_render":
            return self._stage_render(payload)
        if name == "quantech_run_approved_render":
            return self._run_approved(payload)
        if name == "quantech_inspect_production_result":
            return self._inspect_result(payload)
        raise ContractError("TOOL_NOT_FOUND", 404)

    def _revision(self, payload: object):
        return self.store.get_revision(self.actor, payload.project_id, payload.revision)

    @staticmethod
    def _validate_candidate(candidate: dict) -> None:
        try:
            SceneProjectV2.model_validate(candidate)
        except ValidationError as exc:
            raise ContractError("INVALID_PROJECT_PROPOSAL", 422) from exc

    def _proposal(self, name: str, payload: object, project_hash: str, diff: list[dict], **extra: object) -> dict:
        identity = canonical_hash({"tool": name, "actor": self.actor["id"],
                                   "input": payload.model_dump(mode="json"), "project_hash": project_hash})
        return {"proposal_id": "proposal_" + identity[:32], "effect": "proposal_only",
                "diff_format": "quantech-scene-proposal-v1",
                "project_hash": project_hash, "diff": diff, **extra}

    def _inspect_project(self, payload: InspectProjectInput) -> dict:
        revision = self._revision(payload)
        result = {"project_id": revision.project_id, "revision": revision.revision,
                  "document_hash": revision.document_hash, "slug": revision.document.slug,
                  "title": revision.document.title, "source_ids": revision.document.sources,
                  "scene_count": len(revision.document.scenes),
                  "track_locales": [track.locale for track in revision.document.tracks],
                  "profiles": [profile.name for profile in revision.document.output_profiles]}
        if payload.include_document:
            result["document"] = revision.document.model_dump(mode="json")
        return result

    def _stage_source(self, payload: StageSourceImportInput) -> dict:
        revision = self._revision(payload)
        row = self.store.source_rows(self.actor, [payload.source_asset_id], "render")[0]
        diff = [] if payload.source_asset_id in revision.document.sources else [
            {"op": "add", "path": "/sources/-", "value": payload.source_asset_id}
        ]
        candidate = revision.document.model_dump(mode="json")
        if diff:
            candidate["sources"].append(payload.source_asset_id)
            self._validate_candidate(candidate)
        return self._proposal("quantech_stage_source_import", payload, revision.document_hash, diff,
                              source={"id": row["id"], "sha256": row["sha256"],
                                      "media_type": row["media_type"], "size": row["size"]})

    def _analyze_visual(self, payload: AnalyzeVisualAssetInput) -> dict:
        revision = self._revision(payload)
        content, media_type, source_hash = self.store.source_analysis(
            self.actor, payload.source_asset_id)
        representation = decode_raster(content, media_type)
        return {"project_hash": revision.document_hash,
                "source_sha256": source_hash,
                "asset": {"id": payload.source_asset_id, "sha256": source_hash,
                          "media_type": media_type, "width": representation.width,
                          "height": representation.height, "format": representation.format,
                          "color_mode": representation.color_mode},
                "representation_2d": representation.model_dump(mode="json"),
                "semantic_vision": self.vision.analyze(content, media_type, representation),
                "effect": "read_only"}

    def _stage_storyboard(self, payload: StageStoryboardInput) -> dict:
        revision = self._revision(payload)
        if any(scene.source_asset_id not in revision.document.sources for scene in payload.scenes):
            raise ContractError("SOURCE_NOT_DECLARED", 422)
        value = [scene.model_dump(mode="json") for scene in payload.scenes]
        candidate = revision.document.model_dump(mode="json")
        candidate["scenes"] = value if payload.mode == "replace" else candidate["scenes"] + value
        self._validate_candidate(candidate)
        diff = ([{"op": "replace", "path": "/scenes", "value": value}]
                if payload.mode == "replace"
                else [{"op": "add", "path": "/scenes/-", "value": scene} for scene in value])
        return self._proposal("quantech_stage_storyboard", payload, revision.document_hash, diff)

    def _stage_scene_changes(self, payload: StageSceneChangesInput) -> dict:
        revision = self._revision(payload)
        known = {scene.id for scene in revision.document.scenes}
        if any(patch.scene_id not in known for patch in payload.patches):
            raise ContractError("SCENE_NOT_FOUND", 404)
        if len({patch.scene_id for patch in payload.patches}) != len(payload.patches):
            raise ContractError("DUPLICATE_SCENE_PATCH", 422)
        for patch in payload.patches:
            if patch.source_asset_id and patch.source_asset_id not in revision.document.sources:
                raise ContractError("SOURCE_NOT_DECLARED", 422)
        candidate = revision.document.model_dump(mode="json")
        by_id = {scene["id"]: scene for scene in candidate["scenes"]}
        for patch in payload.patches:
            by_id[patch.scene_id].update(patch.model_dump(mode="json", exclude={"scene_id"}, exclude_none=True))
        candidate["scenes"] = [by_id[scene["id"]] for scene in candidate["scenes"]]
        self._validate_candidate(candidate)
        diff = [{"op": "merge", "path": f"/scenes/{patch.scene_id}",
                 "value": patch.model_dump(mode="json", exclude={"scene_id"}, exclude_none=True)}
                for patch in payload.patches]
        return self._proposal("quantech_stage_scene_changes", payload, revision.document_hash, diff)

    def _stage_render(self, payload: StageRenderInput) -> dict:
        plan = self.store.create_plan(self.actor, payload.model_dump(exclude={"timeout_ms"}))
        return {"effect": "plan_prepared_only", "authorization": "required",
                "plan": plan.model_dump(mode="json")}

    def _run_approved(self, payload: RunApprovedRenderInput) -> dict:
        plan = self.store.get_plan(self.actor, payload.plan_id)
        if plan.project_id != payload.project_id or plan.revision != payload.revision:
            raise ContractError("PLAN_BINDING_MISMATCH", 409)
        job, created = self.store.enqueue_approved(self.actor, payload.plan_id, payload.idempotency_key)
        if created:
            self.production.schedule()
        return {"effect": "approved_job_queued" if created else "idempotent_replay",
                "job": job.model_dump(mode="json")}

    def _inspect_result(self, payload: InspectProductionResultInput) -> dict:
        job = self.store.job_for(self.actor, payload.job_id)
        if job.project_id != payload.project_id or job.revision != payload.revision:
            raise ContractError("JOB_BINDING_MISMATCH", 409)
        return {"effect": "read_only", "job": job.model_dump(mode="json")}
