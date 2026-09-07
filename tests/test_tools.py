from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from PIL import Image

from quantech_vid.api import APIConfig, create_app
from quantech_vid.config import Settings
from quantech_vid.mcp_server import HTTPToolDispatcher, build_mcp_server
from quantech_vid.production_schemas import (OutputProfileV2, SceneProjectV2, SceneV2,
    SourceAsset, SourceProvenance, SourceRights, TrackV2)
from quantech_vid.production_service import ProductionService
from quantech_vid.production_store import ProductionStore, now_iso
from quantech_vid.tool_catalog import TOOL_DEFINITIONS, public_catalog
from quantech_vid.tool_service import ToolService


EXPECTED_NAMES = ["quantech_inspect_project", "quantech_stage_source_import",
    "quantech_analyze_visual_asset", "quantech_stage_storyboard", "quantech_stage_scene_changes",
    "quantech_stage_render", "quantech_run_approved_render", "quantech_inspect_production_result"]


@pytest.fixture()
def tools_fixture(tmp_path: Path) -> dict:
    settings = Settings(root=tmp_path, data_dir=tmp_path / "runtime", host="127.0.0.1", port=7476,
        allowed_asset_roots=(tmp_path,), tts_model="disabled", tts_voice_fr="disabled",
        tts_voice_en="disabled", max_workers=1)
    settings.ensure_directories()
    store = ProductionStore(settings.data_dir / "production.sqlite3", b"tool-signing-key-32-bytes-long!!",
                            "tool-pairing-code-2026")
    human_token, csrf = store.consume_pairing("tool-pairing-code-2026", "teacher")
    human = store.authenticate(human_token, csrf)
    image_path = settings.data_dir / "admitted-assets" / "fixture.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (96, 64), "#345678").save(image_path)
    asset = SourceAsset(id="src_" + "a" * 32, sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
        media_type="image/png", size=image_path.stat().st_size,
        provenance=SourceProvenance(origin="generated fixture", collected_by="teacher"),
        rights=SourceRights(basis="owned", reference="test fixture"),
        allowed_operations=["render", "analyze"], created_at=now_iso())
    store.add_source(human, asset, image_path)
    document = SceneProjectV2(slug="tool-project", title="Tool project", sources=[asset.id],
        scenes=[SceneV2(id="scene-1", duration=0.5, source_asset_id=asset.id,
                       title={"fr": "Scène", "en": "Scene"}, body={"fr": "", "en": ""})],
        tracks=[TrackV2(locale="en", title="Tool project", narration="Short narration")],
        output_profiles=[OutputProfileV2(name="square", width=320, height=320, fps=24)])
    revision = store.create_project(human, document)
    agent_token = store.create_agent("teacher", "tool-agent", "Tool fixture",
                                     revision.project_id, revision.revision)
    actor = store.authenticate(agent_token)
    other_token = store.create_agent("teacher", "other-tool-agent", "Other fixture",
                                     revision.project_id, revision.revision)
    other_actor = store.authenticate(other_token)
    production = ProductionService(settings, store, background=False)
    return {"settings": settings, "store": store, "production": production, "human": human,
            "human_token": human_token, "csrf": csrf, "actor": actor, "agent_token": agent_token,
            "other_actor": other_actor, "other_token": other_token, "asset": asset,
            "project_id": revision.project_id, "revision": revision.revision, "document": document}


def ref(fixture: dict) -> dict:
    return {"project_id": fixture["project_id"], "revision": fixture["revision"]}


def count_rows(fixture: dict, table: str) -> int:
    assert table in {"project_revisions", "render_plans", "production_grants", "production_jobs"}
    with sqlite3.connect(fixture["store"].path) as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_catalog_has_exact_closed_schemas() -> None:
    catalog = public_catalog()
    assert [item["name"] for item in catalog] == EXPECTED_NAMES
    assert len(TOOL_DEFINITIONS) == 8
    for item in catalog:
        assert item["input_schema"]["type"] == "object"
        assert item["input_schema"]["additionalProperties"] is False
        assert item["output_schema"]["additionalProperties"] is False
        for nested in item["input_schema"].get("$defs", {}).values():
            if nested.get("type") == "object":
                assert nested.get("additionalProperties") is False


