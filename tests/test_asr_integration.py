import hashlib
import io
import sqlite3
import wave
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient
from PIL import Image

from quantech_vid.api import APIConfig, create_app
from quantech_vid.config import Settings
from quantech_vid.local_asr import LocalAsrResult


ORIGIN = "http://127.0.0.1:7476"
BASE = {"host": "127.0.0.1:7476", "origin": ORIGIN}


def wav_bytes(*, seconds: float = 1, zero: bool = False) -> bytes:
    frames = round(16_000 * seconds)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16_000)
        output.writeframes((b"\x00\x00" if zero else b"\x01\x00") * frames)
    return stream.getvalue()


class FakePilot:
    def __init__(self):
        self.calls = []

    def transcribe(self, source_id, source_hash, wav_path, job_dir, **kwargs):
        self.calls.append((source_id, source_hash, Path(wav_path), Path(job_dir), kwargs))
        size = Path(wav_path).stat().st_size
        return LocalAsrResult(receipt={
            "audio": {"duration_ms": 1000, "size": size},
            "segments": [{"id": "asrseg_0000", "start_ms": 0, "end_ms": 750,
                          "text": "machine draft"}],
        }, binding_sha256="b" * 64)


def fixture(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    settings = Settings(
        root=root, data_dir=root / "runtime", host="127.0.0.1", port=7476,
        allowed_asset_roots=(root,), tts_model="disabled", tts_voice_fr="disabled",
        tts_voice_en="disabled", max_workers=1,
        local_asr_runtime_root=tmp_path / "isolated-asr",
    )
    app = create_app(settings, APIConfig(
        expected_host="127.0.0.1:7476", allowed_origin=ORIGIN,
        pairing_code="operator-code-2026", signing_key=b"asr-test-signing-key-32-bytes!!",
        background_jobs=False,
    ))
    client = TestClient(app)
    paired = client.post("/api/v2/pair", headers=BASE, json={
        "operator_code": "operator-code-2026", "actor_id": "operator",
    }).json()
    headers = {**BASE, "authorization": f"Bearer {paired['session_token']}",
               "x-csrf-token": paired["csrf_token"]}
    return client, app, headers


def upload(client, headers, payload=None):
    payload = payload if payload is not None else wav_bytes()
    response = client.post("/api/v2/sources/upload", headers={
        **headers, "x-file-name": quote("source.wav"), "x-rights-basis": "owned",
        "x-rights-reference": quote("Synthetic non-personal fixture"),
    }, content=payload)
    assert response.status_code == 201, response.text
    return response.json()


def project(client, headers, asset, *, duration=2):
    response = client.post("/api/v2/projects", headers=headers, json={"document": {
        "schema_version": "2.0", "slug": "asr-fixture", "title": "ASR fixture",
        "sources": [asset["id"]], "scenes": [{"id": "scene-1", "duration": duration,
            "source_asset_id": asset["id"], "title": {"en": "Review"}, "fit": "contain"}],
        "tracks": [{"locale": "en", "title": "English", "narration": "Manual review."}],
        "output_profiles": [{"name": "square", "width": 320, "height": 320, "fps": 12}],
    }})
    assert response.status_code == 201, response.text
    return response.json()


def proposal_body(asset, revision):
    return {"project_id": revision["project_id"], "revision": revision["revision"],
            "locale": "en", "expected_asset_sha256": asset["sha256"],
            "expected_original_sha256": asset["provenance"]["original"]["sha256"],
            "acknowledge_machine_proposal_only": True}


def test_wav_upload_preserves_original_and_serves_only_waveform(tmp_path):
    client, app, headers = fixture(tmp_path)
    payload = wav_bytes()
    body = upload(client, headers, payload)
    asset = body["asset"]
    assert body["original"]["media_type"] == "audio/wav"
    assert asset["provenance"]["original"]["transformation"] == "pcm16-waveform-png-v1"
    original = next((tmp_path / "repo" / "runtime" / "source-originals").glob("*/original.wav"))
    assert original.read_bytes() == payload
    preview = client.get(f"/api/v2/sources/{asset['id']}/preview", headers=headers)
    assert preview.status_code == 200 and preview.content != payload
    assert hashlib.sha256(preview.content).hexdigest() == asset["sha256"]
    with Image.open(io.BytesIO(preview.content)) as image:
        assert image.format == "PNG" and image.size == (1280, 320)
    with app.state.production_store._connect() as db:
        assert all(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
                   for table in ("projects", "render_plans", "production_grants", "production_jobs"))


def test_altered_waveform_refuses_before_lazy_pilot(tmp_path):
    client, app, headers = fixture(tmp_path)
    asset = upload(client, headers)["asset"]
    revision = project(client, headers, asset)
    factory_calls = []
    app.state.local_asr_factory = lambda: factory_calls.append(True)
    with app.state.production_store._connect() as db:
        path = Path(db.execute("SELECT internal_path FROM source_assets WHERE id=?", (asset["id"],)).fetchone()[0])
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    response = client.post(f"/api/v2/sources/{asset['id']}/asr-proposals", headers=headers,
                           json=proposal_body(asset, revision))
    assert response.status_code == 409
    assert factory_calls == []


def test_zero_and_too_long_refuse_before_lazy_pilot(tmp_path):
    client, app, headers = fixture(tmp_path)
    factory_calls = []
    app.state.local_asr_factory = lambda: factory_calls.append(True)
    zero = upload(client, headers, wav_bytes(zero=True))["asset"]
    revision = project(client, headers, zero)
    response = client.post(f"/api/v2/sources/{zero['id']}/asr-proposals", headers=headers,
                           json=proposal_body(zero, revision))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LOCAL_ASR_EXACT_ZERO_ENERGY_REJECTED"
    assert factory_calls == []

    nonzero = upload(client, headers, wav_bytes(seconds=2))["asset"]
    short = project(client, headers, nonzero, duration=1)
    response = client.post(f"/api/v2/sources/{nonzero['id']}/asr-proposals", headers=headers,
                           json=proposal_body(nonzero, short))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LOCAL_ASR_AUDIO_EXCEEDS_PROJECT_TIMELINE"
    assert factory_calls == []


def test_proposal_is_human_scoped_hash_bound_and_never_mutates_canonical_state(tmp_path):
    client, app, headers = fixture(tmp_path)
    uploaded = upload(client, headers); asset = uploaded["asset"]
    revision = project(client, headers, asset)
    pilot = FakePilot(); factory_calls = []
    app.state.local_asr_factory = lambda: (factory_calls.append(True) or pilot)
    with sqlite3.connect(app.state.production_store.path) as db:
        before = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("project_revisions", "render_plans", "production_grants", "production_jobs")}
    response = client.post(f"/api/v2/sources/{asset['id']}/asr-proposals", headers=headers,
                           json=proposal_body(asset, revision))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["effect"] == "proposal_only" and body["segments"][0]["text"] == "machine draft"
    assert body["limitations"] == {"machine_proposal_only": True, "human_review_required": True,
        "speaker_identity_inferred": False, "vad_performed": False,
        "exact_zero_energy_rejected": True, "independent_verification": False}
    assert len(factory_calls) == len(pilot.calls) == 1
    assert pilot.calls[0][4]["timeout"] == 180
    with sqlite3.connect(app.state.production_store.path) as db:
        after = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                 for table in before}
    assert after == before
    stored = client.get(f"/api/v2/projects/{revision['project_id']}", headers=headers).json()
    assert "segments" not in stored["document"]["tracks"][0]

    agent_token = app.state.production_store.create_agent(
        "operator", "asr-agent", "ASR agent denied", revision["project_id"], 1, [asset["id"]]
    )
    denied = client.post(f"/api/v2/sources/{asset['id']}/asr-proposals", headers={
        **BASE, "authorization": f"Bearer {agent_token}"}, json=proposal_body(asset, revision))
    assert denied.status_code == 403 and len(pilot.calls) == 1


