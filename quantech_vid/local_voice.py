from __future__ import annotations

import hashlib
import hmac
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


MODEL_FILENAME = "kokoro-v1.0.onnx"
MODEL_SIZE = 325_505_369
MODEL_SHA256 = "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"  # pragma: allowlist secret -- public release digest, not a credential
VOICES_FILENAME = "voices-v1.0.bin"
VOICES_SIZE = 28_214_398
VOICES_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"  # pragma: allowlist secret -- public release digest, not a credential

REQUEST_SCHEMA = "quantech.local-voice.request.v1"
RECEIPT_SCHEMA = "quantech.local-voice.receipt.v1"
BINDING_SCHEMA = "quantech.local-voice.binding.v1"
WORKER_PROTOCOL = "kokoro-cpu-pilot-v1"
PILOT_VOICE = "af_heart"
PILOT_LANGUAGE = "en-us"
PILOT_SPEED = 1.0
MAX_TEXT_CHARS = 1_000
MAX_AUDIO_SECONDS = 60
SAMPLE_RATE = 24_000
MAX_RECEIPT_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 32 * 1024
MAX_RESOURCE_TREE_BYTES = 1024 * 1024 * 1024
MAX_RESOURCE_TREE_ENTRIES = 100_000

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class LocalVoiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExpectedFile:
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class LocalVoiceResources:
    isolated_python: Path
    resource_root: Path
    model: ExpectedFile
    voices: ExpectedFile
    site_packages: Path
    dependency_versions: Mapping[str, str]
    espeak_library: Path
    espeak_data: Path

    @classmethod
    def discover(cls, isolated_root: str | os.PathLike[str]) -> "LocalVoiceResources":
        root = Path(isolated_root)
        if os.name == "nt":
            python = root / "venv" / "Scripts" / "python.exe"
            site_packages = root / "venv" / "Lib" / "site-packages"
            espeak_library = site_packages / "espeakng_loader" / "espeak-ng.dll"
        else:
            python = root / "venv" / "bin" / "python"
            candidates = sorted((root / "venv" / "lib").glob("python*/site-packages"))
            if len(candidates) != 1:
                raise LocalVoiceError("LOCAL_VOICE_RUNTIME_INVALID")
            site_packages = candidates[0]
            library_candidates = sorted((site_packages / "espeakng_loader").glob("libespeak-ng.*"))
            if len(library_candidates) != 1:
                raise LocalVoiceError("LOCAL_VOICE_RUNTIME_INVALID")
            espeak_library = library_candidates[0]
        versions = _installed_versions(site_packages)
        required = {
            "kokoro-onnx", "numpy", "onnxruntime", "phonemizer", "espeakng-loader"
        }
        if not required.issubset(versions):
            raise LocalVoiceError("LOCAL_VOICE_RUNTIME_INVALID")
        model_path = root / "models" / MODEL_FILENAME
        voices_path = root / "models" / VOICES_FILENAME
        return cls(
            isolated_python=python,
            resource_root=root,
            model=ExpectedFile(model_path, MODEL_SIZE, MODEL_SHA256),
            voices=ExpectedFile(voices_path, VOICES_SIZE, VOICES_SHA256),
            site_packages=site_packages,
            dependency_versions=versions,
            espeak_library=espeak_library,
            espeak_data=site_packages / "espeakng_loader" / "espeak-ng-data",
        )


@dataclass(frozen=True)
class LocalVoiceResult:
    wav_path: Path
    receipt: dict[str, object]
    binding_sha256: str