def test_direct_dispatch_validates_and_stages_without_hidden_authority(tools_fixture: dict) -> None:
    service = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["actor"])
    base = ref(tools_fixture)
    inspected = service.dispatch("quantech_inspect_project", base)
    assert inspected["ok"] is True and inspected["result"]["revision"] == 1
    revision_count = count_rows(tools_fixture, "project_revisions")
    source_stage = service.dispatch("quantech_stage_source_import", {**base,
        "source_asset_id": tools_fixture["asset"].id})
    storyboard = service.dispatch("quantech_stage_storyboard", {**base, "mode": "replace",
        "scenes": [tools_fixture["document"].scenes[0].model_dump(mode="json")]})
    scene_change = service.dispatch("quantech_stage_scene_changes", {**base,
        "patches": [{"scene_id": "scene-1", "duration": 0.6}]})
    assert all(item["ok"] and item["result"]["effect"] == "proposal_only"
               for item in (source_stage, storyboard, scene_change))
    assert storyboard["result"]["diff_format"] == "quantech-scene-proposal-v1"
    assert count_rows(tools_fixture, "project_revisions") == revision_count
    analysis = service.dispatch("quantech_analyze_visual_asset", {**base,
        "source_asset_id": tools_fixture["asset"].id})
    assert analysis["ok"] is True
    assert analysis["result"]["source_sha256"] == tools_fixture["asset"].sha256
    assert analysis["result"]["representation_2d"] == {
        "kind": "verified_raster_2d", "media_type": "image/png", "format": "PNG",
        "width": 96, "height": 64, "color_mode": "RGB"}
    assert analysis["result"]["semantic_vision"] == {
        "status": "unavailable", "reason_code": "VISION_PROVIDER_NOT_CONFIGURED",
        "effect": "proposal_only", "observations": []}
    assert "path" not in json.dumps(analysis)


@pytest.mark.parametrize("mutation,code", [
    ("scope", "SOURCE_NOT_FOUND"),
    ("operation", "SOURCE_OPERATION_FORBIDDEN"),
    ("hash", "SOURCE_ANALYSIS_INTEGRITY_FAILED"),
    ("path", "SOURCE_ANALYSIS_INTEGRITY_FAILED"),
    ("mime", "SOURCE_ANALYSIS_UNSUPPORTED_MEDIA"),
    ("dimensions", "SOURCE_ANALYSIS_TOO_LARGE"),
])
def test_visual_analysis_fails_closed_before_optional_transport(
        tools_fixture: dict, mutation: str, code: str) -> None:
    actor = tools_fixture["actor"]
    source_id = tools_fixture["asset"].id
    if mutation == "scope":
        actor = {**actor, "source_ids": []}
    else:
        with sqlite3.connect(tools_fixture["store"].path) as db:
            if mutation == "operation":
                db.execute("UPDATE source_assets SET operations_json=? WHERE id=?",
                           ('["render"]', source_id))
            elif mutation == "hash":
                db.execute("UPDATE source_assets SET sha256=? WHERE id=?", ("0" * 64, source_id))
            elif mutation == "path":
                outside = tools_fixture["settings"].root / "outside.png"
                Image.new("RGB", (96, 64), "#345678").save(outside)
                db.execute("UPDATE source_assets SET internal_path=? WHERE id=?",
                           (str(outside), source_id))
            elif mutation == "mime":
                db.execute("UPDATE source_assets SET media_type='image/jpeg' WHERE id=?", (source_id,))
            elif mutation == "dimensions":
                row = db.execute("SELECT internal_path FROM source_assets WHERE id=?",
                                 (source_id,)).fetchone()
                image_path = Path(row[0])
                Image.new("RGB", (4001, 4000), "#345678").save(image_path)
                content = image_path.read_bytes()
                db.execute("UPDATE source_assets SET size=?,sha256=? WHERE id=?",
                           (len(content), hashlib.sha256(content).hexdigest(), source_id))

    service = ToolService(tools_fixture["store"], tools_fixture["production"], actor)
    result = service.dispatch("quantech_analyze_visual_asset", {
        **ref(tools_fixture), "source_asset_id": source_id})
    assert result["ok"] is False
    assert result["error"] == {"code": code,
        "message": "Tool request could not be completed", "retryable": False}


