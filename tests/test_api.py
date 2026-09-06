from __future__ import annotations

import json
import io
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from quantech_vid.api import APIConfig, create_app
from quantech_vid.config import Settings
from quantech_vid.production_store import now_iso, token_hash


ORIGIN = "http://127.0.0.1:7476"
BASE_HEADERS = {"host": "127.0.0.1:7476", "origin": ORIGIN}


def test_api_module_import_has_no_runtime_side_effect(tmp_path: Path) -> None:
    data_dir = tmp_path / "must-not-exist"
    environment = os.environ.copy()
    environment["QUANTECH_VID_DATA_DIR"] = str(data_dir)
    result = subprocess.run([sys.executable, "-c", "import quantech_vid.api"], cwd=Path(__file__).parents[1],
                            env=environment, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert not data_dir.exists()


@pytest.fixture()
def api(tmp_path: Path):
    source = tmp_path / "chosen.png"
    Image.new("RGB", (640, 640), "#287868").save(source)
    legacy_dir = tmp_path / "projects" / "legacy-demo"
    legacy_dir.mkdir(parents=True)
    Image.new("RGB", (640, 360), "#234567").save(legacy_dir / "legacy.png")
    (legacy_dir / "project.json").write_text(json.dumps({
        "schema_version": "1.0", "slug": "legacy-demo", "title": "Legacy demo",
        "locales": [{"locale": "en", "title": "Legacy", "narration": "Legacy narration"}],
        "profiles": [{"name": "square", "width": 320, "height": 320, "fps": 12}],
        "scenes": [{"id": "legacy-1", "duration": 1, "title_fr": "Ancien", "body_fr": "",
                    "title_en": "Legacy", "body_en": "", "asset": "legacy.png", "fit": "cover"}]
    }), encoding="utf-8")
    studio = tmp_path / "studio"
    studio.mkdir()
    (studio / "index.html").write_text("<!doctype html><title>Studio fixture</title>", encoding="utf-8")
    data = tmp_path / "runtime"
    settings = Settings(root=tmp_path, data_dir=data, host="127.0.0.1", port=7476,
        allowed_asset_roots=(tmp_path,), tts_model="disabled", tts_voice_fr="disabled",
        tts_voice_en="disabled", max_workers=1)
    settings.ensure_directories()
    app = create_app(settings, APIConfig(expected_host="127.0.0.1:7476", allowed_origin=ORIGIN,
        pairing_code="operator-code-2026", signing_key=b"test-signing-key-32-bytes-long!!",
        selections={"selection-token-0001": source}, background_jobs=False))
    return TestClient(app), app, tmp_path


def pair(client: TestClient, actor_id: str = "teacher") -> tuple[dict, dict]:
    response = client.post("/api/v2/pair", headers=BASE_HEADERS,
        json={"operator_code": "operator-code-2026", "actor_id": actor_id})
    assert response.status_code == 201, response.text
    body = response.json()
    headers = {**BASE_HEADERS, "authorization": f"Bearer {body['session_token']}", "x-csrf-token": body["csrf_token"]}
    return body, headers


def setup_project(client: TestClient, human_headers: dict) -> tuple[str, dict, str]:
    admitted = client.post("/api/v2/sources/admit", headers=human_headers, json={
        "selection_token": "selection-token-0001",
        "provenance": {"origin": "fixture generated locally", "collected_by": "test operator"},
        "rights": {"basis": "owned", "reference": "test fixture declaration"},
        "allowed_operations": ["render"],
    })
    assert admitted.status_code == 201, admitted.text
    source = admitted.json()
    document = {"schema_version": "2.0", "slug": "secure-demo", "title": "Secure demo",
        "sources": [source["id"]],
        "scenes": [{"id": "scene-1", "duration": 0.5, "source_asset_id": source["id"],
                    "title": {"fr": "Démo sûre", "en": "Safe demo"},
                    "body": {"fr": "Révision humaine", "en": "Human review"}, "fit": "cover"}],
        "tracks": [{"locale": "en", "title": "Safe demo", "narration": "A short silent test."}],
        "output_profiles": [{"name": "square", "width": 320, "height": 320, "fps": 24}]}
    created = client.post("/api/v2/projects", headers=human_headers, json={"document": document})
    assert created.status_code == 201, created.text
    return created.json()["project_id"], document, source["id"]


def create_agent(client: TestClient, headers: dict, project_id: str, agent_id: str = "codex-client",
                 revision: int = 1, source_ids: list[str] | None = None) -> tuple[str, dict]:
    response = client.post("/api/v2/agents", headers=headers, json={
        "agent_id": agent_id, "label": "Local agent", "project_id": project_id,
        "revision": revision, "source_ids": source_ids or [],
    })
    assert response.status_code == 201, response.text
    token = response.json()["client_token"]
    return agent_id, {**BASE_HEADERS, "authorization": f"Bearer {token}"}


def test_agent_cannot_bypass_proposal_review_through_raw_project_routes(api) -> None:
    client, _, _ = api
    _, human_headers = pair(client)
    project_id, document, _ = setup_project(client, human_headers)
    _, agent_headers = create_agent(client, human_headers, project_id)
    assert client.post("/api/v2/projects", headers=agent_headers, json={"document": document}).status_code == 403
    document["title"] = "Unreviewed agent mutation"
    assert client.put(f"/api/v2/projects/{project_id}", headers=agent_headers,
                      json={"base_revision": 1, "document": document}).status_code == 403
    current = client.get(f"/api/v2/projects/{project_id}", headers=human_headers).json()
    assert current["revision"] == 1 and current["document"]["title"] == "Secure demo"


def test_agent_scope_is_enforced_for_same_owner_projects_revisions_sources_and_plans(api) -> None:
    client, app, _ = api
    _, human_headers = pair(client)
    project_id, document, source_id = setup_project(client, human_headers)
    other_project_id, _, other_source_id = setup_project(client, human_headers)
    agent_id, agent_headers = create_agent(client, human_headers, project_id)

    projects = client.get("/api/v2/projects", headers=agent_headers)
    assert projects.status_code == 200
    assert [(item["project_id"], item["revision"]) for item in projects.json()["projects"]] == [(project_id, 1)]
    sources = client.get("/api/v2/sources", headers=agent_headers)
    assert [item["id"] for item in sources.json()["sources"]] == [source_id]
    assert client.get(f"/api/v2/projects/{other_project_id}", headers=agent_headers).status_code == 404
    forged = client.post("/api/v2/tools/quantech_stage_source_import", headers=agent_headers, json={
        "project_id": project_id, "revision": 1, "source_asset_id": other_source_id,
    })
    assert forged.status_code == 200 and forged.json()["error"]["code"] == "SOURCE_NOT_FOUND"
    wrong_project = client.post("/api/v2/tools/quantech_inspect_project", headers=agent_headers, json={
        "project_id": other_project_id, "revision": 1,
    })
    assert wrong_project.json()["error"]["code"] == "PROJECT_REVISION_NOT_FOUND"
    assert client.post("/api/v2/agents", headers=human_headers, json={
        "agent_id": "forged-source-agent", "label": "forged", "project_id": project_id,
        "revision": 1, "source_ids": ["src_" + "f" * 32],
    }).status_code == 404

    revised = client.put(f"/api/v2/projects/{project_id}", headers=human_headers,
        json={"base_revision": 1, "document": {**document, "title": "Scope revision 2"}})
    assert revised.status_code == 200
    assert client.get(f"/api/v2/projects/{project_id}?revision=2", headers=agent_headers).status_code == 404
    assert client.get(f"/api/v2/projects/{project_id}", headers=agent_headers).json()["revision"] == 1
    stale = client.post("/api/v2/render-plans", headers=agent_headers, json={
        "project_id": project_id, "revision": 1, "locale": "en", "profile": "square",
    })
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_PROJECT_REVISION"

    new_agent_id, new_headers = create_agent(client, human_headers, project_id, "revision-two-agent", revision=2)
    plan = client.post("/api/v2/render-plans", headers=new_headers, json={
        "project_id": project_id, "revision": 2, "locale": "en", "profile": "square",
    }).json()
    mismatched = client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=human_headers,
        json={"agent_id": new_agent_id, "project_id": project_id, "revision": 1, "expires_in_seconds": 300})
    assert mismatched.status_code == 409 and mismatched.json()["error"]["code"] == "PLAN_BINDING_MISMATCH"
    old_scope = client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=human_headers,
        json={"agent_id": agent_id, "project_id": project_id, "revision": 2, "expires_in_seconds": 300})
    assert old_scope.status_code == 403 and old_scope.json()["error"]["code"] == "AGENT_SCOPE_MISMATCH"
    approved = client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=human_headers,
        json={"agent_id": new_agent_id, "project_id": project_id, "revision": 2, "expires_in_seconds": 300})
    assert approved.status_code == 201

    _, source_headers = create_agent(client, human_headers, project_id, "explicit-source-agent",
                                     revision=2, source_ids=[other_source_id])
    proposed = client.post("/api/v2/tools/quantech_stage_source_import", headers=source_headers, json={
        "project_id": project_id, "revision": 2, "source_asset_id": other_source_id,
    }).json()
    assert proposed["ok"] is True and proposed["result"]["effect"] == "proposal_only"
    assert client.delete(f"/api/v2/agents/{agent_id}", headers=human_headers).status_code == 204
    assert client.get(f"/api/v2/projects/{project_id}", headers=agent_headers).status_code == 401
    assert app.state.production_store.get_revision(
        {"id": "teacher", "kind": "human", "owner_id": None}, project_id, 1).revision == 1


