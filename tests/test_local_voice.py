from __future__ import annotations

import hashlib
import json
import sys
import wave
import zipfile
from pathlib import Path

import pytest

from quantech_vid import kokoro_worker
from quantech_vid.local_voice import (
    LocalVoiceError,
    LocalVoicePilot,
    LocalVoiceResources,
    ExpectedFile,
    PILOT_LANGUAGE,
    PILOT_SPEED,
    PILOT_VOICE,
    RECEIPT_SCHEMA,
    _validate_receipt,
    _validate_wav,
    build_resource_binding,
)


def _expected(path: Path) -> ExpectedFile:
    payload = path.read_bytes()
    return ExpectedFile(path, len(payload), hashlib.sha256(payload).hexdigest())


def _resources(tmp_path: Path) -> LocalVoiceResources:
    root = tmp_path / "runtime"
    model_dir = root / "models"
    site_packages = root / "venv" / "site-packages"
    espeak_data = site_packages / "espeakng_loader" / "espeak-ng-data"
    model_dir.mkdir(parents=True)
    espeak_data.mkdir(parents=True)
    model = model_dir / "model.onnx"
    voices = model_dir / "voices.bin"
    library = site_packages / "espeakng_loader" / "espeak-ng.dll"
    model.write_bytes(b"synthetic model")
    voices.write_bytes(b"synthetic voices")
    library.write_bytes(b"synthetic espeak library")
    (espeak_data / "synthetic-data").write_bytes(b"data")
    return LocalVoiceResources(
        # CI Linux installs python as a symlink; the synthetic fixture supplies
        # its resolved executable while production continues rejecting links.
        isolated_python=Path(sys.executable).resolve(strict=True),
        resource_root=root,
        model=_expected(model),
        voices=_expected(voices),
        site_packages=site_packages,
        dependency_versions={"synthetic-runtime": "1.0"},
        espeak_library=library,
        espeak_data=espeak_data,
    )


def _fake_worker(path: Path, *, sample_rate: int = 24_000, providers: str = '["CPUExecutionProvider"]') -> Path:
    path.write_text(
        """import hashlib, json, sys, wave
request_path, receipt_path = sys.argv[1:]
request = json.load(open(request_path, encoding='utf-8'))
output = request['output_wav']
frames = b'\\x00\\x00' * 240
with wave.open(output, 'wb') as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(SAMPLE_RATE)
    wav.writeframes(frames)
payload = open(output, 'rb').read()
receipt = {
    'schema': 'quantech.local-voice.receipt.v1',
    'status': 'ok',
    'request_id': request['request_id'],
    'binding_sha256': request['binding_sha256'],
    'voice': request['voice'],
    'language': request['language'],
    'speed': request['speed'],
    'audio': {
        'sha256': hashlib.sha256(payload).hexdigest(),
        'size': len(payload),
        'sample_rate': SAMPLE_RATE,
        'channels': 1,
        'sample_width': 2,
        'frames': 240,
        'duration_ms': 240 * 1000 // SAMPLE_RATE,
    },
    'runtime': {
        'providers': PROVIDERS,
        'dependencies': request['dependencies'],
    },
}
with open(receipt_path, 'x', encoding='utf-8') as stream:
    json.dump(receipt, stream)
""".replace("SAMPLE_RATE", str(sample_rate)).replace("PROVIDERS", providers),
        encoding="utf-8",
    )
    return path


