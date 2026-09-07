from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import os
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from .process import run_command


REQUEST_SCHEMA = "quantech.local-asr.request.v1"
RECEIPT_SCHEMA = "quantech.local-asr.receipt.v1"
BINDING_SCHEMA = "quantech.local-asr.binding.v1"
WORKER_PROTOCOL = "faster-whisper-cpu-pilot-v1"
MODEL_ID = "Systran/faster-whisper-tiny.en"
MODEL_REVISION = "7d45cf02c1ed72d240c0dbf99d544d19bef1b5a3"  # pragma: allowlist secret -- immutable public upstream revision
MODEL_FILE_METADATA: dict[str, tuple[int, str]] = {
    "config.json": (2_317, "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5"),  # pragma: allowlist secret -- public file digest
    "model.bin": (75_537_502, "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a"),  # pragma: allowlist secret -- public file digest
    "tokenizer.json": (2_128_466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),  # pragma: allowlist secret -- public file digest
    "vocabulary.txt": (422_309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),  # pragma: allowlist secret -- public file digest
}

SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 300
MAX_SEGMENTS = 128
MAX_WORDS = 4_096
MAX_SEGMENT_TEXT_CHARS = 1_000
MAX_WORD_TEXT_CHARS = 200
CPU_THREADS = 2
NUM_WORKERS = 1
LANGUAGE = "en"
TASK = "transcribe"
COMPUTE_TYPE = "int8_float32"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_RESOURCE_TREE_BYTES = 2 * 1024 * 1024 * 1024
MAX_RESOURCE_TREE_ENTRIES = 100_000

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class LocalAsrError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExpectedFile:
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class LocalAsrResources:
    isolated_python: Path
    resource_root: Path
    model_dir: Path
    model_files: tuple[ExpectedFile, ...]
    site_packages: Path
    dependency_versions: Mapping[str, str]

    @classmethod
    def discover(cls, isolated_root: str | os.PathLike[str]) -> "LocalAsrResources":
        root = Path(isolated_root)
        if os.name == "nt":
            python = root / "venv" / "Scripts" / "python.exe"
            site_packages = root / "venv" / "Lib" / "site-packages"
        else:
            python = root / "venv" / "bin" / "python"
            candidates = sorted((root / "venv" / "lib").glob("python*/site-packages"))
            if len(candidates) != 1:
                raise LocalAsrError("LOCAL_ASR_RUNTIME_INVALID")
            site_packages = candidates[0]
        versions = _installed_versions(site_packages)
        required = {
            "faster-whisper", "ctranslate2", "av", "tokenizers",
            "huggingface-hub", "onnxruntime", "numpy", "tqdm",
        }
        if not required.issubset(versions):
            raise LocalAsrError("LOCAL_ASR_RUNTIME_INVALID")
        model_dir = root / "models" / "tiny.en"
        files = tuple(
            ExpectedFile(model_dir / name, size, sha256)
            for name, (size, sha256) in sorted(MODEL_FILE_METADATA.items())
        )
        return cls(python, root, model_dir, files, site_packages, versions)


@dataclass(frozen=True)
class LocalAsrResult:
    receipt: dict[str, object]
    binding_sha256: str


def _installed_versions(site_packages: Path) -> dict[str, str]:
    if not site_packages.is_dir():
        raise LocalAsrError("LOCAL_ASR_RUNTIME_INVALID")
    versions: dict[str, str] = {}
    for metadata_path in sorted(site_packages.glob("*.dist-info/METADATA")):
        name = version = None
        try:
            for line in metadata_path.read_text(encoding="utf-8", errors="strict").splitlines():
                if line.startswith("Name: "):
                    name = line[6:].strip().lower().replace("_", "-")
                elif line.startswith("Version: "):
                    version = line[9:].strip()
                if name and version:
                    break
        except (OSError, UnicodeError) as exc:
            raise LocalAsrError("LOCAL_ASR_RUNTIME_INVALID") from exc
        if name and version:
            if name in versions and versions[name] != version:
                raise LocalAsrError("LOCAL_ASR_RUNTIME_INVALID")
            versions[name] = version
    return versions


def _absolute_without_links(path: Path, *, directory: bool = False) -> Path:
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalAsrError("LOCAL_ASR_RESOURCE_MISSING") from exc
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise LocalAsrError("LOCAL_ASR_RESOURCE_LINK_REJECTED")
    if directory != path.is_dir() or (not directory and not path.is_file()):
        raise LocalAsrError("LOCAL_ASR_RESOURCE_INVALID")
    return resolved


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _hash_file(path: Path, *, maximum: int = MAX_RESOURCE_TREE_BYTES) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise LocalAsrError("LOCAL_ASR_RESOURCE_TOO_LARGE")
                digest.update(chunk)
    except LocalAsrError:
        raise
    except OSError as exc:
        raise LocalAsrError("LOCAL_ASR_RESOURCE_INVALID") from exc
    return total, digest.hexdigest()