def _installed_versions(site_packages: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    if not site_packages.is_dir():
        raise LocalVoiceError("LOCAL_VOICE_RUNTIME_INVALID")
    for metadata_path in sorted(site_packages.glob("*.dist-info/METADATA")):
        name = version = None
        try:
            for line in metadata_path.read_text(encoding="utf-8", errors="strict").splitlines():
                if line.startswith("Name: "):
                    name = line[6:].strip().lower()
                elif line.startswith("Version: "):
                    version = line[9:].strip()
                if name and version:
                    break
        except (OSError, UnicodeError) as exc:
            raise LocalVoiceError("LOCAL_VOICE_RUNTIME_INVALID") from exc
        if name and version:
            if name in versions and versions[name] != version:
                raise LocalVoiceError("LOCAL_VOICE_RUNTIME_INVALID")
            versions[name] = version
    return versions


def _absolute_without_links(path: Path, *, directory: bool = False) -> Path:
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
        stat_result = path.stat()
    except (OSError, RuntimeError) as exc:
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_MISSING") from exc
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_LINK_REJECTED")
    if directory != path.is_dir() or (not directory and not path.is_file()):
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_INVALID")
    if not directory and not stat_result.st_size >= 0:
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_INVALID")
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
                    raise LocalVoiceError("LOCAL_VOICE_RESOURCE_TOO_LARGE")
                digest.update(chunk)
    except LocalVoiceError:
        raise
    except OSError as exc:
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_INVALID") from exc
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
                    raise LocalVoiceError("LOCAL_VOICE_RESOURCE_TOO_LARGE")
                resolved = _absolute_without_links(current_path / name, directory=True)
                if not _contained(resolved, root):
                    raise LocalVoiceError("LOCAL_VOICE_RESOURCE_LINK_REJECTED")
            for name in filenames:
                entries_seen += 1
                if entries_seen > MAX_RESOURCE_TREE_ENTRIES:
                    raise LocalVoiceError("LOCAL_VOICE_RESOURCE_TOO_LARGE")
                entry = current_path / name
                resolved = _absolute_without_links(entry)
                if not _contained(resolved, root):
                    raise LocalVoiceError("LOCAL_VOICE_RESOURCE_LINK_REJECTED")
                size, file_hash = _hash_file(entry, maximum=MAX_RESOURCE_TREE_BYTES - total)
                total += size
                count += 1
                relative = entry.relative_to(root).as_posix().encode("utf-8")
                digest.update(len(relative).to_bytes(4, "big"))
                digest.update(relative)
                digest.update(size.to_bytes(8, "big"))
                digest.update(bytes.fromhex(file_hash))
    except LocalVoiceError:
        raise
    except OSError as exc:
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_INVALID") from exc
    return count, total, digest.hexdigest()


def _validate_expected_file(expected: ExpectedFile, root: Path | None = None) -> dict[str, object]:
    path = _absolute_without_links(expected.path)
    if root is not None and not _contained(path, root):
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_OUTSIDE_ROOT")
    if isinstance(expected.size, bool) or expected.size < 1 or not _HEX_64.fullmatch(expected.sha256):
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_INVALID")
    if path.stat().st_size != expected.size:
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED")
    size, digest = _hash_file(path, maximum=expected.size)
    if size != expected.size or not hmac.compare_digest(digest, expected.sha256):
        raise LocalVoiceError("LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED")
    return {"path": str(path), "size": size, "sha256": digest}


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_resource_binding(
    resources: LocalVoiceResources,
    *,
    worker_script: Path | None = None,
) -> tuple[dict[str, object], str]:
    worker = worker_script or Path(__file__).with_name("kokoro_worker.py")
    python = _absolute_without_links(resources.isolated_python)
    root = _absolute_without_links(resources.resource_root, directory=True)
    site_packages = _absolute_without_links(resources.site_packages, directory=True)
    espeak_library = _absolute_without_links(resources.espeak_library)
    espeak_data = _absolute_without_links(resources.espeak_data, directory=True)
    worker_path = _absolute_without_links(worker)
    model = _validate_expected_file(resources.model, root)
    voices = _validate_expected_file(resources.voices, root)
    worker_size, worker_hash = _hash_file(worker_path)
    python_size, python_hash = _hash_file(python)
    espeak_size, espeak_hash = _hash_file(espeak_library)
    espeak_count, espeak_tree_size, espeak_tree_hash = _tree_digest(espeak_data)
    package_count, package_size, package_tree_hash = _tree_digest(site_packages)
    versions = dict(sorted(resources.dependency_versions.items()))
    if not versions or any(
        not isinstance(name, str) or not name or not isinstance(version, str) or not version
        for name, version in versions.items()
    ):
        raise LocalVoiceError("LOCAL_VOICE_RUNTIME_INVALID")
    payload: dict[str, object] = {
        "schema": BINDING_SCHEMA,
        "worker_protocol": WORKER_PROTOCOL,
        "worker": {"size": worker_size, "sha256": worker_hash},
        "python": {"size": python_size, "sha256": python_hash},
        "dependencies": versions,
        "site_packages": {
            "files": package_count,
            "size": package_size,
            "tree_sha256": package_tree_hash,
        },
        "model": {key: model[key] for key in ("size", "sha256")},
        "voices": {key: voices[key] for key in ("size", "sha256")},
        "espeak": {
            "library_size": espeak_size,
            "library_sha256": espeak_hash,
            "data_files": espeak_count,
            "data_size": espeak_tree_size,
            "data_tree_sha256": espeak_tree_hash,
        },
        "execution_providers": ["CPUExecutionProvider"],
        "voice": PILOT_VOICE,
        "language": PILOT_LANGUAGE,
        "speed": PILOT_SPEED,
        "sample_rate": SAMPLE_RATE,
        "max_text_chars": MAX_TEXT_CHARS,
        "max_audio_seconds": MAX_AUDIO_SECONDS,
    }
    return payload, _canonical_hash(payload)


def _validate_text(text: str) -> None:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise LocalVoiceError("LOCAL_VOICE_TEXT_INVALID")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in text):
        raise LocalVoiceError("LOCAL_VOICE_TEXT_INVALID")


