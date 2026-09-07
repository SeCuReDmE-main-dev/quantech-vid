from __future__ import annotations

import hashlib
import inspect
import json
import math
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from quantech_vid import asr_worker
from quantech_vid.local_asr import (
    COMPUTE_TYPE,
    ExpectedFile,
    LocalAsrError,
    LocalAsrPilot,
    LocalAsrResources,
    MAX_AUDIO_SECONDS,
    MODEL_FILE_METADATA,
    MODEL_ID,
    MODEL_REVISION,
    RECEIPT_SCHEMA,
    SAMPLE_RATE,
    _read_wav,
    _validate_receipt,
    build_resource_binding,
)


def _expected(path: Path) -> ExpectedFile:
    payload = path.read_bytes()
    return ExpectedFile(path, len(payload), hashlib.sha256(payload).hexdigest())


def _resources(tmp_path: Path) -> LocalAsrResources:
    root = tmp_path / "runtime"
    model_dir = root / "models" / "tiny.en"
    site_packages = root / "venv" / "site-packages"
    model_dir.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    files = []
    for name in sorted(MODEL_FILE_METADATA):
        path = model_dir / name
        path.write_bytes(f"synthetic {name}".encode())
        files.append(_expected(path))
    (model_dir / "README.md").write_text("retained provenance, not runtime input", encoding="utf-8")
    (site_packages / "synthetic.dist-info").mkdir()
    (site_packages / "synthetic.dist-info" / "METADATA").write_text(
        "Name: synthetic-runtime\nVersion: 1.0\n", encoding="utf-8"
    )
    return LocalAsrResources(
        isolated_python=Path(sys.executable).resolve(strict=True),
        resource_root=root,
        model_dir=model_dir,
        model_files=tuple(files),
        site_packages=site_packages,
        dependency_versions={"synthetic-runtime": "1.0"},
    )


def _wav(path: Path, *, sample_rate: int = SAMPLE_RATE, channels: int = 1, frames: int = 320) -> str:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frames * channels)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_worker(path: Path, *, device: str = "cpu") -> Path:
    path.write_text(
        """import json, sys
request_path, receipt_path = sys.argv[1:]
request = json.load(open(request_path, encoding='utf-8'))
audio = {key: value for key, value in request['audio'].items() if key != 'path'}
receipt = {
    'schema': 'quantech.local-asr.receipt.v1',
    'status': 'ok',
    'request_id': request['request_id'],
    'binding_sha256': request['binding_sha256'],
    'source': request['source'],
    'audio': audio,
    'model': {'id': 'Systran/faster-whisper-tiny.en', 'revision': '7d45cf02c1ed72d240c0dbf99d544d19bef1b5a3'},  # pragma: allowlist secret -- public upstream revision in fixture
    'task': {
        'language': 'en', 'task': 'transcribe', 'beam_size': 1, 'best_of': 1,
        'temperature': 0.0, 'condition_on_previous_text': False,
        'word_timestamps': True, 'vad_filter': False,
    },
    'segments': [{
        'id': 'asrseg_0000', 'start_ms': 0, 'end_ms': 10, 'text': ' test',
        'avg_logprob': -0.2, 'no_speech_prob': 0.01, 'temperature': 0.0,
        'words': [{'id': 'asrword_0000_0000', 'start_ms': 0, 'end_ms': 10,
                   'text': ' test', 'probability': 0.9}],
    }],
    'runtime': {
        'device': DEVICE, 'compute_type': 'int8_float32', 'cpu_threads': 2, 'num_workers': 1,
        'dependencies': request['dependencies'],
    },
    'limitations': {
        'machine_proposal_only': True, 'human_review_required': True,
        'speaker_identity_inferred': False,
    },
}
with open(receipt_path, 'x', encoding='utf-8') as output:
    json.dump(receipt, output)
""".replace("DEVICE", repr(device)),
        encoding="utf-8",
    )
    return path