def _tree_digest(path: Path) -> tuple[int, int, str]:
    root = _absolute_without_links(path, directory=True)
    digest = hashlib.sha256()
    count = total = entries_seen = 0
    try:
        for current, directories, filenames in os.walk(root, followlinks=False):
            directories.sort()
            filenames.sort()
            current_path = Path(current)
            for name in directories:
                entries_seen += 1
                if entries_seen > MAX_RESOURCE_TREE_ENTRIES:
                    raise LocalAsrError("LOCAL_ASR_RESOURCE_TOO_LARGE")
                resolved = _absolute_without_links(current_path / name, directory=True)
                if not _contained(resolved, root):
                    raise LocalAsrError("LOCAL_ASR_RESOURCE_LINK_REJECTED")
            for name in filenames:
                entries_seen += 1
                if entries_seen > MAX_RESOURCE_TREE_ENTRIES:
                    raise LocalAsrError("LOCAL_ASR_RESOURCE_TOO_LARGE")
                entry = current_path / name
                resolved = _absolute_without_links(entry)
                if not _contained(resolved, root):
                    raise LocalAsrError("LOCAL_ASR_RESOURCE_LINK_REJECTED")
                size, entry_hash = _hash_file(entry, maximum=MAX_RESOURCE_TREE_BYTES - total)
                total += size
                count += 1
                relative = entry.relative_to(root).as_posix().encode("utf-8")
                digest.update(len(relative).to_bytes(4, "big"))
                digest.update(relative)
                digest.update(size.to_bytes(8, "big"))
                digest.update(bytes.fromhex(entry_hash))
    except LocalAsrError:
        raise
    except OSError as exc:
        raise LocalAsrError("LOCAL_ASR_RESOURCE_INVALID") from exc
    return count, total, digest.hexdigest()


def _validate_expected_file(expected: ExpectedFile, root: Path) -> dict[str, object]:
    path = _absolute_without_links(expected.path)
    if not _contained(path, root):
        raise LocalAsrError("LOCAL_ASR_RESOURCE_OUTSIDE_ROOT")
    if (
        isinstance(expected.size, bool) or expected.size < 1
        or not isinstance(expected.sha256, str) or not _HEX_64.fullmatch(expected.sha256)
    ):
        raise LocalAsrError("LOCAL_ASR_RESOURCE_INVALID")
    if path.stat().st_size != expected.size:
        raise LocalAsrError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
    size, digest = _hash_file(path, maximum=expected.size)
    if size != expected.size or not hmac.compare_digest(digest, expected.sha256):
        raise LocalAsrError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
    return {"name": path.name, "size": size, "sha256": digest}


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_resource_binding(
    resources: LocalAsrResources,
    *,
    worker_script: Path | None = None,
) -> tuple[dict[str, object], str]:
    worker = worker_script or Path(__file__).with_name("asr_worker.py")
    python = _absolute_without_links(resources.isolated_python)
    root = _absolute_without_links(resources.resource_root, directory=True)
    model_dir = _absolute_without_links(resources.model_dir, directory=True)
    site_packages = _absolute_without_links(resources.site_packages, directory=True)
    worker_path = _absolute_without_links(worker)
    if not _contained(model_dir, root) or not _contained(site_packages, root):
        raise LocalAsrError("LOCAL_ASR_RESOURCE_OUTSIDE_ROOT")
    expected_names = set(MODEL_FILE_METADATA)
    if {entry.path.name for entry in resources.model_files} != expected_names:
        raise LocalAsrError("LOCAL_ASR_MODEL_MANIFEST_INVALID")
    model_files = [
        _validate_expected_file(entry, model_dir)
        for entry in sorted(resources.model_files, key=lambda value: value.path.name)
    ]
    worker_size, worker_hash = _hash_file(worker_path)
    python_size, python_hash = _hash_file(python)
    package_count, package_size, package_hash = _tree_digest(site_packages)
    versions = dict(sorted(resources.dependency_versions.items()))
    if not versions or len(versions) > 128 or any(
        not isinstance(name, str) or not name or not isinstance(version, str) or not version
        for name, version in versions.items()
    ):
        raise LocalAsrError("LOCAL_ASR_RUNTIME_INVALID")
    payload: dict[str, object] = {
        "schema": BINDING_SCHEMA,
        "worker_protocol": WORKER_PROTOCOL,
        "worker": {"size": worker_size, "sha256": worker_hash},
        "python": {"size": python_size, "sha256": python_hash},
        "dependencies": versions,
        "site_packages": {
            "files": package_count, "size": package_size, "tree_sha256": package_hash,
        },
        "model": {
            "id": MODEL_ID, "revision": MODEL_REVISION, "files": model_files,
        },
        "runtime": {
            "device": "cpu", "compute_type": COMPUTE_TYPE,
            "cpu_threads": CPU_THREADS, "num_workers": NUM_WORKERS,
        },
        "task": {
            "language": LANGUAGE, "task": TASK, "beam_size": 1, "best_of": 1,
            "temperature": 0.0, "condition_on_previous_text": False,
            "word_timestamps": True, "vad_filter": False,
        },
        "limits": {
            "sample_rate": SAMPLE_RATE, "max_audio_seconds": MAX_AUDIO_SECONDS,
            "max_segments": MAX_SEGMENTS, "max_words": MAX_WORDS,
        },
    }
    return payload, _canonical_hash(payload)