def _validate_wav(path: Path) -> dict[str, object]:
    wav_path = _absolute_without_links(path)
    size = wav_path.stat().st_size
    maximum_size = 44 + MAX_AUDIO_SECONDS * SAMPLE_RATE * 2
    if size < 46 or size > maximum_size:
        raise LocalVoiceError("LOCAL_VOICE_AUDIO_INVALID")
    try:
        with wave.open(str(wav_path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
            compression = wav.getcomptype()
            payload = wav.readframes(frames)
            if wav.readframes(1):
                raise LocalVoiceError("LOCAL_VOICE_AUDIO_INVALID")
    except LocalVoiceError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise LocalVoiceError("LOCAL_VOICE_AUDIO_INVALID") from exc
    if (
        channels != 1
        or sample_width != 2
        or sample_rate != SAMPLE_RATE
        or compression != "NONE"
        or not 1 <= frames <= MAX_AUDIO_SECONDS * SAMPLE_RATE
        or len(payload) != frames * channels * sample_width
        or size != 44 + len(payload)
    ):
        raise LocalVoiceError("LOCAL_VOICE_AUDIO_INVALID")
    _, digest = _hash_file(wav_path, maximum=maximum_size)
    return {
        "sha256": digest,
        "size": size,
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width": sample_width,
        "frames": frames,
        "duration_ms": frames * 1000 // sample_rate,
    }


def _exact_keys(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise LocalVoiceError("LOCAL_VOICE_RECEIPT_INVALID")
    return value


def _exact_int(value: object, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LocalVoiceError("LOCAL_VOICE_RECEIPT_INVALID")
    return value


def _validate_receipt(
    receipt: object,
    *,
    request_id: str,
    binding_sha256: str,
    dependencies: Mapping[str, str],
    wav: Mapping[str, object],
) -> dict[str, object]:
    top = _exact_keys(
        receipt,
        {"schema", "status", "request_id", "binding_sha256", "voice", "language",
         "speed", "audio", "runtime"},
    )
    if (
        top["schema"] != RECEIPT_SCHEMA
        or top["status"] != "ok"
        or top["request_id"] != request_id
        or not hmac.compare_digest(str(top["binding_sha256"]), binding_sha256)
        or top["voice"] != PILOT_VOICE
        or top["language"] != PILOT_LANGUAGE
        or top["speed"] != PILOT_SPEED
    ):
        raise LocalVoiceError("LOCAL_VOICE_RECEIPT_INVALID")
    audio = _exact_keys(
        top["audio"],
        {"sha256", "size", "sample_rate", "channels", "sample_width", "frames", "duration_ms"},
    )
    for key in ("size", "sample_rate", "channels", "sample_width", "frames", "duration_ms"):
        _exact_int(audio[key], 1 if key != "duration_ms" else 0)
    if any(audio[key] != wav[key] for key in audio):
        raise LocalVoiceError("LOCAL_VOICE_RECEIPT_INVALID")
    runtime = _exact_keys(top["runtime"], {"providers", "dependencies"})
    if runtime["providers"] != ["CPUExecutionProvider"]:
        raise LocalVoiceError("LOCAL_VOICE_CPU_REQUIRED")
    if runtime["dependencies"] != dict(dependencies):
        raise LocalVoiceError("LOCAL_VOICE_RECEIPT_INVALID")
    return top


class LocalVoicePilot:
    def __init__(self, resources: LocalVoiceResources, *, worker_script: Path | None = None) -> None:
        self.resources = resources
        self.worker_script = worker_script or Path(__file__).with_name("kokoro_worker.py")

    def binding(self) -> tuple[dict[str, object], str]:
        return build_resource_binding(self.resources, worker_script=self.worker_script)

    def synthesize(
        self,
        text: str,
        job_dir: str | os.PathLike[str],
        *,
        timeout: float = 180,
        cancelled: Callable[[], bool] | None = None,
    ) -> LocalVoiceResult:
        _validate_text(text)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise LocalVoiceError("LOCAL_VOICE_TIMEOUT_INVALID")
        timeout = float(timeout)
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise LocalVoiceError("LOCAL_VOICE_TIMEOUT_INVALID")
        if cancelled and cancelled():
            raise LocalVoiceError("LOCAL_VOICE_CANCELLED")
        directory = _absolute_without_links(Path(job_dir), directory=True)
        binding, binding_sha256 = self.binding()
        request_id = "lv_" + uuid4().hex
        if not _OPAQUE_ID.fullmatch(request_id):
            raise LocalVoiceError("LOCAL_VOICE_REQUEST_INVALID")
        request_path = directory / f"{request_id}.request.json"
        receipt_path = directory / f"{request_id}.receipt.json"
        wav_path = directory / f"{request_id}.wav"
        if any(path.exists() or path.is_symlink() for path in (request_path, receipt_path, wav_path)):
            raise LocalVoiceError("LOCAL_VOICE_JOB_CONFLICT")

        root = _absolute_without_links(self.resources.resource_root, directory=True)
        site_packages = _absolute_without_links(self.resources.site_packages, directory=True)
        espeak_library = _absolute_without_links(self.resources.espeak_library)
        espeak_data = _absolute_without_links(self.resources.espeak_data, directory=True)
        request = {
            "schema": REQUEST_SCHEMA,
            "request_id": request_id,
            "binding_sha256": binding_sha256,
            "text": text,
            "voice": PILOT_VOICE,
            "language": PILOT_LANGUAGE,
            "speed": PILOT_SPEED,
            "job_dir": str(directory),
            "output_wav": str(wav_path),
            "resource_root": str(root),
            "model": {
                "path": str(self.resources.model.path.resolve(strict=True)),
                "size": self.resources.model.size,
                "sha256": self.resources.model.sha256,
            },
            "voices": {
                "path": str(self.resources.voices.path.resolve(strict=True)),
                "size": self.resources.voices.size,
                "sha256": self.resources.voices.sha256,
            },
            "site_packages": {
                "path": str(site_packages),
                **binding["site_packages"],
            },
            "dependencies": dict(self.resources.dependency_versions),
            "espeak": {
                "library_path": str(espeak_library),
                "library_size": binding["espeak"]["library_size"],
                "library_sha256": binding["espeak"]["library_sha256"],
                "data_path": str(espeak_data),
                "data_files": binding["espeak"]["data_files"],
                "data_size": binding["espeak"]["data_size"],
                "data_tree_sha256": binding["espeak"]["data_tree_sha256"],
            },
            "max_audio_seconds": MAX_AUDIO_SECONDS,
        }
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise LocalVoiceError("LOCAL_VOICE_REQUEST_INVALID")
        try:
            with request_path.open("xb") as stream:
                stream.write(encoded)
            completed = run_command(
                [self.resources.isolated_python, "-I", self.worker_script, request_path, receipt_path],
                timeout=timeout,
                cancelled=cancelled,
                check=False,
            )
        except InterruptedError as exc:
            raise LocalVoiceError("LOCAL_VOICE_CANCELLED") from exc
        except TimeoutError as exc:
            raise LocalVoiceError("LOCAL_VOICE_SYNTHESIS_TIMEOUT") from exc
        except (OSError, ValueError, TypeError) as exc:
            raise LocalVoiceError("LOCAL_VOICE_SYNTHESIS_FAILED") from exc
        if not receipt_path.is_file() or receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
            raise LocalVoiceError("LOCAL_VOICE_SYNTHESIS_FAILED")
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LocalVoiceError("LOCAL_VOICE_RECEIPT_INVALID") from exc
        if completed.returncode:
            code = None
            if isinstance(receipt, dict) and receipt.get("schema") == RECEIPT_SCHEMA:
                error = receipt.get("error")
                if isinstance(error, dict):
                    code = error.get("code")
            allowed = {
                "LOCAL_VOICE_REQUEST_INVALID", "LOCAL_VOICE_RESOURCE_MISSING",
                "LOCAL_VOICE_RESOURCE_INVALID", "LOCAL_VOICE_RESOURCE_LINK_REJECTED",
                "LOCAL_VOICE_RESOURCE_OUTSIDE_ROOT", "LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED",
                "LOCAL_VOICE_ARCHIVE_INVALID", "LOCAL_VOICE_VOICE_NOT_ALLOWED",
                "LOCAL_VOICE_CPU_REQUIRED", "LOCAL_VOICE_AUDIO_INVALID",
                "LOCAL_VOICE_RUNTIME_INVALID", "LOCAL_VOICE_SYNTHESIS_FAILED",
            }
            raise LocalVoiceError(code if code in allowed else "LOCAL_VOICE_SYNTHESIS_FAILED")
        wav = _validate_wav(wav_path)
        receipt = _validate_receipt(
            receipt,
            request_id=request_id,
            binding_sha256=binding_sha256,
            dependencies=self.resources.dependency_versions,
            wav=wav,
        )
        _, after_sha256 = self.binding()
        if not hmac.compare_digest(after_sha256, binding_sha256):
            raise LocalVoiceError("LOCAL_VOICE_BINDING_MISMATCH")
        return LocalVoiceResult(wav_path, receipt, binding_sha256)
