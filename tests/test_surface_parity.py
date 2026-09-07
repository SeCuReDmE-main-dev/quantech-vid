from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from quantech_vid.api import APIConfig, create_app
from quantech_vid.config import Settings
from quantech_vid.production_store import canonical_hash


ORIGIN = "http://127.0.0.1:7476"
BASE_HEADERS = {"host": "127.0.0.1:7476", "origin": ORIGIN}
TOOL_NAMES = [
    "quantech_inspect_project",
    "quantech_stage_source_import",
    "quantech_analyze_visual_asset",
    "quantech_stage_storyboard",
    "quantech_stage_scene_changes",
    "quantech_stage_render",
    "quantech_run_approved_render",
    "quantech_inspect_production_result",
]


def test_human_api_and_catalogue_tools_share_plan_and_idempotent_job(tmp_path: Path) -> None:
    source_path = tmp_path / "surface-parity.png"
    Image.new("RGB", (96, 64), "#345678").save(source_path)
    settings = Settings(
        root=tmp_path,
        data_dir=tmp_path / "runtime",
        host="127.0.0.1",
        port=7476,
        allowed_asset_roots=(tmp_path,),
        tts_model="disabled",
        tts_voice_fr="disabled",
        tts_voice_en="disabled",
        max_workers=1,
    )
    settings.ensure_directories()
    app = create_app(
        settings,
        APIConfig(
            expected_host="127.0.0.1:7476",
            allowed_origin=ORIGIN,
            pairing_code="surface-parity-code-2026",
            signing_key=b"surface-parity-signing-key-32!!",
            selections={"surface-parity-selection": source_path},
            background_jobs=False,
        ),
    )

    # This is a control-plane parity test. It deliberately cannot invoke the
    # real FFmpeg renderer; renderer output is covered by the render QA tests.
    render_calls: list[str] = []

    def forbidden_synthetic_renderer(*args, **kwargs):
        del args, kwargs
        render_calls.append("called")
        raise AssertionError("surface parity must not execute a renderer")

    app.state.production_service.render_fn = forbidden_synthetic_renderer
    schedule_calls: list[str] = []
    app.state.production_service.schedule = lambda: schedule_calls.append("scheduled")

    with TestClient(app) as client:
        paired = client.post(
            "/api/v2/pair",
            headers=BASE_HEADERS,
            json={"operator_code": "surface-parity-code-2026", "actor_id": "parity-operator"},
        )
        assert paired.status_code == 201, paired.text
        human_headers = {
            **BASE_HEADERS,
            "authorization": f"Bearer {paired.json()['session_token']}",
            "x-csrf-token": paired.json()["csrf_token"],
        }
        admitted = client.post(
            "/api/v2/sources/admit",
            headers=human_headers,
            json={
                "selection_token": "surface-parity-selection",
                "provenance": {"origin": "synthetic parity fixture", "collected_by": "test operator"},
                "rights": {"basis": "owned", "reference": "generated test fixture"},
                "allowed_operations": ["render"],
            },
        )
        assert admitted.status_code == 201, admitted.text
        asset = admitted.json()
        document = {
            "schema_version": "2.0",
            "slug": "surface-parity",
            "title": "Surface parity",
            "sources": [asset["id"]],
            "scenes": [
                {
                    "id": "scene-1",
                    "duration": 0.5,
                    "source_asset_id": asset["id"],
                    "title": {"en": "One visible result"},
                    "body": {"en": "Human and tool surfaces retain the same authority boundary."},
                    "fit": "contain",
                }
            ],
            "tracks": [{"locale": "en", "title": "Parity", "narration": "A silent parity fixture."}],
            "output_profiles": [{"name": "square", "width": 320, "height": 320, "fps": 24}],
        }
        revision_response = client.post("/api/v2/projects", headers=human_headers, json={"document": document})
        assert revision_response.status_code == 201, revision_response.text
        revision = revision_response.json()
        agent_id = "surface-parity-agent"
        agent_response = client.post(
            "/api/v2/agents",
            headers=human_headers,
            json={
                "agent_id": agent_id,
                "label": "Surface parity tool",
                "project_id": revision["project_id"],
                "revision": revision["revision"],
                "source_ids": [asset["id"]],
            },
        )
        assert agent_response.status_code == 201, agent_response.text
        agent_headers = {
            **BASE_HEADERS,
            "authorization": f"Bearer {agent_response.json()['client_token']}",
        }

        catalogue_response = client.get("/api/v2/tools/catalog", headers=agent_headers)
        assert catalogue_response.status_code == 200, catalogue_response.text
        catalogue = catalogue_response.json()["tools"]
        assert [item["name"] for item in catalogue] == TOOL_NAMES
        assert next(item for item in catalogue if item["name"] == "quantech_stage_render")[
            "input_schema"
        ]["additionalProperties"] is False

        plan_input = {
            "project_id": revision["project_id"],
            "revision": revision["revision"],
            "locale": "en",
            "profile": "square",
            "narration_mode": "silent",
            "max_duration_seconds": 5.25,
            "max_output_bytes": 12_345_678,
        }
        api_plan_response = client.post("/api/v2/render-plans", headers=human_headers, json=plan_input)
        assert api_plan_response.status_code == 201, api_plan_response.text
        api_plan = api_plan_response.json()
        tool_plan_response = client.post(
            "/api/v2/tools/quantech_stage_render", headers=agent_headers, json=plan_input
        )
        assert tool_plan_response.status_code == 200, tool_plan_response.text
        tool_result = tool_plan_response.json()
        assert tool_result["ok"] is True and tool_result["error"] is None
        assert tool_result["result"]["effect"] == "plan_prepared_only"
        assert tool_result["result"]["authorization"] == "required"
        tool_plan = tool_result["result"]["plan"]

        assert tool_plan == api_plan
        assert api_plan["project_id"] == revision["project_id"]
        assert api_plan["revision"] == revision["revision"] == 1
        assert api_plan["project_hash"] == revision["document_hash"]
        assert api_plan["asset_hashes"] == {asset["id"]: asset["sha256"]}
        assert api_plan.get("original_hashes", {}) == {}
        assert api_plan["limits"] == {
            "max_duration_seconds": 5.25,
            "max_output_bytes": 12_345_678,
        }
        unsigned_plan = {
            key: value for key, value in api_plan.items() if key not in {"id", "plan_hash", "created_at"}
        }
        assert canonical_hash(unsigned_plan) == api_plan["plan_hash"] == tool_plan["plan_hash"]

        with sqlite3.connect(settings.data_dir / "production.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM render_plans").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM production_grants").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM production_jobs").fetchone()[0] == 0

        authorization = client.post(
            f"/api/v2/render-plans/{api_plan['id']}/authorize",
            headers=human_headers,
            json={
                "agent_id": agent_id,
                "project_id": revision["project_id"],
                "revision": revision["revision"],
                "expires_in_seconds": 300,
            },
        )
        assert authorization.status_code == 201, authorization.text

        idempotency_key = "surface-parity-render-0001"
        api_job_response = client.post(
            "/api/v2/renders/run-approved",
            headers={**agent_headers, "idempotency-key": idempotency_key},
            json={"plan_id": api_plan["id"]},
        )
        assert api_job_response.status_code == 202, api_job_response.text
        api_job = api_job_response.json()
        assert api_job["status"] == "queued"

        tool_job_response = client.post(
            "/api/v2/tools/quantech_run_approved_render",
            headers=agent_headers,
            json={
                "project_id": revision["project_id"],
                "revision": revision["revision"],
                "plan_id": api_plan["id"],
                "idempotency_key": idempotency_key,
            },
        )
        assert tool_job_response.status_code == 200, tool_job_response.text
        replay = tool_job_response.json()
        assert replay["ok"] is True and replay["error"] is None
        assert replay["result"]["effect"] == "idempotent_replay"
        assert replay["result"]["job"] == api_job
        assert schedule_calls == ["scheduled"]
        assert render_calls == []

        with sqlite3.connect(settings.data_dir / "production.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM render_plans").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM production_grants").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM production_jobs").fetchone()[0] == 1
            consumed = db.execute("SELECT consumed_at FROM production_grants").fetchone()[0]
            assert consumed is not None
