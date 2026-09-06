from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from quantech_vid.config import Settings
from quantech_vid.production_schemas import (OutputProfileV2, SceneProjectV2, SceneV2,
    SourceAsset, SourceProvenance, SourceRights, TrackV2)
from quantech_vid.production_store import ContractError, ProductionStore, now_iso
from quantech_vid.renderer import render_project
from quantech_vid.scene3d import (MAX_SCENE3D_FRAMES, Scene3DError, Scene3DUnavailable,
    Visual3DConfig, bundle_identity, capture_scene_frames, validate_project_bounds)
from quantech_vid.schemas import LocaleTrack, Profile, ProjectManifest, Scene
from quantech_vid.tool_catalog import ScenePatch


SOURCE_ID = "src_" + "a" * 32


def browser_path() -> Path | None:
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    try:
        default = Path(playwright.chromium.executable_path)
    finally:
        playwright.stop()
    if default.is_file():
        return default
    cache = Path.home() / "AppData" / "Local" / "ms-playwright"
    candidates = sorted(cache.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True)
    return candidates[0] if candidates else None


def visual(animation: str = "spin") -> Visual3DConfig:
    return Visual3DConfig(kind="diagram", lines=["Input", "Human review"],
                          accent="#14B8A6", animation=animation)


def test_visual_contract_is_closed_bounded_and_historical_default_is_omitted() -> None:
    scene = SceneV2(id="scene-1", duration=1, source_asset_id=SOURCE_ID,
                    title={"en": "Scene"})
    assert "visual_3d" not in scene.model_dump(mode="json")
    with pytest.raises(ValidationError):
        Visual3DConfig.model_validate({**visual().model_dump(), "url": "https://example.invalid"})
    with pytest.raises(ValidationError):
        Visual3DConfig(kind="code", lines=["x"] * 9, accent="#14B8A6", animation="none")
    with pytest.raises(ValidationError):
        Visual3DConfig(kind="code", lines=[""], accent="#14B8A6", animation="none")
    with pytest.raises(ValidationError):
        Visual3DConfig(kind="code", lines=["   "], accent="#14B8A6", animation="none")
    proposal = ScenePatch(scene_id="scene-1", visual_3d=visual())
    assert proposal.model_dump(mode="json", exclude_none=True)["visual_3d"]["kind"] == "diagram"


def test_plan_binds_bundle_identity_and_rejects_changed_runtime(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    import quantech_vid.production_store as store_module
    monkeypatch.setattr(store_module, "runtime_binding", lambda: "three@0.185.1:sha256:" + "a" * 64)
    store = ProductionStore(tmp_path / "production.sqlite3", b"scene3d-signing-key-32-bytes-long",
                            "scene3d-pairing-code")
    token, csrf = store.consume_pairing("scene3d-pairing-code", "teacher")
    human = store.authenticate(token, csrf)
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 320), "navy").save(source)
    asset = SourceAsset(id=SOURCE_ID, sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        media_type="image/png", size=source.stat().st_size,
        provenance=SourceProvenance(origin="synthetic fixture", collected_by="teacher"),
        rights=SourceRights(basis="owned", reference="test fixture"),
        allowed_operations=["render"], created_at=now_iso())
    store.add_source(human, asset, source)
    project = SceneProjectV2(slug="scene3d-plan", title="Scene3D plan", sources=[SOURCE_ID],
        scenes=[SceneV2(id="scene-1", duration=1, source_asset_id=SOURCE_ID,
                        title={"en": "Scene"}, visual_3d=visual())],
        tracks=[TrackV2(locale="en", title="Scene", narration="Narration")],
        output_profiles=[OutputProfileV2(name="square", width=320, height=320, fps=12)])
    revision = store.create_project(human, project)
    agent_token = store.create_agent("teacher", "scene3d-agent", "Scene3D agent",
                                     revision.project_id, revision.revision)
    agent = store.authenticate(agent_token)
    plan = store.create_plan(agent, {"project_id": revision.project_id, "revision": 1,
        "locale": "en", "profile": "square", "narration_mode": "silent",
        "max_duration_seconds": 5, "max_output_bytes": 20_000_000})
    assert plan.provider_resource_modes["scene3d"].endswith("a" * 64)
    assert plan.limits["max_scene3d_frames"] == MAX_SCENE3D_FRAMES
    monkeypatch.setattr(store_module, "runtime_binding", lambda: "three@0.185.1:sha256:" + "b" * 64)
    with pytest.raises(ContractError) as changed:
        store.plan_and_revision_internal(plan.id)
    assert changed.value.code == "THREED_BUNDLE_MISMATCH"


def test_bundle_identity_and_project_preflight_are_bounded(monkeypatch: pytest.MonkeyPatch,
                                                            tmp_path: Path) -> None:
    identity = bundle_identity()
    assert identity.binding.startswith("three@0.185.1:sha256:")
    assert hashlib.sha256(identity.path.read_bytes()).hexdigest() == identity.sha256
    scenes = [SimpleNamespace(duration=60, visual_3d=visual()) for _ in range(3)]
    assert validate_project_bounds(scenes, 320, 320, 10, 1_000_000) == MAX_SCENE3D_FRAMES
    with pytest.raises(Scene3DError, match="FRAME_LIMIT"):
        validate_project_bounds(scenes + [SimpleNamespace(duration=.1, visual_3d=visual())],
                                320, 320, 10, 1_000_000)
    with pytest.raises(Scene3DError, match="PROFILE_LIMIT"):
        validate_project_bounds([], 1920, 1200, 12, 1_000_000)
    import quantech_vid.scene3d as module
    monkeypatch.setattr(module, "bundle_path", lambda: tmp_path / "missing.js")
    with pytest.raises(Scene3DUnavailable, match="THREED_RENDERER_UNAVAILABLE"):
        module.bundle_identity()