def test_runtime_rejects_unknown_extra_wrong_type_oversize_and_timeout(tools_fixture: dict) -> None:
    service = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["actor"])
    base = ref(tools_fixture)
    assert service.dispatch("missing", base)["error"]["code"] == "TOOL_NOT_FOUND"
    assert service.dispatch("quantech_inspect_project", {**base, "unexpected": True})["error"]["code"] == "INVALID_TOOL_INPUT"
    assert service.dispatch("quantech_inspect_project", {**base, "revision": "one"})["error"]["code"] == "INVALID_TOOL_INPUT"
    tiny = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["actor"], max_payload_bytes=20)
    assert tiny.dispatch("quantech_inspect_project", base)["error"]["code"] == "TOOL_INPUT_TOO_LARGE"
    wrong_project = service.dispatch("quantech_inspect_project", {
        **base, "project_id": "prj_" + "f" * 32})
    assert wrong_project["error"]["code"] == "PROJECT_REVISION_NOT_FOUND"
    ticks = iter([0.0, 1.0])
    timed = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["actor"], clock=lambda: next(ticks))
    assert timed.dispatch("quantech_inspect_project", {**base, "timeout_ms": 100})["error"]["code"] == "TOOL_TIMEOUT"
    assert count_rows(tools_fixture, "project_revisions") == 1


def test_storyboard_and_scene_proposals_validate_the_candidate_document(tools_fixture: dict) -> None:
    service = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["actor"])
    base = ref(tools_fixture)
    duplicate = tools_fixture["document"].scenes[0].model_dump(mode="json")
    rejected = service.dispatch("quantech_stage_storyboard", {
        **base, "mode": "append", "scenes": [duplicate]})
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INVALID_PROJECT_PROPOSAL"
    assert count_rows(tools_fixture, "project_revisions") == 1

    appended = dict(duplicate)
    appended["id"] = "scene-2"
    proposed = service.dispatch("quantech_stage_storyboard", {
        **base, "mode": "append", "scenes": [appended]})
    assert proposed["ok"] is True
    assert proposed["result"]["diff"] == [{"op": "add", "path": "/scenes/-", "value": appended}]

    repeated_patch = service.dispatch("quantech_stage_scene_changes", {**base, "patches": [
        {"scene_id": "scene-1", "duration": 0.7},
        {"scene_id": "scene-1", "duration": 0.8},
    ]})
    assert repeated_patch["error"]["code"] == "DUPLICATE_SCENE_PATCH"
    assert count_rows(tools_fixture, "project_revisions") == 1


def test_render_tool_requires_real_approval_binding_and_idempotency(tools_fixture: dict) -> None:
    service = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["actor"])
    staged = service.dispatch("quantech_stage_render", {**ref(tools_fixture), "locale": "en", "profile": "square",
                                                        "max_duration_seconds": 5, "max_output_bytes": 10_000_000})
    assert staged["ok"] is True and staged["result"]["authorization"] == "required"
    staged_again = service.dispatch("quantech_stage_render", {**ref(tools_fixture), "locale": "en", "profile": "square",
                                                              "max_duration_seconds": 5, "max_output_bytes": 10_000_000})
    assert staged_again["result"]["plan"]["id"] == staged["result"]["plan"]["id"]
    assert count_rows(tools_fixture, "render_plans") == 1
    assert count_rows(tools_fixture, "production_grants") == count_rows(tools_fixture, "production_jobs") == 0
    plan_id = staged["result"]["plan"]["id"]
    run_args = {**ref(tools_fixture), "plan_id": plan_id, "idempotency_key": "tool-request-key-0001"}
    denied = service.dispatch("quantech_run_approved_render", run_args)
    assert denied["ok"] is False and denied["error"]["code"] == "APPROVAL_REQUIRED"
    assert count_rows(tools_fixture, "production_jobs") == 0
    other = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["other_actor"])
    tools_fixture["store"].authorize(tools_fixture["human"], plan_id, tools_fixture["actor"]["id"], 300)
    assert other.dispatch("quantech_run_approved_render", run_args)["error"]["code"] == "APPROVAL_REQUIRED"
    queued = service.dispatch("quantech_run_approved_render", run_args)
    replay = service.dispatch("quantech_run_approved_render", run_args)
    assert queued["ok"] and replay["ok"]
    assert queued["result"]["job"]["id"] == replay["result"]["job"]["id"]
    assert replay["result"]["effect"] == "idempotent_replay" and count_rows(tools_fixture, "production_jobs") == 1
    job_id = queued["result"]["job"]["id"]
    tools_fixture["store"].cancel(tools_fixture["actor"], job_id)
    result = service.dispatch("quantech_inspect_production_result", {**ref(tools_fixture), "job_id": job_id})
    assert result["ok"] and result["result"]["job"]["status"] == "cancelled"
    wrong_revision = service.dispatch("quantech_inspect_project", {**ref(tools_fixture), "revision": 99})
    assert wrong_revision["error"]["code"] == "PROJECT_REVISION_NOT_FOUND"