def test_binding_covers_exact_model_files_worker_python_and_runtime(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    binding, digest = build_resource_binding(resources, worker_script=worker)
    assert binding["schema"] == "quantech.local-asr.binding.v1"
    assert binding["model"]["id"] == MODEL_ID
    assert binding["model"]["revision"] == MODEL_REVISION
    assert {entry["name"] for entry in binding["model"]["files"]} == set(MODEL_FILE_METADATA)
    assert binding["worker"]["sha256"] == hashlib.sha256(worker.read_bytes()).hexdigest()
    assert binding["runtime"] == {
        "device": "cpu", "compute_type": "int8_float32", "cpu_threads": 2, "num_workers": 1,
    }
    assert len(digest) == 64


def test_missing_tampered_and_incomplete_model_manifest_fail_closed(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    resources.model_files[0].path.write_bytes(b"tampered")
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_RESOURCE_INTEGRITY_FAILED"):
        build_resource_binding(resources, worker_script=worker)
    resources.model_files[0].path.unlink()
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_RESOURCE_MISSING"):
        build_resource_binding(resources, worker_script=worker)
    incomplete = LocalAsrResources(
        resources.isolated_python, resources.resource_root, resources.model_dir,
        resources.model_files[1:], resources.site_packages, resources.dependency_versions,
    )
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_MODEL_MANIFEST_INVALID"):
        build_resource_binding(incomplete, worker_script=worker)


def test_symlinked_model_is_rejected_when_supported(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    original = resources.model_files[0]
    link = original.path.with_name("config-link.json")
    try:
        link.symlink_to(original.path)
    except OSError:
        pytest.skip("symlinks unavailable for this test account")
    linked_files = (ExpectedFile(link, original.size, original.sha256), *resources.model_files[1:])
    linked = LocalAsrResources(
        resources.isolated_python, resources.resource_root, resources.model_dir,
        linked_files, resources.site_packages, resources.dependency_versions,
    )
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_MODEL_MANIFEST_INVALID|LOCAL_ASR_RESOURCE_LINK_REJECTED"):
        build_resource_binding(linked, worker_script=worker)


def test_validated_wav_and_source_hash_reach_synthetic_worker(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    audio_path = tmp_path / "input.wav"
    source_hash = _wav(audio_path)
    job = tmp_path / "job"
    job.mkdir()
    result = LocalAsrPilot(resources, worker_script=worker).transcribe(
        "src_synthetic", source_hash, audio_path, job, timeout=10
    )
    assert result.receipt["limitations"]["machine_proposal_only"] is True
    assert result.receipt["runtime"]["device"] == "cpu"
    request = json.loads(next(job.glob("*.request.json")).read_text(encoding="utf-8"))
    assert request["audio"]["sha256"] == source_hash
    assert request["source"] == {"id": "src_synthetic", "sha256": source_hash}
    assert request["binding"]["task"]["language"] == "en"
    assert request["binding"]["task"]["vad_filter"] is False


def test_wrong_source_hash_fails_before_worker(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    worker = _fake_worker(tmp_path / "fake_worker.py")
    audio_path = tmp_path / "input.wav"
    _wav(audio_path)
    job = tmp_path / "job"
    job.mkdir()
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_SOURCE_INTEGRITY_FAILED"):
        LocalAsrPilot(resources, worker_script=worker).transcribe(
            "src_synthetic", "0" * 64, audio_path, job
        )
    assert not list(job.iterdir())


@pytest.mark.parametrize("sample_rate,channels", [(48_000, 1), (SAMPLE_RATE, 2)])
def test_wav_rejects_wrong_rate_and_channels(tmp_path: Path, sample_rate: int, channels: int) -> None:
    path = tmp_path / "invalid.wav"
    _wav(path, sample_rate=sample_rate, channels=channels)
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_AUDIO_INVALID"):
        _read_wav(path)


def test_wav_rejects_duration_over_five_minutes_from_header(tmp_path: Path) -> None:
    path = tmp_path / "oversize.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.setnframes(MAX_AUDIO_SECONDS * SAMPLE_RATE + 1)
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_AUDIO_INVALID"):
        _read_wav(path)


def _receipt(
    audio: dict[str, object], *, device: str = "cpu", compute_type: str = COMPUTE_TYPE,
) -> dict[str, object]:
    return {
        "schema": RECEIPT_SCHEMA, "status": "ok", "request_id": "asr_test",
        "binding_sha256": "1" * 64, "source": {"id": "src_test", "sha256": audio["sha256"]},
        "audio": audio, "model": {"id": MODEL_ID, "revision": MODEL_REVISION},
        "task": {
            "language": "en", "task": "transcribe", "beam_size": 1, "best_of": 1,
            "temperature": 0.0, "condition_on_previous_text": False,
            "word_timestamps": True, "vad_filter": False,
        },
        "segments": [],
        "runtime": {
            "device": device, "compute_type": compute_type, "cpu_threads": 2,
            "num_workers": 1, "dependencies": {"synthetic-runtime": "1.0"},
        },
        "limitations": {
            "machine_proposal_only": True, "human_review_required": True,
            "speaker_identity_inferred": False,
        },
    }


def test_receipt_rejects_non_cpu_runtime(tmp_path: Path) -> None:
    path = tmp_path / "input.wav"
    source_hash = _wav(path)
    audio, _ = _read_wav(path)
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_CPU_REQUIRED"):
        _validate_receipt(
            _receipt(audio, device="cuda"), request_id="asr_test", binding_sha256="1" * 64,
            source_id="src_test", source_sha256=source_hash, audio=audio,
            dependencies={"synthetic-runtime": "1.0"},
        )


def test_receipt_accepts_exact_int8_float32_and_rejects_int8_alias(tmp_path: Path) -> None:
    path = tmp_path / "input.wav"
    source_hash = _wav(path)
    audio, _ = _read_wav(path)
    accepted = _validate_receipt(
        _receipt(audio, compute_type="int8_float32"),
        request_id="asr_test", binding_sha256="1" * 64,
        source_id="src_test", source_sha256=source_hash, audio=audio,
        dependencies={"synthetic-runtime": "1.0"},
    )
    assert accepted["runtime"]["compute_type"] == "int8_float32"
    with pytest.raises(LocalAsrError, match="LOCAL_ASR_CPU_REQUIRED"):
        _validate_receipt(
            _receipt(audio, compute_type="int8"),
            request_id="asr_test", binding_sha256="1" * 64,
            source_id="src_test", source_sha256=source_hash, audio=audio,
            dependencies={"synthetic-runtime": "1.0"},
        )


def test_worker_binding_rejects_wrong_locale() -> None:
    binding = {
        "schema": asr_worker.BINDING_SCHEMA, "worker_protocol": asr_worker.WORKER_PROTOCOL,
        "worker": {}, "python": {}, "dependencies": {}, "site_packages": {}, "model": {},
        "runtime": {"device": "cpu", "compute_type": "int8_float32", "cpu_threads": 2, "num_workers": 1},
        "task": {
            "language": "fr", "task": "transcribe", "beam_size": 1, "best_of": 1,
            "temperature": 0.0, "condition_on_previous_text": False,
            "word_timestamps": True, "vad_filter": False,
        },
        "limits": {"sample_rate": 16_000, "max_audio_seconds": 300, "max_segments": 128, "max_words": 4_096},
    }
    digest = asr_worker._canonical_hash(binding)
    with pytest.raises(asr_worker.WorkerError, match="LOCAL_ASR_REQUEST_INVALID"):
        asr_worker._validate_binding(binding, digest)
    binding["task"]["language"] = "en"
    binding["runtime"]["compute_type"] = "int8"
    digest = asr_worker._canonical_hash(binding)
    with pytest.raises(asr_worker.WorkerError, match="LOCAL_ASR_CPU_REQUIRED"):
        asr_worker._validate_binding(binding, digest)


def _word(start: float = 0.0, end: float = 0.1, text: str = " test", probability: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, word=text, probability=probability)


def _segment(
    start: float = 0.0, end: float = 0.1, text: str = " test",
    words: list[SimpleNamespace] | None = None, avg_logprob: float = -0.2,
) -> SimpleNamespace:
    return SimpleNamespace(
        start=start, end=end, text=text, words=[_word()] if words is None else words,
        avg_logprob=avg_logprob, no_speech_prob=0.01, temperature=0.0,
    )


def test_worker_converts_valid_observations_to_closed_proposal() -> None:
    result = asr_worker._build_segments([_segment()], 1_000)
    assert result == [{
        "id": "asrseg_0000", "start_ms": 0, "end_ms": 100, "text": " test",
        "avg_logprob": -0.2, "no_speech_prob": 0.01, "temperature": 0.0,
        "words": [{
            "id": "asrword_0000_0000", "start_ms": 0, "end_ms": 100,
            "text": " test", "probability": 0.9,
        }],
    }]


@pytest.mark.parametrize(
    "segments",
    [
        [_segment(end=2.0)],
        [_segment(avg_logprob=math.nan)],
        [{"start": 0.0, "end": 0.1, "text": "hostile dictionary"}],
        [_segment(words=[_word(probability=2.0)])],
    ],
)
def test_worker_rejects_out_of_bounds_nan_dictionary_and_probability(segments: object) -> None:
    with pytest.raises(asr_worker.WorkerError, match="LOCAL_ASR_PROPOSAL_INVALID"):
        asr_worker._build_segments(segments, 1_000)


def test_worker_rejects_segment_and_word_count_overflow() -> None:
    segments = [_segment(start=index / 10, end=(index + 1) / 10) for index in range(129)]
    with pytest.raises(asr_worker.WorkerError, match="LOCAL_ASR_PROPOSAL_INVALID"):
        asr_worker._build_segments(segments, 20_000)
    with pytest.raises(asr_worker.WorkerError, match="LOCAL_ASR_PROPOSAL_INVALID"):
        asr_worker._build_segments([_segment(words=[_word() for _ in range(4_097)])], 1_000)


def test_worker_uses_verified_waveform_and_never_passes_path_to_transcribe() -> None:
    source = inspect.getsource(asr_worker._transcribe)
    assert "np.frombuffer" in source
    assert "model.transcribe(\n            waveform" in source
    assert "local_files_only=True" in source
    assert 'device="cpu"' in source
