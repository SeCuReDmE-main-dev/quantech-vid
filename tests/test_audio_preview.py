from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from quantech_vid.api import APIConfig, MAX_AUDIO_PREVIEW_BYTES, create_app
from quantech_vid.config import Settings


ORIGIN = "http://127.0.0.1:7476"
BASE_HEADERS = {"host": "127.0.0.1:7476", "origin": ORIGIN}


def wav_bytes(frames: int = 16_000) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x01\x00" * frames)
    return stream.getvalue()


@pytest.fixture()
def audio_api(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    settings = Settings(
        root=root,
        data_dir=root / "runtime",
        host="127.0.0.1",
        port=7476,
        allowed_asset_roots=(root,),
        tts_model="disabled",
        tts_voice_fr="disabled",
        tts_voice_en="disabled",
        max_workers=1,
    )
    app = create_app(settings, APIConfig(
        expected_host="127.0.0.1:7476",
        allowed_origin=ORIGIN,
        pairing_code="audio-preview-code-2026",
        signing_key=b"audio-preview-signing-key-32!!",
        background_jobs=False,
    ))
    client = TestClient(app)
    paired = client.post("/api/v2/pair", headers=BASE_HEADERS, json={
        "operator_code": "audio-preview-code-2026", "actor_id": "audio-owner",
    })
    assert paired.status_code == 201, paired.text
    headers = {
        **BASE_HEADERS,
        "authorization": f"Bearer {paired.json()['session_token']}",
        "x-csrf-token": paired.json()["csrf_token"],
    }
    payload = wav_bytes()
    uploaded = client.post("/api/v2/sources/upload", headers={
        **headers,
        "x-file-name": quote("review.wav"),
        "x-rights-basis": "owned",
        "x-rights-reference": quote("Synthetic non-personal fixture"),
    }, content=payload)
    assert uploaded.status_code == 201, uploaded.text
    return client, app, headers, uploaded.json()["asset"], payload


def create_agent(client: TestClient, headers: dict, asset: dict) -> dict:
    revision = client.post("/api/v2/projects", headers=headers, json={"document": {
        "schema_version": "2.0",
        "slug": "audio-preview",
        "title": "Audio preview",
        "sources": [asset["id"]],
        "scenes": [{
            "id": "scene-1",
            "duration": 1,
            "source_asset_id": asset["id"],
            "title": {"en": "Review"},
            "fit": "contain",
        }],
        "tracks": [{"locale": "en", "title": "English", "narration": "Manual review."}],
        "output_profiles": [{"name": "square", "width": 320, "height": 320, "fps": 12}],
    }})
    assert revision.status_code == 201, revision.text
    registered = client.post("/api/v2/agents", headers=headers, json={
        "agent_id": "audio-preview-agent",
        "label": "Audio preview denied agent",
        "project_id": revision.json()["project_id"],
        "revision": 1,
        "source_ids": [asset["id"]],
    })
    assert registered.status_code == 201, registered.text
    return {**BASE_HEADERS, "authorization": f"Bearer {registered.json()['client_token']}"}


def test_audio_preview_is_explicit_human_owner_and_analyze_scoped(audio_api) -> None:
    client, app, headers, asset, payload = audio_api
    route = f"/api/v2/sources/{asset['id']}/audio-preview"

    response = client.get(route, headers=headers)
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(route, headers=BASE_HEADERS).status_code == 401
    malformed = client.get("/api/v2/sources/not-an-id/audio-preview", headers=headers)
    assert malformed.status_code == 404 and malformed.json()["error"]["code"] == "SOURCE_NOT_FOUND"

    agent_headers = create_agent(client, headers, asset)
    agent = client.get(route, headers=agent_headers)
    assert agent.status_code == 403 and agent.json()["error"]["code"] == "HUMAN_SESSION_REQUIRED"

    other_code = app.state.production_store.issue_pairing_code()
    other = client.post("/api/v2/pair", headers=BASE_HEADERS, json={
        "operator_code": other_code, "actor_id": "other-audio-owner",
    })
    other_headers = {**BASE_HEADERS, "authorization": f"Bearer {other.json()['session_token']}"}
    hidden = client.get(route, headers=other_headers)
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "SOURCE_NOT_FOUND"

    with app.state.production_store._connect() as db:
        db.execute("UPDATE source_assets SET operations_json='[\"render\"]' WHERE id=?", (asset["id"],))
    forbidden = client.get(route, headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "SOURCE_OPERATION_FORBIDDEN"


def test_audio_preview_refuses_original_tamper_without_returning_bytes(audio_api) -> None:
    client, app, headers, asset, _ = audio_api
    with app.state.production_store._connect() as db:
        original = Path(db.execute(
            "SELECT internal_path FROM source_originals WHERE source_id=?", (asset["id"],)
        ).fetchone()[0])
    original.write_bytes(original.read_bytes()[:-1] + b"x")
    response = client.get(f"/api/v2/sources/{asset['id']}/audio-preview", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_ORIGINAL_INTEGRITY_FAILED"
    assert str(original) not in response.text


def test_audio_preview_refuses_consistent_but_oversized_original(audio_api) -> None:
    client, app, headers, asset, _ = audio_api
    oversized = wav_bytes(4_800_001)
    assert len(oversized) == MAX_AUDIO_PREVIEW_BYTES + 2
    digest = hashlib.sha256(oversized).hexdigest()
    with app.state.production_store._connect() as db:
        original = Path(db.execute(
            "SELECT internal_path FROM source_originals WHERE source_id=?", (asset["id"],)
        ).fetchone()[0])
        provenance = json.loads(db.execute(
            "SELECT provenance_json FROM source_assets WHERE id=?", (asset["id"],)
        ).fetchone()[0])
        original.write_bytes(oversized)
        provenance["original"]["size"] = len(oversized)
        provenance["original"]["sha256"] = digest
        db.execute("UPDATE source_originals SET size=?,sha256=? WHERE source_id=?",
                   (len(oversized), digest, asset["id"]))
        db.execute("UPDATE source_assets SET provenance_json=? WHERE id=?",
                   (json.dumps(provenance), asset["id"]))
    response = client.get(f"/api/v2/sources/{asset['id']}/audio-preview", headers=headers)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "AUDIO_PREVIEW_TOO_LARGE"
    assert str(original) not in response.text