def test_binding_covers_worker_runtime_models_and_espeak(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    binding, digest = build_resource_binding(resources, worker_script=worker)
    assert binding["schema"] == "quantech.local-voice.binding.v1"
    assert binding["model"]["sha256"] == resources.model.sha256
    assert binding["voices"]["sha256"] == resources.voices.sha256
    assert binding["worker"]["sha256"] == hashlib.sha256(worker.read_bytes()).hexdigest()
    assert binding["dependencies"] == {"synthetic-runtime": "1.0"}
    assert binding["execution_providers"] == ["CPUExecutionProvider"]
    assert binding["voice"] == PILOT_VOICE
    assert len(digest) == 64


def test_missing_and_tampered_resources_fail_closed(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    resources.model.path.write_bytes(b"tampered model")
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED"):
        build_resource_binding(resources, worker_script=worker)
    resources.model.path.unlink()
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_RESOURCE_MISSING"):
        build_resource_binding(resources, worker_script=worker)


def test_symlinked_model_is_rejected_when_supported(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    target = resources.model.path
    link = target.with_name("linked.onnx")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable for this test account")
    linked = LocalVoiceResources(
        isolated_python=resources.isolated_python,
        resource_root=resources.resource_root,
        model=ExpectedFile(link, resources.model.size, resources.model.sha256),
        voices=resources.voices,
        site_packages=resources.site_packages,
        dependency_versions=resources.dependency_versions,
        espeak_library=resources.espeak_library,
        espeak_data=resources.espeak_data,
    )
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_RESOURCE_LINK_REJECTED"):
        build_resource_binding(linked, worker_script=worker)


def test_pilot_runs_synthetic_worker_and_preserves_request_contract(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    result = LocalVoicePilot(resources, worker_script=worker).synthesize(
        "A bounded synthetic narration.", job_dir, timeout=10
    )
    assert result.wav_path.is_file()
    assert result.receipt["runtime"]["providers"] == ["CPUExecutionProvider"]
    request_files = list(job_dir.glob("*.request.json"))
    assert len(request_files) == 1
    request = json.loads(request_files[0].read_text(encoding="utf-8"))
    assert request["schema"] == "quantech.local-voice.request.v1"
    assert request["voice"] == PILOT_VOICE
    assert request["language"] == PILOT_LANGUAGE
    assert request["speed"] == PILOT_SPEED
    assert request["max_audio_seconds"] == 60
    assert set(request["model"]) == {"path", "size", "sha256"}
    assert set(request["voices"]) == {"path", "size", "sha256"}


@pytest.mark.parametrize("text", ["", "   ", "a" * 1001, "bad\x00text"])
def test_text_boundary_rejects_empty_oversize_and_control_text(tmp_path: Path, text: str) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_TEXT_INVALID"):
        LocalVoicePilot(resources, worker_script=worker).synthesize(text, job_dir)
    assert not list(job_dir.iterdir())


def test_precancel_avoids_resource_hashing(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    resources.model.path.unlink()
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_CANCELLED"):
        LocalVoicePilot(resources).synthesize(
            "hello", job_dir, cancelled=lambda: True
        )


def test_worker_rejects_any_non_stock_voice_before_loading_runtime(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    receipt_path = tmp_path / "receipt.json"
    request_path.write_text("{}", encoding="utf-8")
    payload = {
        "schema": kokoro_worker.REQUEST_SCHEMA,
        "request_id": "lv_test",
        "binding_sha256": "0" * 64,
        "text": "hello",
        "voice": "personal_voice",
        "language": PILOT_LANGUAGE,
        "speed": PILOT_SPEED,
        "job_dir": str(tmp_path),
        "output_wav": str(tmp_path / "output.wav"),
        "resource_root": str(tmp_path),
        "model": {},
        "voices": {},
        "site_packages": {},
        "dependencies": {},
        "espeak": {},
        "max_audio_seconds": 60,
    }
    with pytest.raises(kokoro_worker.WorkerError, match="LOCAL_VOICE_VOICE_NOT_ALLOWED"):
        kokoro_worker._validate_request(payload, request_path, receipt_path)


def _npy_payload(*, descriptor: str = "<f4", shape: tuple[int, ...] = (2, 2)) -> bytes:
    header = repr({"descr": descriptor, "fortran_order": False, "shape": shape})
    raw_header = (header + "\n").encode("latin1")
    prefix = b"\x93NUMPY\x01\x00" + len(raw_header).to_bytes(2, "little")
    item_size = int(descriptor[-1]) if descriptor[-1:].isdigit() else 0
    elements = 1
    for dimension in shape:
        elements *= dimension
    return prefix + raw_header + b"\x00" * (elements * item_size)


def test_npz_structure_is_bounded_before_numpy_allocation(tmp_path: Path) -> None:
    archive = tmp_path / "voices.bin"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("af_heart.npy", _npy_payload())
    assert kokoro_worker._preflight_npz_structure(archive) == {"af_heart"}


@pytest.mark.parametrize(
    "descriptor,shape",
    [("|O8", (2, 2)), ("<f4", (1_000_001, 1))],
)
def test_npz_preflight_rejects_object_dtype_and_oversized_shape(
    tmp_path: Path, descriptor: str, shape: tuple[int, ...]
) -> None:
    archive = tmp_path / "voices.bin"
    header = _npy_payload(descriptor=descriptor, shape=shape)
    if len(header) > 1024:
        header = header[:128]
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("af_heart.npy", header)
    with pytest.raises(kokoro_worker.WorkerError, match="LOCAL_VOICE_ARCHIVE_INVALID"):
        kokoro_worker._preflight_npz_structure(archive)


def test_npz_preflight_rejects_duplicate_entries(tmp_path: Path) -> None:
    archive = tmp_path / "voices.bin"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("af_heart.npy", _npy_payload())
            output.writestr("af_heart.npy", _npy_payload())
    with pytest.raises(kokoro_worker.WorkerError, match="LOCAL_VOICE_ARCHIVE_INVALID"):
        kokoro_worker._preflight_npz_structure(archive)


def _wav(path: Path, *, sample_rate: int = 24_000, channels: int = 1, frames: int = 240) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frames * channels)


@pytest.mark.parametrize("sample_rate,channels", [(48_000, 1), (24_000, 2)])
def test_wav_validator_rejects_wrong_format(
    tmp_path: Path, sample_rate: int, channels: int
) -> None:
    path = tmp_path / "bad.wav"
    _wav(path, sample_rate=sample_rate, channels=channels)
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_AUDIO_INVALID"):
        _validate_wav(path)


def test_receipt_rejects_non_cpu_provider(tmp_path: Path) -> None:
    path = tmp_path / "audio.wav"
    _wav(path)
    audio = _validate_wav(path)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "ok",
        "request_id": "lv_test",
        "binding_sha256": "1" * 64,
        "voice": PILOT_VOICE,
        "language": PILOT_LANGUAGE,
        "speed": PILOT_SPEED,
        "audio": audio,
        "runtime": {
            "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
            "dependencies": {"synthetic-runtime": "1.0"},
        },
    }
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_CPU_REQUIRED"):
        _validate_receipt(
            receipt,
            request_id="lv_test",
            binding_sha256="1" * 64,
            dependencies={"synthetic-runtime": "1.0"},
            wav=audio,
        )


def test_synthetic_worker_wav_error_is_filtered(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py", sample_rate=48_000)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    with pytest.raises(LocalVoiceError, match="LOCAL_VOICE_AUDIO_INVALID"):
        LocalVoicePilot(resources, worker_script=worker).synthesize("hello", job_dir, timeout=10)