def _read_wav(path: Path) -> tuple[dict[str, object], bytes]:
    wav_path = _absolute_without_links(path)
    maximum_size = 44 + MAX_AUDIO_SECONDS * SAMPLE_RATE * 2
    try:
        size = wav_path.stat().st_size
        if not 46 <= size <= maximum_size:
            raise LocalAsrError("LOCAL_ASR_AUDIO_INVALID")
        data = wav_path.read_bytes()
        if len(data) != size:
            raise LocalAsrError("LOCAL_ASR_AUDIO_INVALID")
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
            compression = wav.getcomptype()
            payload = wav.readframes(frames)
            if wav.readframes(1):
                raise LocalAsrError("LOCAL_ASR_AUDIO_INVALID")
    except LocalAsrError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise LocalAsrError("LOCAL_ASR_AUDIO_INVALID") from exc
    if (
        channels != 1 or sample_width != 2 or sample_rate != SAMPLE_RATE
        or compression != "NONE" or not 1 <= frames <= MAX_AUDIO_SECONDS * SAMPLE_RATE
        or len(payload) != frames * 2 or size != 44 + len(payload)
    ):
        raise LocalAsrError("LOCAL_ASR_AUDIO_INVALID")
    digest = hashlib.sha256(data).hexdigest()
    return {
        "sha256": digest, "size": size, "sample_rate": sample_rate,
        "channels": channels, "sample_width": sample_width, "frames": frames,
        "duration_ms": frames * 1000 // sample_rate,
    }, data


