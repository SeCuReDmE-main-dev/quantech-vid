from __future__ import annotations

import hashlib
import json
import shutil
import wave
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image

from quantech_vid.config import Settings
from quantech_vid.local_voice import LocalVoiceError, LocalVoiceResult
from quantech_vid.production_schemas import (
    OutputProfileV2,
    SceneProjectV2,
    SceneV2,
    SourceAsset,
    SourceProvenance,
    SourceRights,
    TrackV2,
)
from quantech_vid.production_service import ProductionService
from quantech_vid.production_store import ContractError, ProductionStore, now_iso, utc_now
from quantech_vid.renderer import _prepare_narration
from quantech_vid.schemas import LocaleTrack, Profile, ProjectManifest, Scene


class SyntheticPilot:
    def __init__(self, binding: str = "a" * 64, *, frames: int = 240,
                 providers: list[str] | None = None) -> None:
        self.binding_sha256 = binding
        self.frames = frames
        self.providers = providers or ["CPUExecutionProvider"]
        self.binding_calls = 0
        self.synthesis_calls: list[tuple[str, Path, float]] = []

    def binding(self) -> tuple[dict, str]:
        self.binding_calls += 1
        return {"synthetic": True}, self.binding_sha256

    def synthesize(self, text: str, job_dir: Path, *, timeout: float, cancelled) -> LocalVoiceResult:
        directory = Path(job_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "synthetic-pilot.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24_000)
            output.writeframes(b"\x00\x00" * min(self.frames, 24_000))
        self.synthesis_calls.append((text, directory, timeout))
        return LocalVoiceResult(
            wav_path=path,
            receipt={
                "audio": {"frames": self.frames, "sample_rate": 24_000},
                "runtime": {"providers": self.providers},
            },
            binding_sha256=self.binding_sha256,
        )


def _fixture(tmp_path: Path, *, local_voice=None, narration: str = "Short narration",
             voice: str | None = None, include_french: bool = True) -> dict:
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
    store = ProductionStore(
        settings.data_dir / "production.sqlite3",
        b"local-voice-production-test-key",
        "local-voice-pairing-code",
        local_voice=local_voice,
    )
    token, csrf = store.consume_pairing("local-voice-pairing-code", "operator")
    human = store.authenticate(token, csrf)
    image_path = settings.data_dir / "admitted-assets" / "source.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "#135724").save(image_path)
    content = image_path.read_bytes()
    asset = SourceAsset(
        id="src_" + "1" * 32,
        sha256=hashlib.sha256(content).hexdigest(),
        media_type="image/png",
        size=len(content),
        provenance=SourceProvenance(origin="synthetic", collected_by="test"),
        rights=SourceRights(basis="owned", reference="synthetic fixture"),
        allowed_operations=["render"],
        created_at=now_iso(),
    )
    store.add_source(human, asset, image_path)
    tracks = [TrackV2(locale="en", title="English", narration=narration, voice=voice)]
    titles = {"en": "Scene"}
    if include_french:
        tracks.append(TrackV2(locale="fr", title="Français", narration="Narration française"))
        titles["fr"] = "Scène"
    document = SceneProjectV2(
        slug="local-voice-project",
        title="Local voice project",
        sources=[asset.id],
        scenes=[SceneV2(id="scene-1", duration=1.0, source_asset_id=asset.id,
                        title=titles)],
        tracks=tracks,
        output_profiles=[OutputProfileV2(name="square", width=320, height=320, fps=12)],
    )
    revision = store.create_project(human, document)
    agent_token = store.create_agent(
        "operator", "voice-agent", "Voice agent", revision.project_id, revision.revision
    )
    agent = store.authenticate(agent_token)
    return {
        "settings": settings,
        "store": store,
        "human": human,
        "agent": agent,
        "document": document,
        "project_id": revision.project_id,
        "revision": revision.revision,
    }


def _plan_data(fixture: dict, *, locale: str = "en", mode: str = "local_kokoro_cpu") -> dict:
    return {
        "project_id": fixture["project_id"],
        "revision": fixture["revision"],
        "locale": locale,
        "profile": "square",
        "narration_mode": mode,
        "max_duration_seconds": 600,
        "max_output_bytes": 50_000_000,
    }


def _manifest(*, narration: str = "Short narration", voice: str | None = None,
              duration: float = 1.0) -> ProjectManifest:
    scene_durations = ([duration] if duration <= 60 else [60.0, duration - 60.0])
    return ProjectManifest(
        slug="voice-helper",
        title="Voice helper",
        locales=[LocaleTrack(locale="en", title="English", narration=narration, voice=voice)],
        profiles=[Profile(name="square", width=320, height=320, fps=12)],
        scenes=[Scene(id=f"scene-{index}", duration=scene_duration,
                      title_fr="", body_fr="", title_en="Scene", body_en="",
                      asset="unused.png")
                for index, scene_duration in enumerate(scene_durations, start=1)],
    )


def test_silent_plan_hash_and_resources_remain_legacy_compatible(tmp_path: Path) -> None:
    pilot = SyntheticPilot()
    fixture = _fixture(tmp_path, local_voice=pilot)
    plan = fixture["store"].create_plan(fixture["agent"], _plan_data(fixture, mode="silent"))
    assert plan.narration_mode == "silent"
    assert plan.provider_resource_modes == {
        "narration": "local-silent", "render": "local-ffmpeg"
    }
    assert pilot.binding_calls == 0
    unsigned = plan.model_dump(mode="json", exclude={"id", "plan_hash", "created_at"})
    from quantech_vid.production_store import canonical_hash
    assert plan.plan_hash == canonical_hash(unsigned)


def test_local_plan_requires_configured_server_runtime(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(ContractError, match="LOCAL_VOICE_UNAVAILABLE") as caught:
        fixture["store"].create_plan(fixture["agent"], _plan_data(fixture))
    assert caught.value.status == 503


def test_local_plan_binds_exact_server_runtime_and_detects_change(tmp_path: Path) -> None:
    pilot = SyntheticPilot()
    fixture = _fixture(tmp_path, local_voice=pilot)
    plan = fixture["store"].create_plan(fixture["agent"], _plan_data(fixture))
    assert plan.provider_resource_modes == {
        "narration": "local-kokoro-cpu",
        "render": "local-ffmpeg",
        "local_voice": "a" * 64,
        "local_voice_voice": "af_heart",
        "local_voice_language": "en-us",
    }
    fixture["store"].plan_and_revision_internal(plan.id)
    pilot.binding_sha256 = "b" * 64
    with pytest.raises(ContractError, match="LOCAL_VOICE_BINDING_MISMATCH"):
        fixture["store"].plan_and_revision_internal(plan.id)


def test_local_plan_rejects_wrong_locale_custom_voice_and_long_text(tmp_path: Path) -> None:
    pilot = SyntheticPilot()
    fixture = _fixture(tmp_path / "locale", local_voice=pilot)
    with pytest.raises(ContractError, match="LOCAL_VOICE_LOCALE_UNSUPPORTED"):
        fixture["store"].create_plan(
            fixture["agent"], _plan_data(fixture, locale="fr")
        )
    custom = _fixture(tmp_path / "voice", local_voice=pilot, voice="custom")
    with pytest.raises(ContractError, match="LOCAL_VOICE_VOICE_NOT_ALLOWED"):
        custom["store"].create_plan(custom["agent"], _plan_data(custom))
    long = _fixture(tmp_path / "text", local_voice=pilot, narration="a" * 1001)
    with pytest.raises(ContractError, match="LOCAL_VOICE_TEXT_INVALID"):
        long["store"].create_plan(long["agent"], _plan_data(long))


def test_local_plan_does_not_bypass_missing_expired_or_invalid_approval(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, local_voice=SyntheticPilot())
    store = fixture["store"]
    plan = store.create_plan(fixture["agent"], _plan_data(fixture))
    with pytest.raises(ContractError, match="APPROVAL_REQUIRED"):
        store.enqueue_approved(fixture["agent"], plan.id, "missing-approval-key")

    store.authorize(fixture["human"], plan.id, "voice-agent", 300)
    with store._connect() as db:
        db.execute(
            "UPDATE production_grants SET expires_at=? WHERE plan_id=?",
            ((utc_now() - timedelta(seconds=1)).isoformat(), plan.id),
        )
    with pytest.raises(ContractError, match="APPROVAL_REQUIRED"):
        store.enqueue_approved(fixture["agent"], plan.id, "expired-approval-key")

    store.authorize(fixture["human"], plan.id, "voice-agent", 300)
    with store._connect() as db:
        db.execute(
            "UPDATE production_grants SET signature='invalid' WHERE plan_id=? AND consumed_at IS NULL",
            (plan.id,),
        )
    with pytest.raises(ContractError, match="APPROVAL_INTEGRITY_FAILED"):
        store.enqueue_approved(fixture["agent"], plan.id, "invalid-approval-key")


def test_narration_refuses_audio_longer_than_timeline_without_normalizing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = SyntheticPilot(frames=24_001)
    normalized = []
    monkeypatch.setattr("quantech_vid.renderer.normalize_audio",
                        lambda *args, **kwargs: normalized.append(args))
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_AUDIO_EXCEEDS_TIMELINE"):
        _prepare_narration(
            _fixture(tmp_path / "settings")["settings"],
            _manifest(),
            _manifest().locales[0],
            "en",
            tmp_path / "output",
            "local_kokoro_cpu",
            local_voice=pilot,
            local_voice_binding=pilot.binding_sha256,
        )
    assert normalized == []


def test_narration_rejects_non_cpu_injected_receipt(tmp_path: Path) -> None:
    pilot = SyntheticPilot(providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    manifest = _manifest()
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_CPU_REQUIRED"):
        _prepare_narration(
            _fixture(tmp_path / "settings")["settings"], manifest, manifest.locales[0],
            "en", tmp_path / "output", "local_kokoro_cpu",
            local_voice=pilot, local_voice_binding=pilot.binding_sha256,
        )


def test_narration_rechecks_binding_before_spending_synthesis(tmp_path: Path) -> None:
    pilot = SyntheticPilot(binding="b" * 64)
    manifest = _manifest()
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_BINDING_MISMATCH"):
        _prepare_narration(
            _fixture(tmp_path / "settings")["settings"], manifest, manifest.locales[0],
            "en", tmp_path / "output", "local_kokoro_cpu",
            local_voice=pilot, local_voice_binding="a" * 64,
        )
    assert pilot.synthesis_calls == []


def test_narration_enforces_worker_audio_cap_even_on_longer_timeline(tmp_path: Path) -> None:
    pilot = SyntheticPilot(frames=24_000 * 60 + 1)
    manifest = _manifest(duration=120.0)
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_AUDIO_INVALID"):
        _prepare_narration(
            _fixture(tmp_path / "settings")["settings"], manifest, manifest.locales[0],
            "en", tmp_path / "output", "local_kokoro_cpu",
            local_voice=pilot, local_voice_binding=pilot.binding_sha256,
        )


def test_short_local_narration_is_padded_by_existing_normalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = SyntheticPilot(frames=240)
    manifest = _manifest(duration=2.0)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    calls = []

    def normalize(source: Path, target: Path, duration: float) -> Path:
        calls.append((source, target, duration))
        shutil.copyfile(source, target)
        return target

    monkeypatch.setattr("quantech_vid.renderer.normalize_audio", normalize)
    narration, cache_hit, digest, model, voice = _prepare_narration(
        _fixture(tmp_path / "settings")["settings"], manifest, manifest.locales[0],
        "en", output_dir, "local_kokoro_cpu",
        local_voice=pilot, local_voice_binding=pilot.binding_sha256,
    )
    assert calls == [(output_dir / "synthetic-pilot.wav", output_dir / "narration.wav", 2.0)]
    assert narration == output_dir / "narration.wav"
    assert cache_hit is False and len(digest) == 64
    assert model == "kokoro-v1.0.onnx" and voice == "af_heart"
    assert pilot.synthesis_calls[0][0] == "Short narration"


@pytest.mark.parametrize("tamper_during_render", [False, True])
def test_approved_service_passes_only_signed_local_runtime_to_renderer(
    tmp_path: Path, tamper_during_render: bool
) -> None:
    pilot = SyntheticPilot()
    fixture = _fixture(tmp_path, local_voice=pilot)
    store = fixture["store"]
    plan = store.create_plan(fixture["agent"], _plan_data(fixture))
    store.authorize(fixture["human"], plan.id, "voice-agent", 300)
    job, _ = store.enqueue_approved(fixture["agent"], plan.id, "approved-local-render-key")
    observed = {}

    def renderer(*args, **kwargs):
        observed["mode"] = args[6]
        observed["pilot"] = kwargs.get("local_voice")
        observed["binding"] = kwargs.get("local_voice_binding")
        output_dir = Path(args[5])
        output_dir.mkdir(parents=True, exist_ok=True)
        mp4 = output_dir / "synthetic.mp4"
        webm = output_dir / "synthetic.webm"
        qa = output_dir / "synthetic-qa.json"
        mp4.write_bytes(b"m" * 2048)
        webm.write_bytes(b"w" * 2048)
        qa.write_text(json.dumps({"passed": True, "webm": {"passed": True}}),
                      encoding="utf-8")
        if tamper_during_render:
            pilot.binding_sha256 = "b" * 64
        return [mp4, webm, qa]

    service = ProductionService(
        fixture["settings"], store, render_fn=renderer, background=False
    )
    result = service.run_job(job.id)
    assert observed == {
        "mode": "local_kokoro_cpu", "pilot": pilot, "binding": "a" * 64
    }
    if tamper_during_render:
        assert result.status == "failed"
        assert result.error_code == "LOCAL_VOICE_BINDING_MISMATCH"
        assert result.receipts == []
        return
    assert result.status == "complete"
    assert {receipt.role for receipt in result.receipts}.issuperset(
        {"video-mp4", "video-webm", "quality-report", "claim-provenance"}
    )