def test_historical_agent_session_without_persisted_scope_fails_closed(api) -> None:
    client, app, _ = api
    _, human_headers = pair(client)
    setup_project(client, human_headers)
    raw_token = "historical-agent-token-without-scope"
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with app.state.production_store._connect() as db:
        db.execute("INSERT INTO actors VALUES(?,?,?,?,?)",
                   ("historical-agent", "agent", "teacher", "Historical", now_iso()))
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,NULL)",
                   (token_hash(raw_token), "historical-agent", None, expiry))
    response = client.get("/api/v2/projects", headers={
        **BASE_HEADERS, "authorization": f"Bearer {raw_token}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AGENT_SCOPE_REQUIRED"


def test_health_is_sanitized_and_legacy_bypasses_fail_closed(api) -> None:
    client, _, tmp_path = api
    health = client.get("/api/v2/health", headers={"host": "127.0.0.1:7476"})
    assert health.status_code == 200
    encoded = json.dumps(health.json())
    assert str(tmp_path) not in encoded and "operator-code" not in encoded
    assert health.json()["capabilities"]["network_import"] is False
    assert client.post("/api/v1/renders", json={"manifest_path": "anything"}).status_code == 410
    assert client.post("/api/v1/narrations", json={"text": "paid"}).status_code == 410
    assert client.post("/api/plugin/generate-token").status_code == 410
    page = client.get("/")
    assert page.status_code == 200
    assert "default-src 'self'" in page.headers["content-security-policy"]


def test_pairing_is_one_time_and_boundary_is_exact(api) -> None:
    client, _, _ = api
    assert client.get("/api/v2/health", headers={"host": "evil.test"}).json()["error"]["code"] == "HOST_REJECTED"
    assert client.post("/api/v2/pair", headers={"host": "127.0.0.1:7476", "origin": "http://evil.test"},
                       json={"operator_code": "operator-code-2026", "actor_id": "teacher"}).status_code == 403
    _, headers = pair(client)
    assert client.post("/api/v2/pair", headers=BASE_HEADERS,
                       json={"operator_code": "operator-code-2026", "actor_id": "second"}).status_code == 403
    no_csrf = {k: v for k, v in headers.items() if k != "x-csrf-token"}
    assert client.post("/api/v2/agents", headers=no_csrf, json={"agent_id": "agent-x", "label": "x"}).status_code == 403
    assert client.post("/api/v2/agents", headers=headers,
                       json={"agent_id": "agent-x", "label": "x"}).status_code == 422
    oversized = client.post("/api/v2/agents", headers={**headers, "content-length": "1000001"}, content=b"{}")
    assert oversized.status_code == 413


def test_source_admission_is_opaque_and_url_import_disabled(api) -> None:
    client, _, tmp_path = api
    _, headers = pair(client)
    rejected = client.post("/api/v2/sources/admit", headers=headers, json={
        "selection_token": "../../private-path", "provenance": {"origin": "x", "collected_by": "x"},
        "rights": {"basis": "owned", "reference": "x"}, "allowed_operations": ["render"]})
    assert rejected.status_code == 404
    project_id, _, _ = setup_project(client, headers)
    _, agent_headers = create_agent(client, headers, project_id)
    disabled = client.post("/api/v2/sources/import-url", headers=agent_headers, json={"url": "http://127.0.0.1/private"})
    assert disabled.status_code == 501 and disabled.json()["error"]["code"] == "NETWORK_IMPORT_DISABLED"
    assert str(tmp_path) not in json.dumps(disabled.json())


def test_sample_source_is_safe_and_session_is_revocable(api) -> None:
    client, app, tmp_path = api
    _, headers = pair(client)
    project_id, _, _ = setup_project(client, headers)
    _, agent_headers = create_agent(client, headers, project_id)
    sample = client.post("/api/v2/sources/sample", headers=headers)
    assert sample.status_code == 201
    body = sample.json()
    assert body["media_type"] == "image/png" and body["rights"]["basis"] == "owned"
    assert str(tmp_path) not in json.dumps(body)
    assert client.delete("/api/v2/session", headers=headers).status_code == 204
    assert client.post("/api/v2/sources/sample", headers=headers).status_code == 401
    assert client.get("/api/v2/sources", headers=agent_headers).status_code == 401
    # Restart never reuses a consumed operator code. Local issuance is a separate action.
    recovered = TestClient(create_app(app.state.production_service.settings,
        APIConfig(expected_host="127.0.0.1:7476", allowed_origin=ORIGIN, pairing_code="operator-code-2026",
                  signing_key=b"test-signing-key-32-bytes-long!!", background_jobs=False)))
    assert recovered.post("/api/v2/pair", headers=BASE_HEADERS,
        json={"operator_code": "operator-code-2026", "actor_id": "teacher"}).status_code == 403
    fresh_code = recovered.app.state.production_store.issue_pairing_code()
    assert recovered.post("/api/v2/pair", headers=BASE_HEADERS,
        json={"operator_code": fresh_code, "actor_id": "teacher"}).status_code == 201


def test_reopening_browser_can_repair_without_waiting_for_the_old_session(api) -> None:
    client, app, _ = api
    _, headers = pair(client)
    project_id, _, _ = setup_project(client, headers)
    fresh_code = app.state.production_store.issue_pairing_code()
    recovered = client.post("/api/v2/pair", headers=BASE_HEADERS,
        json={"operator_code": fresh_code, "actor_id": "teacher"})
    assert recovered.status_code == 201
    recovered_headers = {**BASE_HEADERS, "authorization": "Bearer " + recovered.json()["session_token"]}
    assert client.get(f"/api/v2/projects/{project_id}", headers=recovered_headers).json()["revision"] == 1
    assert client.post("/api/v2/pair", headers=BASE_HEADERS,
        json={"operator_code": fresh_code, "actor_id": "teacher"}).status_code == 403


def test_pairing_code_expires_and_has_a_bounded_guess_budget(api) -> None:
    client, app, _ = api
    for _ in range(10):
        assert client.post("/api/v2/pair", headers=BASE_HEADERS,
            json={"operator_code": "wrong-code-00000000", "actor_id": "teacher"}).status_code == 403
    assert client.post("/api/v2/pair", headers=BASE_HEADERS,
        json={"operator_code": "operator-code-2026", "actor_id": "teacher"}).status_code == 403
    store = app.state.production_store
    fresh_code = store.issue_pairing_code()
    with store._connect() as db:
        db.execute("UPDATE security_state SET pairing_expires_at=? WHERE id=1",
                   ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    assert client.post("/api/v2/pair", headers=BASE_HEADERS,
        json={"operator_code": fresh_code, "actor_id": "teacher"}).status_code == 403


def test_browser_upload_is_streamed_derived_and_bounded(api) -> None:
    client, _, tmp_path = api
    _, headers = pair(client)
    upload_headers = {**headers, "x-file-name": quote("résumé.png"), "x-rights-basis": "owned",
                      "x-rights-reference": quote("déclaration opérateur"), "x-source-origin": quote("sélecteur navigateur")}
    buffer = io.BytesIO(); Image.new("RGB", (80, 60), "red").save(buffer, format="PNG")
    response = client.post("/api/v2/sources/upload", headers=upload_headers, content=buffer.getvalue())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["asset"]["media_type"] == "image/png" and body["derived"] is True
    assert body["original"]["name"] == "résumé.png" and str(tmp_path) not in json.dumps(body)
    sources = client.get("/api/v2/sources", headers=headers).json()["sources"]
    assert [item["id"] for item in sources] == [body["asset"]["id"]]
    text_headers = {**upload_headers, "x-file-name": quote("leçon 100%.txt")}
    text = client.post("/api/v2/sources/upload", headers=text_headers, content="Résumé vérifié".encode())
    assert text.status_code == 201 and text.json()["original"]["media_type"] == "text/plain"
    assert text.json()["original"]["name"] == "leçon 100%.txt"
    assert client.post("/api/v2/sources/upload", headers={**upload_headers, "x-file-name": quote("../escape.png", safe="")},
                       content=buffer.getvalue()).status_code == 422
    assert client.post("/api/v2/sources/upload", headers={**upload_headers, "x-file-name": "fake.png"},
                       content=b"not an image").status_code == 422
    too_large = client.post("/api/v2/sources/upload", headers={**upload_headers, "content-length": "50000001"}, content=b"x")
    assert too_large.status_code == 413
    malformed = client.post("/api/v2/sources/upload", headers={**upload_headers, "x-file-name": "bad%GG.png"},
                            content=buffer.getvalue())
    assert malformed.status_code == 422


def test_v1_migration_is_by_copy_and_original_is_untouched(api) -> None:
    client, _, tmp_path = api
    _, headers = pair(client)
    manifest = tmp_path / "projects" / "legacy-demo" / "project.json"
    before = manifest.read_bytes()
    listed = client.get("/api/v2/legacy-projects", headers=headers)
    assert listed.status_code == 200 and [item["slug"] for item in listed.json()["projects"]] == ["legacy-demo"]
    migrated = client.post("/api/v2/legacy-projects/legacy-demo/import", headers=headers, json={
        "provenance": {"origin": "bundled v1 project", "collected_by": "teacher"},
        "rights": {"basis": "owned", "reference": "operator declaration"}})
    assert migrated.status_code == 201, migrated.text
    body = migrated.json()
    assert body["migration"] == "copied" and body["project"]["revision"] == 1
    assert manifest.read_bytes() == before and str(tmp_path) not in json.dumps(body)
    assert client.post("/api/v2/legacy-projects/..%2Foutside/import", headers=headers, json={
        "provenance": {"origin": "x", "collected_by": "x"},
        "rights": {"basis": "owned", "reference": "x"}}).status_code in {404, 410}


def test_approved_silent_render_end_to_end_with_idempotency(api) -> None:
    client, app, tmp_path = api
    _, human_headers = pair(client)
    project_id, _, _ = setup_project(client, human_headers)
    agent_id, agent_headers = create_agent(client, human_headers, project_id)
    plan_response = client.post("/api/v2/render-plans", headers=agent_headers, json={
        "project_id": project_id, "revision": 1, "locale": "en", "profile": "square",
        "narration_mode": "silent", "max_duration_seconds": 5, "max_output_bytes": 50_000_000})
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    denied = client.post("/api/v2/renders/run-approved", headers={**agent_headers, "idempotency-key": "request-key-00000001"},
                         json={"plan_id": plan["id"]})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "APPROVAL_REQUIRED"
    approved = client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=human_headers,
        json={"agent_id": agent_id, "project_id": project_id, "revision": 1, "expires_in_seconds": 300})
    assert approved.status_code == 201
    submit_headers = {**agent_headers, "idempotency-key": "request-key-00000001"}
    first = client.post("/api/v2/renders/run-approved", headers=submit_headers, json={"plan_id": plan["id"]})
    second = client.post("/api/v2/renders/run-approved", headers=submit_headers, json={"plan_id": plan["id"]})
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    app.state.production_service.drain()
    result = client.get(f"/api/v2/renders/{first.json()['id']}", headers=agent_headers)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "complete" and {r["role"] for r in body["receipts"]} >= {"video-mp4", "video-webm", "provenance", "quality-report"}
    assert all(len(item["sha256"]) == 64 and item["size"] > 0 for item in body["receipts"])
    public = json.dumps(body)
    assert str(tmp_path) not in public and "session_token" not in public and "internal_path" not in public
    video = next(item for item in body["receipts"] if item["role"] == "video-mp4")
    ranged = client.get(f"/api/v2/renders/{body['id']}/artifacts/{video['name']}",
                        headers={**agent_headers, "range": "bytes=0-1023"})
    assert ranged.status_code == 206 and len(ranged.content) == min(1024, video["size"]), ranged.text
    assert "token" not in ranged.headers.get("content-disposition", "").lower()
    _, other_headers = create_agent(client, human_headers, project_id, "artifact-intruder")
    assert client.get(f"/api/v2/renders/{body['id']}/artifacts/{video['name']}", headers=other_headers).status_code == 404
    assert client.get(f"/api/v2/renders/{body['id']}/artifacts/..%2Fsecret", headers=agent_headers).status_code == 404
    _, internal = app.state.production_store.job_internal(body["id"])
    qa_path = next(Path(path) for path in internal if path.endswith("-qa.json"))
    qa_path.write_text("{}", encoding="utf-8")
    assert client.get(f"/api/v2/renders/{body['id']}/artifacts/{video['name']}", headers=agent_headers).status_code == 409


def test_wrong_agent_revision_and_idempotency_conflict(api) -> None:
    client, _, _ = api
    _, human_headers = pair(client)
    project_id, document, _ = setup_project(client, human_headers)
    agent_id, agent_headers = create_agent(client, human_headers, project_id)
    _, other_headers = create_agent(client, human_headers, project_id, "other-agent")
    plan = client.post("/api/v2/render-plans", headers=agent_headers,
        json={"project_id": project_id, "revision": 1, "locale": "en", "profile": "square"}).json()
    client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=human_headers,
                json={"agent_id": agent_id, "project_id": project_id, "revision": 1, "expires_in_seconds": 300})
    wrong = client.post("/api/v2/renders/run-approved", headers={**other_headers, "idempotency-key": "wrong-agent-key-0001"},
                        json={"plan_id": plan["id"]})
    assert wrong.status_code == 403
    assert client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=other_headers,
                       json={"agent_id": agent_id, "project_id": project_id, "revision": 1,
                             "expires_in_seconds": 300}).status_code == 403
    assert client.post("/api/v2/renders/run-approved",
        headers={**human_headers, "idempotency-key": "human-cannot-run-01"}, json={"plan_id": plan["id"]}).status_code == 403
    assert client.post("/api/v2/sources/upload", headers={**other_headers, "x-file-name": "x.png",
        "x-rights-basis": "owned", "x-rights-reference": "x"}, content=b"x").status_code == 403
    revised = client.put(f"/api/v2/projects/{project_id}", headers=human_headers,
                         json={"base_revision": 1, "document": {**document, "title": "Revision 2"}})
    assert revised.status_code == 200
    stale = client.post("/api/v2/renders/run-approved", headers={**agent_headers, "idempotency-key": "stale-revision-0001"},
                        json={"plan_id": plan["id"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_PROJECT_REVISION"


def _prepared(api):
    client, app, _ = api
    _, human_headers = pair(client)
    project_id, _, _ = setup_project(client, human_headers)
    agent_id, agent_headers = create_agent(client, human_headers, project_id)
    plan = client.post("/api/v2/render-plans", headers=agent_headers,
        json={"project_id": project_id, "revision": 1, "locale": "en", "profile": "square"}).json()
    approval = client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=human_headers,
        json={"agent_id": agent_id, "project_id": project_id, "revision": 1,
              "expires_in_seconds": 300}).json()
    return client, app, human_headers, agent_headers, plan, approval


def test_expired_tampered_and_revoked_approval_are_rejected(api) -> None:
    client, app, human_headers, agent_headers, plan, approval = _prepared(api)
    db_path = app.state.production_store.path
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE production_grants SET expires_at=? WHERE id=?", (expired, approval["grant_id"]))
    response = client.post("/api/v2/renders/run-approved",
        headers={**agent_headers, "idempotency-key": "expired-grant-key-01"}, json={"plan_id": plan["id"]})
    assert response.status_code == 403 and response.json()["error"]["code"] == "APPROVAL_REQUIRED"
    approval2 = client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=human_headers,
        json={"agent_id": "codex-client", "project_id": plan["project_id"], "revision": plan["revision"],
              "expires_in_seconds": 300}).json()
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE production_grants SET signature='tampered' WHERE id=?", (approval2["grant_id"],))
    response = client.post("/api/v2/renders/run-approved",
        headers={**agent_headers, "idempotency-key": "tampered-grant-key-1"}, json={"plan_id": plan["id"]})
    assert response.status_code == 403 and response.json()["error"]["code"] == "APPROVAL_INTEGRITY_FAILED"
    approval3 = client.post(f"/api/v2/render-plans/{plan['id']}/authorize", headers=human_headers,
        json={"agent_id": "codex-client", "project_id": plan["project_id"], "revision": plan["revision"],
              "expires_in_seconds": 300}).json()
    assert client.delete(f"/api/v2/production-grants/{approval3['grant_id']}", headers=human_headers).status_code == 204
    response = client.post("/api/v2/renders/run-approved",
        headers={**agent_headers, "idempotency-key": "revoked-grant-key-01"}, json={"plan_id": plan["id"]})
    assert response.status_code == 403


def test_idempotency_conflict_cancel_race_and_restart_recovery(api) -> None:
    client, app, _, agent_headers, plan, _ = _prepared(api)
    first = client.post("/api/v2/renders/run-approved",
        headers={**agent_headers, "idempotency-key": "conflict-key-000001"}, json={"plan_id": plan["id"]})
    assert first.status_code == 202
    conflict = client.post("/api/v2/renders/run-approved",
        headers={**agent_headers, "idempotency-key": "conflict-key-000001"}, json={"plan_id": "plan_different"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    started = threading.Event()
    def cancellable(*args, **kwargs):
        started.set()
        while not kwargs["cancelled"]():
            time.sleep(0.01)
        raise InterruptedError
    app.state.production_service.render_fn = cancellable
    worker = threading.Thread(target=app.state.production_service.drain)
    worker.start(); assert started.wait(2)
    cancellation = client.post(f"/api/v2/renders/{first.json()['id']}/cancel", headers=agent_headers)
    assert cancellation.status_code == 200
    worker.join(3); assert not worker.is_alive()
    final = client.get(f"/api/v2/renders/{first.json()['id']}", headers=agent_headers).json()
    assert final["status"] == "cancelled"

    # A process restart must never silently resume an ambiguous running render.
    with sqlite3.connect(app.state.production_store.path) as db:
        db.execute("UPDATE production_jobs SET status='running' WHERE id=?", (first.json()["id"],))
    assert app.state.production_store.recover_interrupted() == 1
    recovered = client.get(f"/api/v2/renders/{first.json()['id']}", headers=agent_headers).json()
    assert recovered["status"] == "failed" and recovered["error_code"] == "RESTART_INTERRUPTED"
