from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from quantech_vid.config import Settings
from quantech_vid.db import JobStore
from quantech_vid.renderer import render_project
from quantech_vid.schemas import ProjectManifest, load_manifest
from quantech_vid.subtitles import cues, write_subtitles
from quantech_vid.tts import narration_key


def settings_for(root: Path) -> Settings:
    data = root / "runtime"
    settings = Settings(
        root=root, data_dir=data, host="127.0.0.1", port=7476,
        allowed_asset_roots=(root,), tts_model="test", tts_voice_fr="alloy",
        tts_voice_en="alloy", max_workers=1,
    )
    settings.ensure_directories()
    return settings


def test_manifest_and_duration() -> None:
    manifest = load_manifest(Path("projects/synthia-promo/project.json"))
    assert manifest.duration == 45
    assert {item.locale for item in manifest.locales} == {"fr", "en"}
    assert {item.name for item in manifest.profiles} == {"landscape", "vertical"}


def test_asset_root_blocks_escape(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    allowed = tmp_path / "asset.png"
    allowed.touch()
    assert settings.require_allowed_path(allowed) == allowed.resolve()
    with pytest.raises(ValueError):
        settings.require_allowed_path(tmp_path.parent / "outside.png")


def test_tts_cache_key_is_stable() -> None:
    first = narration_key("hello", "alloy", "model")
    assert first == narration_key("hello", "alloy", "model")
    assert first != narration_key("bonjour", "alloy", "model")


def test_subtitles_cover_duration(tmp_path: Path) -> None:
    items = cues("First sentence. Second sentence.", 8)
    assert items[0][0] == 0
    assert items[-1][1] == 8
    srt, vtt = tmp_path / "test.srt", tmp_path / "test.vtt"
    write_subtitles("First sentence. Second sentence.", 8, srt, vtt)
    assert "WEBVTT" in vtt.read_text(encoding="utf-8")
    assert "00:00:08,000" in srt.read_text(encoding="utf-8")


def test_job_recovery_and_cancel(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create({"value": 1})
    store.update(job.id, status="running", progress=20)
    assert store.recover_interrupted() == 1
    assert store.get(job.id).status == "failed"
    queued = store.create({"value": 2})
    assert store.cancel(queued.id).status == "cancelled"


def test_real_short_render(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    Image.new("RGB", (640, 360), "#0b4038").save(project / "asset.png")
    payload = {
        "schema_version": "1.0", "slug": "smoke", "title": "Smoke",
        "locales": [{"locale": "en", "title": "Smoke", "narration": "Smoke test."}],
        "profiles": [{"name": "smoke", "width": 320, "height": 320, "fps": 30}],
        "scenes": [{
            "id": "one", "duration": 1, "title_fr": "Test", "body_fr": "Test",
            "title_en": "Render smoke test", "body_en": "Local media pipeline",
            "asset": "asset.png", "fit": "cover"
        }]
    }
    path = project / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = ProjectManifest.model_validate(payload)
    artifacts = render_project(settings, path, manifest, "en", "smoke", tmp_path / "output", "silent")
    assert len(artifacts) == 7
    assert all(item.is_file() for item in artifacts)
    qa = json.loads((tmp_path / "output" / "smoke-en-smoke-qa.json").read_text(encoding="utf-8"))
    assert qa["passed"] is True