def test_proposal_requires_exact_origin_csrf_and_analyze_right(tmp_path):
    client, app, headers = fixture(tmp_path)
    asset = upload(client, headers)["asset"]
    revision = project(client, headers, asset)
    body = proposal_body(asset, revision)
    pilot = FakePilot(); app.state.local_asr_factory = lambda: pilot
    assert client.post(f"/api/v2/sources/{asset['id']}/asr-proposals",
                       headers=BASE, json=body).status_code == 401
    no_csrf = {key: value for key, value in headers.items() if key != "x-csrf-token"}
    assert client.post(f"/api/v2/sources/{asset['id']}/asr-proposals",
                       headers=no_csrf, json=body).status_code == 403
    evil = {**headers, "origin": "http://evil.invalid"}
    assert client.post(f"/api/v2/sources/{asset['id']}/asr-proposals",
                       headers=evil, json=body).status_code == 403
    with app.state.production_store._connect() as db:
        db.execute("UPDATE source_assets SET operations_json='[\"render\"]' WHERE id=?",
                   (asset["id"],))
    forbidden = client.post(f"/api/v2/sources/{asset['id']}/asr-proposals",
                            headers=headers, json=body)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "SOURCE_OPERATION_FORBIDDEN"
    assert pilot.calls == []


def test_wrong_project_hash_and_original_tamper_fail_closed_without_pilot(tmp_path):
    client, app, headers = fixture(tmp_path)
    first = upload(client, headers)["asset"]
    second = upload(client, headers)["asset"]
    revision = project(client, headers, first)
    pilot = FakePilot(); app.state.local_asr_factory = lambda: pilot
    wrong_project = client.post(f"/api/v2/sources/{second['id']}/asr-proposals", headers=headers,
        json=proposal_body(second, revision))
    assert wrong_project.status_code == 404
    bad = proposal_body(first, revision); bad["expected_original_sha256"] = "0" * 64
    assert client.post(f"/api/v2/sources/{first['id']}/asr-proposals", headers=headers,
                       json=bad).status_code == 409
    with app.state.production_store._connect() as db:
        path = Path(db.execute("SELECT internal_path FROM source_originals WHERE source_id=?",
                               (first["id"],)).fetchone()[0])
    path.write_bytes(path.read_bytes()[:-2] + b"xx")
    tampered = client.post(f"/api/v2/sources/{first['id']}/asr-proposals", headers=headers,
                           json=proposal_body(first, revision))
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "SOURCE_ORIGINAL_INTEGRITY_FAILED"
    assert pilot.calls == []
