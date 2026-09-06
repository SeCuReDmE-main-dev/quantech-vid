from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from quantech_vid.config import Settings
from quantech_vid.db import JobStore
from quantech_vid import renderer
from quantech_vid.process import run_command
from quantech_vid.renderer import render_project, verify_media
from quantech_vid.schemas import ProjectManifest, load_manifest
from quantech_vid.subtitles import cues, write_subtitles
from quantech_vid.tts import narration_key, synthesize


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _assert_process_stopped(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return
        time.sleep(0.05)
    assert not _pid_is_running(pid), f"process {pid} is still running"


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
    Image.new("RGB", (640, 360), "#0b4038").save(project / "asset & one.png")
    payload = {
        "schema_version": "1.0", "slug": "smoke", "title": "Smoke",
        "locales": [{"locale": "en", "title": "Smoke", "narration": "Smoke test."}],
        "profiles": [{"name": "smoke", "width": 320, "height": 320, "fps": 30}],
        "scenes": [
            {
                "id": "one", "duration": 0.4, "title_fr": "Test", "body_fr": "Test",
                "title_en": "Render smoke test", "body_en": "Local media pipeline",
                "asset": "asset & one.png", "fit": "cover",
            },
            {
                "id": "two", "duration": 0.6, "title_fr": "Suite", "body_fr": "Test",
                "title_en": "Second scene", "body_en": "Variable scene duration",
                "asset": "asset & one.png", "fit": "contain",
            },
        ]
    }
    path = project / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = ProjectManifest.model_validate(payload)
    artifacts = render_project(settings, path, manifest, "en", "smoke", tmp_path / "output", "silent")
    assert len(artifacts) == 7
    assert all(item.is_file() for item in artifacts)
    qa = json.loads((tmp_path / "output" / "smoke-en-smoke-qa.json").read_text(encoding="utf-8"))
    assert qa["passed"] is True
    assert qa["audio"] is True
    assert qa["not_black"] is True
    assert qa["duration_observed"] == pytest.approx(1.0, abs=qa["duration_tolerance"])
    assert qa["webm"]["passed"] is True
    provenance = json.loads(
        (tmp_path / "output" / "smoke-en-smoke-provenance.json").read_text(encoding="utf-8")
    )
    assert len(provenance["assets"]) == 2
    assert {item.suffix for item in artifacts} == {".mp4", ".webm", ".srt", ".vtt", ".png", ".json"}
    mismatch = verify_media(artifacts[0], 320, 320, 30, expected_duration=2.0)
    assert mismatch["duration"] is False
    assert mismatch["passed"] is False


def test_single_scene_portrait_accents_and_silent_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = settings_for(tmp_path)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        synthesize(settings, "Voix indisponible", "fr", 0.5)

    project = tmp_path / "projet été"
    project.mkdir()
    Image.new("RGB", (360, 640), "#315b74").save(project / "visuel été.png")
    payload = {
        "schema_version": "1.0",
        "slug": "portrait",
        "title": "Été",
        "locales": [{"locale": "fr", "title": "Été", "narration": "Énergie, façade, naïve."}],
        "profiles": [{"name": "portrait", "width": 320, "height": 480, "fps": 24}],
        "scenes": [{
            "id": "été", "duration": 0.5, "title_fr": "Énergie — été", "body_fr": "Façade naïve",
            "title_en": "Summer", "body_en": "Accented text", "asset": "visuel été.png", "fit": "contain",
        }],
    }
    path = project / "project.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    artifacts = render_project(
        settings, path, ProjectManifest.model_validate(payload), "fr", "portrait", tmp_path / "portrait-output", "silent"
    )
    qa = json.loads(next(item for item in artifacts if item.name.endswith("-qa.json")).read_text(encoding="utf-8"))
    provenance = json.loads(
        next(item for item in artifacts if item.name.endswith("-provenance.json")).read_text(encoding="utf-8")
    )
    assert qa["passed"] is True
    assert qa["webm"]["passed"] is True
    assert provenance["narration"]["mode"] == "silent"


def test_font_falls_back_when_platform_fonts_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "_font_candidates", lambda bold: [])
    assert renderer._font(24).getbbox("portable") is not None


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_bounded_process_stops_parent_and_grandchild(tmp_path: Path, mode: str) -> None:
    pid_path = tmp_path / f"{mode}-pids.txt"
    script = (
        "import os,pathlib,subprocess,sys,time;"
        "time.sleep(0.2);"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],close_fds=False);"
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}\\n{child.pid}',encoding='utf-8');"
        "time.sleep(30)"
    )
    if mode == "cancel":
        with pytest.raises(InterruptedError, match="cancelled"):
            run_command(
                [sys.executable, "-c", script, pid_path],
                timeout=5,
                cancelled=pid_path.exists,
            )
    else:
        with pytest.raises(TimeoutError, match="timed out"):
            run_command([sys.executable, "-c", script, pid_path], timeout=2.0)

    parent_pid, child_pid = [int(value) for value in pid_path.read_text(encoding="utf-8").splitlines()]
    _assert_process_stopped(parent_pid)
    _assert_process_stopped(child_pid)


@pytest.mark.parametrize("scalar", ["python", b"python", Path("python")])
def test_bounded_process_rejects_scalar_arguments(scalar: object) -> None:
    with pytest.raises(TypeError, match="sequence"):
        run_command(scalar, timeout=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_bounded_process_rejects_non_finite_or_non_positive_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        run_command([sys.executable, "-c", "pass"], timeout=timeout)