def test_timeout_after_plan_commit_returns_recoverable_identity(tools_fixture: dict) -> None:
    ticks = iter([0.0, 1.0])
    service = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["actor"],
                          clock=lambda: next(ticks))
    result = service.dispatch("quantech_stage_render", {**ref(tools_fixture), "locale": "en",
        "profile": "square", "max_duration_seconds": 5, "max_output_bytes": 10_000_000,
        "timeout_ms": 100})
    assert result["ok"] is True
    assert result["result"]["deadline_exceeded"] is True
    assert result["result"]["deadline_reason_code"] == "TOOL_DEADLINE_EXCEEDED_AFTER_COMMIT"
    assert result["result"]["plan"]["id"].startswith("plan_")
    assert count_rows(tools_fixture, "render_plans") == 1


def test_api_catalog_dispatch_and_real_mcp_call_have_parity(tools_fixture: dict) -> None:
    app = create_app(tools_fixture["settings"], APIConfig(expected_host="127.0.0.1:7476",
        allowed_origin="http://127.0.0.1:7476", signing_key=b"tool-signing-key-32-bytes-long!!", background_jobs=False))
    client = TestClient(app)
    headers = {"host": "127.0.0.1:7476", "origin": "http://127.0.0.1:7476",
               "authorization": f"Bearer {tools_fixture['agent_token']}"}
    api_catalog = client.get("/api/v2/tools/catalog", headers=headers)
    assert api_catalog.status_code == 200 and api_catalog.json()["tools"] == public_catalog()
    direct = ToolService(tools_fixture["store"], tools_fixture["production"], tools_fixture["actor"])
    api_result = client.post("/api/v2/tools/quantech_inspect_project", headers=headers, json=ref(tools_fixture)).json()
    assert api_result == direct.dispatch("quantech_inspect_project", ref(tools_fixture))

    async def exercise_mcp() -> None:
        server = build_mcp_server(direct.dispatch)
        async with Client(server) as mcp_client:
            listing = await mcp_client.list_tools()
            assert [tool.name for tool in listing.tools] == EXPECTED_NAMES
            assert [tool.input_schema for tool in listing.tools] == [item["input_schema"] for item in public_catalog()]
            success = await mcp_client.call_tool("quantech_inspect_project", ref(tools_fixture))
            assert success.is_error is False and success.structured_content == api_result
            invalid = await mcp_client.call_tool("quantech_inspect_project", {**ref(tools_fixture), "extra": "blocked"})
            assert invalid.is_error is True
            assert invalid.structured_content["error"]["code"] == "INVALID_TOOL_INPUT"
    asyncio.run(exercise_mcp())


def test_mcp_http_dispatcher_restricts_origin_and_tool_path_without_network() -> None:
    for unsafe in ("http://user@127.0.0.1:7476", "http://127.0.0.1:7476?redirect=evil",
                   "http://127.0.0.1:7476/#fragment", "https://127.0.0.1:7476",
                   "http://example.com:7476"):
        with pytest.raises(ValueError):
            HTTPToolDispatcher(unsafe, "agent-token")

    dispatcher = HTTPToolDispatcher("http://127.0.0.1:1", "agent-token", timeout_seconds=1)
    result = asyncio.run(dispatcher("../../pair", {}))
    assert result["error"] == {"code": "TOOL_NOT_FOUND",
        "message": "Tool request could not be completed", "retryable": False}