def test_precancel_does_not_probe_bundle_or_launch_browser(monkeypatch: pytest.MonkeyPatch,
                                                            tmp_path: Path) -> None:
    import quantech_vid.scene3d as module
    monkeypatch.setattr(module, "bundle_identity",
                        lambda: (_ for _ in ()).throw(AssertionError("bundle must not be read")))
    with pytest.raises(InterruptedError):
        module.capture_scene_frames(visual=visual(), title="Cancelled", duration=1,
            width=320, height=320, fps=12, output_dir=tmp_path, frame_byte_limit=1_000_000,
            cancelled=lambda: True)


def test_real_chromium_capture_is_animated_offline_and_cleans_on_cancel(tmp_path: Path) -> None:
    executable = browser_path()
    if executable is None:
        pytest.skip("matching Playwright Chromium is not installed")
    output = tmp_path / "frames"
    frames, attempts = capture_scene_frames(
        visual=visual("spin"), title="<img src=https://example.invalid>", duration=1,
        width=320, height=320, fps=12, output_dir=output, frame_byte_limit=20_000_000,
        cancelled=lambda: False, browser_executable=executable,
    )
    assert len(frames) == 12 and attempts == []
    with Image.open(frames[0]) as first, Image.open(frames[6]) as middle:
        assert first.size == middle.size == (320, 320)
    assert hashlib.sha256(frames[0].read_bytes()).digest() != hashlib.sha256(frames[6].read_bytes()).digest()

    clock_samples = tmp_path / "clock-samples"
    samples, _ = capture_scene_frames(
        visual=visual("spin"), title="Explicit clock", duration=1, width=320, height=320,
        fps=12, output_dir=clock_samples, frame_byte_limit=10_000_000,
        cancelled=lambda: False, browser_executable=executable, _frame_times=[0, 0.5, 0],
    )
    digests = [hashlib.sha256(path.read_bytes()).digest() for path in samples]
    assert digests[0] == digests[2]
    assert digests[0] != digests[1]

    cancelled = tmp_path / "cancelled"
    with pytest.raises(InterruptedError):
        capture_scene_frames(visual=visual(), title="Cancelled", duration=1, width=320,
            height=320, fps=12, output_dir=cancelled, frame_byte_limit=20_000_000,
            cancelled=lambda: True, browser_executable=executable)
    assert list(cancelled.glob("*.png")) == []

    final_limit = tmp_path / "final-limit"
    total_bytes = sum(path.stat().st_size for path in frames)
    checks = 0
    def never_cancel() -> bool:
        nonlocal checks
        checks += 1
        return False
    with pytest.raises(Scene3DError, match="FRAME_BYTES_LIMIT"):
        capture_scene_frames(visual=visual("spin"), title="<img src=https://example.invalid>", duration=1, width=320,
            height=320, fps=12, output_dir=final_limit, frame_byte_limit=total_bytes - 1,
            cancelled=never_cancel, browser_executable=executable)
    assert checks == 13  # pre-launch plus all twelve frames: rejection happened on the final frame
    assert list(final_limit.glob("*.png")) == []


def test_real_scene3d_pipeline_produces_qa_valid_mp4_and_webm(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    executable = browser_path()
    if executable is None:
        pytest.skip("matching Playwright Chromium is not installed")
    monkeypatch.setenv("QUANTECH_SCENE3D_CHROMIUM", str(executable))
    settings = Settings(root=tmp_path, data_dir=tmp_path / "runtime", host="127.0.0.1",
        port=7476, allowed_asset_roots=(tmp_path,), tts_model="disabled",
        tts_voice_fr="disabled", tts_voice_en="disabled", max_workers=1)
    settings.ensure_directories()
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 320), "#224466").save(source)
    identity = bundle_identity()
    manifest = ProjectManifest(slug="scene3d-real", title="Scene 3D",
        disclosure="Synthetic scene; human review required.",
        locales=[LocaleTrack(locale="en", title="Scene 3D", narration="Synthetic scene")],
        profiles=[Profile(name="square", width=320, height=320, fps=12)],
        scenes=[Scene(id="scene-1", duration=1, title_fr="Scène", title_en="Scene",
                      body_en="Original source context.", notice="hypothesis=1; suspended=1",
                      asset=source.name, visual_3d=visual("spin"))],
        scene3d_binding=identity.binding, scene3d_frame_byte_limit=20_000_000)
    manifest_path = tmp_path / "project.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    artifacts = render_project(settings, manifest_path, manifest, "en", "square",
                               tmp_path / "output", "silent")
    mp4 = next(path for path in artifacts if path.suffix == ".mp4")
    webm = next(path for path in artifacts if path.suffix == ".webm")
    qa_path = next(path for path in artifacts if path.name.endswith("-qa.json"))
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    assert mp4.stat().st_size > 1024 and webm.stat().st_size > 1024
    assert qa["passed"] is True and qa["webm"]["passed"] is True
    assert qa["duration_delta"] <= qa["duration_tolerance"]
    assert not (tmp_path / "output" / "timeline").exists()
    assert [path for path in (tmp_path / "output").glob("scene3d-[0-9][0-9]")
            if path.is_dir()] == []