def _exact_keys(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    return value


def _exact_int(value: object, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    if maximum is not None and value > maximum:
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    return value


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    result = float(value)
    if not math.isfinite(result):
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    return result


def _bounded_text(value: object, maximum: int) -> str:
    if (
        not isinstance(value, str) or not value.strip() or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    return value


def _validate_receipt(
    receipt: object,
    *, request_id: str, binding_sha256: str, source_id: str,
    source_sha256: str, audio: Mapping[str, object], dependencies: Mapping[str, str],
) -> dict[str, object]:
    top = _exact_keys(receipt, {
        "schema", "status", "request_id", "binding_sha256", "source", "audio",
        "model", "task", "segments", "runtime", "limitations",
    })
    if (
        top["schema"] != RECEIPT_SCHEMA or top["status"] != "ok"
        or top["request_id"] != request_id
        or not isinstance(top["binding_sha256"], str)
        or not hmac.compare_digest(top["binding_sha256"], binding_sha256)
    ):
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    source = _exact_keys(top["source"], {"id", "sha256"})
    if source != {"id": source_id, "sha256": source_sha256}:
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    audio_value = _exact_keys(top["audio"], {
        "sha256", "size", "sample_rate", "channels", "sample_width", "frames", "duration_ms",
    })
    if audio_value != dict(audio):
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    model = _exact_keys(top["model"], {"id", "revision"})
    if model != {"id": MODEL_ID, "revision": MODEL_REVISION}:
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    task = _exact_keys(top["task"], {
        "language", "task", "beam_size", "best_of", "temperature",
        "condition_on_previous_text", "word_timestamps", "vad_filter",
    })
    expected_task = {
        "language": LANGUAGE, "task": TASK, "beam_size": 1, "best_of": 1,
        "temperature": 0.0, "condition_on_previous_text": False,
        "word_timestamps": True, "vad_filter": False,
    }
    if task != expected_task:
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    runtime = _exact_keys(top["runtime"], {
        "device", "compute_type", "cpu_threads", "num_workers", "dependencies",
    })
    if runtime != {
        "device": "cpu", "compute_type": COMPUTE_TYPE, "cpu_threads": CPU_THREADS,
        "num_workers": NUM_WORKERS, "dependencies": dict(dependencies),
    }:
        if runtime.get("device") != "cpu" or runtime.get("compute_type") != COMPUTE_TYPE:
            raise LocalAsrError("LOCAL_ASR_CPU_REQUIRED")
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    limitations = _exact_keys(top["limitations"], {
        "machine_proposal_only", "human_review_required", "speaker_identity_inferred",
    })
    if limitations != {
        "machine_proposal_only": True, "human_review_required": True,
        "speaker_identity_inferred": False,
    }:
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    segments = top["segments"]
    if not isinstance(segments, list) or len(segments) > MAX_SEGMENTS:
        raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    duration_ms = _exact_int(audio["duration_ms"], 0, MAX_AUDIO_SECONDS * 1000)
    prior_end = 0
    total_words = 0
    for index, raw_segment in enumerate(segments):
        segment = _exact_keys(raw_segment, {
            "id", "start_ms", "end_ms", "text", "avg_logprob",
            "no_speech_prob", "temperature", "words",
        })
        if segment["id"] != f"asrseg_{index:04d}":
            raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
        start = _exact_int(segment["start_ms"], 0, duration_ms)
        end = _exact_int(segment["end_ms"], 1, duration_ms)
        if start < prior_end or end <= start:
            raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
        prior_end = end
        _bounded_text(segment["text"], MAX_SEGMENT_TEXT_CHARS)
        avg_logprob = _finite_number(segment["avg_logprob"])
        no_speech = _finite_number(segment["no_speech_prob"])
        temperature = _finite_number(segment["temperature"])
        if avg_logprob > 0 or not 0 <= no_speech <= 1 or temperature != 0:
            raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
        words = segment["words"]
        if not isinstance(words, list):
            raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
        total_words += len(words)
        if total_words > MAX_WORDS:
            raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
        prior_word_start = start
        for word_index, raw_word in enumerate(words):
            word = _exact_keys(raw_word, {
                "id", "start_ms", "end_ms", "text", "probability",
            })
            if word["id"] != f"asrword_{index:04d}_{word_index:04d}":
                raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
            word_start = _exact_int(word["start_ms"], start, end)
            word_end = _exact_int(word["end_ms"], word_start, end)
            if word_start < prior_word_start:
                raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
            prior_word_start = word_start
            _bounded_text(word["text"], MAX_WORD_TEXT_CHARS)
            probability = _finite_number(word["probability"])
            if not 0 <= probability <= 1:
                raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID")
    return top


class LocalAsrPilot:
    def __init__(self, resources: LocalAsrResources, *, worker_script: Path | None = None) -> None:
        self.resources = resources
        self.worker_script = worker_script or Path(__file__).with_name("asr_worker.py")

    def binding(self) -> tuple[dict[str, object], str]:
        return build_resource_binding(self.resources, worker_script=self.worker_script)

    def transcribe(
        self,
        source_id: str,
        source_sha256: str,
        wav_path: str | os.PathLike[str],
        job_dir: str | os.PathLike[str],
        *,
        timeout: float = 600,
        cancelled: Callable[[], bool] | None = None,
    ) -> LocalAsrResult:
        if cancelled and cancelled():
            raise LocalAsrError("LOCAL_ASR_CANCELLED")
        if not isinstance(source_id, str) or not _OPAQUE_ID.fullmatch(source_id):
            raise LocalAsrError("LOCAL_ASR_SOURCE_INVALID")
        if not isinstance(source_sha256, str) or not _HEX_64.fullmatch(source_sha256):
            raise LocalAsrError("LOCAL_ASR_SOURCE_INVALID")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise LocalAsrError("LOCAL_ASR_TIMEOUT_INVALID")
        timeout = float(timeout)
        if not math.isfinite(timeout) or not 0 < timeout <= 1_800:
            raise LocalAsrError("LOCAL_ASR_TIMEOUT_INVALID")
        audio, _ = _read_wav(Path(wav_path))
        if not hmac.compare_digest(str(audio["sha256"]), source_sha256):
            raise LocalAsrError("LOCAL_ASR_SOURCE_INTEGRITY_FAILED")
        directory = _absolute_without_links(Path(job_dir), directory=True)
        binding, binding_sha256 = self.binding()
        request_id = "asr_" + uuid4().hex
        request_path = directory / f"{request_id}.request.json"
        receipt_path = directory / f"{request_id}.receipt.json"
        if any(path.exists() or path.is_symlink() for path in (request_path, receipt_path)):
            raise LocalAsrError("LOCAL_ASR_JOB_CONFLICT")
        root = _absolute_without_links(self.resources.resource_root, directory=True)
        model_dir = _absolute_without_links(self.resources.model_dir, directory=True)
        site_packages = _absolute_without_links(self.resources.site_packages, directory=True)
        python = _absolute_without_links(self.resources.isolated_python)
        worker = _absolute_without_links(self.worker_script)
        request = {
            "schema": REQUEST_SCHEMA,
            "request_id": request_id,
            "binding_sha256": binding_sha256,
            "binding": binding,
            "source": {"id": source_id, "sha256": source_sha256},
            "audio": {"path": str(Path(wav_path).resolve(strict=True)), **audio},
            "job_dir": str(directory),
            "resource_root": str(root),
            "python": str(python),
            "worker": str(worker),
            "model_dir": str(model_dir),
            "model_files": [
                {"path": str(entry.path.resolve(strict=True)), "size": entry.size, "sha256": entry.sha256}
                for entry in sorted(self.resources.model_files, key=lambda value: value.path.name)
            ],
            "site_packages": {"path": str(site_packages), **binding["site_packages"]},
            "dependencies": dict(sorted(self.resources.dependency_versions.items())),
        }
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise LocalAsrError("LOCAL_ASR_REQUEST_INVALID")
        try:
            with request_path.open("xb") as stream:
                stream.write(encoded)
            completed = run_command(
                [python, "-I", worker, request_path, receipt_path],
                timeout=timeout, cancelled=cancelled, check=False,
            )
        except InterruptedError as exc:
            raise LocalAsrError("LOCAL_ASR_CANCELLED") from exc
        except TimeoutError as exc:
            raise LocalAsrError("LOCAL_ASR_TRANSCRIPTION_TIMEOUT") from exc
        except (OSError, TypeError, ValueError) as exc:
            raise LocalAsrError("LOCAL_ASR_TRANSCRIPTION_FAILED") from exc
        if not receipt_path.is_file() or receipt_path.is_symlink() or receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
            raise LocalAsrError("LOCAL_ASR_TRANSCRIPTION_FAILED")
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LocalAsrError("LOCAL_ASR_RECEIPT_INVALID") from exc
        if completed.returncode:
            code = None
            if isinstance(receipt, dict) and receipt.get("schema") == RECEIPT_SCHEMA:
                error = receipt.get("error")
                if isinstance(error, dict):
                    code = error.get("code")
            allowed = {
                "LOCAL_ASR_REQUEST_INVALID", "LOCAL_ASR_SOURCE_INVALID",
                "LOCAL_ASR_SOURCE_INTEGRITY_FAILED", "LOCAL_ASR_AUDIO_INVALID",
                "LOCAL_ASR_RESOURCE_MISSING", "LOCAL_ASR_RESOURCE_INVALID",
                "LOCAL_ASR_RESOURCE_LINK_REJECTED", "LOCAL_ASR_RESOURCE_OUTSIDE_ROOT",
                "LOCAL_ASR_RESOURCE_INTEGRITY_FAILED", "LOCAL_ASR_MODEL_MANIFEST_INVALID",
                "LOCAL_ASR_RUNTIME_INVALID", "LOCAL_ASR_CPU_REQUIRED",
                "LOCAL_ASR_PROPOSAL_INVALID", "LOCAL_ASR_TRANSCRIPTION_FAILED",
            }
            raise LocalAsrError(code if code in allowed else "LOCAL_ASR_TRANSCRIPTION_FAILED")
        receipt = _validate_receipt(
            receipt, request_id=request_id, binding_sha256=binding_sha256,
            source_id=source_id, source_sha256=source_sha256, audio=audio,
            dependencies=self.resources.dependency_versions,
        )
        after_audio, _ = _read_wav(Path(wav_path))
        if after_audio != audio:
            raise LocalAsrError("LOCAL_ASR_SOURCE_INTEGRITY_FAILED")
        _, after_binding = self.binding()
        if not hmac.compare_digest(after_binding, binding_sha256):
            raise LocalAsrError("LOCAL_ASR_BINDING_MISMATCH")
        return LocalAsrResult(receipt=receipt, binding_sha256=binding_sha256)
