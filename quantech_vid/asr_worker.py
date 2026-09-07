from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import io
import json
import math
import os
import re
import sys
import wave
from pathlib import Path


REQUEST_SCHEMA = "quantech.local-asr.request.v1"
RECEIPT_SCHEMA = "quantech.local-asr.receipt.v1"
BINDING_SCHEMA = "quantech.local-asr.binding.v1"
WORKER_PROTOCOL = "faster-whisper-cpu-pilot-v1"
MODEL_ID = "Systran/faster-whisper-tiny.en"
MODEL_REVISION = "7d45cf02c1ed72d240c0dbf99d544d19bef1b5a3"  # pragma: allowlist secret -- immutable public upstream revision
MODEL_FILE_NAMES = {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"}
SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 300
MAX_SEGMENTS = 128
MAX_WORDS = 4_096
MAX_SEGMENT_TEXT_CHARS = 1_000
MAX_WORD_TEXT_CHARS = 200
CPU_THREADS = 2
NUM_WORKERS = 1
COMPUTE_TYPE = "int8_float32"
LANGUAGE = "en"
TASK = "transcribe"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_RESOURCE_TREE_BYTES = 2 * 1024 * 1024 * 1024
MAX_RESOURCE_TREE_ENTRIES = 100_000

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_OUTPUT_SINK = None


class WorkerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _scrub_environment() -> None:
    global _OUTPUT_SINK
    retained = {}
    for name in ("SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            retained[name] = value
    retained.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": str(CPU_THREADS),
        "MKL_NUM_THREADS": str(CPU_THREADS),
        "OPENBLAS_NUM_THREADS": str(CPU_THREADS),
    })
    os.environ.clear()
    os.environ.update(retained)
    sys.dont_write_bytecode = True
    _OUTPUT_SINK = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _OUTPUT_SINK
    sys.stderr = _OUTPUT_SINK


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _exact_keys(value: object, keys: set[str], code: str = "LOCAL_ASR_REQUEST_INVALID") -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorkerError(code)
    return value


def _exact_int(
    value: object, minimum: int = 0, maximum: int | None = None,
    code: str = "LOCAL_ASR_REQUEST_INVALID",
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise WorkerError(code)
    if maximum is not None and value > maximum:
        raise WorkerError(code)
    return value


def _finite_number(value: object, code: str = "LOCAL_ASR_PROPOSAL_INVALID") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkerError(code)
    result = float(value)
    if not math.isfinite(result):
        raise WorkerError(code)
    return result


def _bounded_text(value: object, maximum: int) -> str:
    if (
        not isinstance(value, str) or not value.strip() or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
    return value


def _absolute_without_links(path_value: object, *, directory: bool = False) -> Path:
    if not isinstance(path_value, str) or not path_value or "\x00" in path_value:
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    path = Path(path_value)
    if not path.is_absolute():
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkerError("LOCAL_ASR_RESOURCE_MISSING") from exc
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise WorkerError("LOCAL_ASR_RESOURCE_LINK_REJECTED")
    if directory != path.is_dir() or (not directory and not path.is_file()):
        raise WorkerError("LOCAL_ASR_RESOURCE_INVALID")
    return resolved


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _hash_file(path: Path, maximum: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise WorkerError("LOCAL_ASR_RESOURCE_INVALID")
                digest.update(chunk)
    except WorkerError:
        raise
    except OSError as exc:
        raise WorkerError("LOCAL_ASR_RESOURCE_INVALID") from exc
    return total, digest.hexdigest()


def _tree_digest(path: Path) -> tuple[int, int, str]:
    root = _absolute_without_links(str(path), directory=True)
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
                    raise WorkerError("LOCAL_ASR_RESOURCE_INVALID")
                resolved = _absolute_without_links(str(current_path / name), directory=True)
                if not _contained(resolved, root):
                    raise WorkerError("LOCAL_ASR_RESOURCE_LINK_REJECTED")
            for name in filenames:
                entries_seen += 1
                if entries_seen > MAX_RESOURCE_TREE_ENTRIES:
                    raise WorkerError("LOCAL_ASR_RESOURCE_INVALID")
                entry = current_path / name
                resolved = _absolute_without_links(str(entry))
                if not _contained(resolved, root):
                    raise WorkerError("LOCAL_ASR_RESOURCE_LINK_REJECTED")
                size, entry_hash = _hash_file(entry, MAX_RESOURCE_TREE_BYTES - total)
                total += size
                count += 1
                relative = entry.relative_to(root).as_posix().encode("utf-8")
                digest.update(len(relative).to_bytes(4, "big"))
                digest.update(relative)
                digest.update(size.to_bytes(8, "big"))
                digest.update(bytes.fromhex(entry_hash))
    except WorkerError:
        raise
    except OSError as exc:
        raise WorkerError("LOCAL_ASR_RESOURCE_INVALID") from exc
    return count, total, digest.hexdigest()


def _validate_bound_file(path: Path, bound: object, *, root: Path) -> Path:
    metadata = _exact_keys(bound, {"size", "sha256"})
    if not _contained(path, root):
        raise WorkerError("LOCAL_ASR_RESOURCE_OUTSIDE_ROOT")
    expected_size = _exact_int(metadata["size"], 1, MAX_RESOURCE_TREE_BYTES)
    expected_hash = metadata["sha256"]
    if not isinstance(expected_hash, str) or not _HEX_64.fullmatch(expected_hash):
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    if path.stat().st_size != expected_size:
        raise WorkerError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
    size, digest = _hash_file(path, expected_size)
    if size != expected_size or not hmac.compare_digest(digest, expected_hash):
        raise WorkerError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
    return path


def _validate_binding(value: object, expected_sha256: str) -> dict[str, object]:
    binding = _exact_keys(value, {
        "schema", "worker_protocol", "worker", "python", "dependencies",
        "site_packages", "model", "runtime", "task", "limits",
    })
    if (
        binding["schema"] != BINDING_SCHEMA
        or binding["worker_protocol"] != WORKER_PROTOCOL
        or not hmac.compare_digest(_canonical_hash(binding), expected_sha256)
    ):
        raise WorkerError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
    runtime = _exact_keys(binding["runtime"], {"device", "compute_type", "cpu_threads", "num_workers"})
    if runtime != {
        "device": "cpu", "compute_type": COMPUTE_TYPE,
        "cpu_threads": CPU_THREADS, "num_workers": NUM_WORKERS,
    }:
        raise WorkerError("LOCAL_ASR_CPU_REQUIRED")
    task = _exact_keys(binding["task"], {
        "language", "task", "beam_size", "best_of", "temperature",
        "condition_on_previous_text", "word_timestamps", "vad_filter",
    })
    if task != {
        "language": LANGUAGE, "task": TASK, "beam_size": 1, "best_of": 1,
        "temperature": 0.0, "condition_on_previous_text": False,
        "word_timestamps": True, "vad_filter": False,
    }:
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    limits = _exact_keys(binding["limits"], {
        "sample_rate", "max_audio_seconds", "max_segments", "max_words",
    })
    if limits != {
        "sample_rate": SAMPLE_RATE, "max_audio_seconds": MAX_AUDIO_SECONDS,
        "max_segments": MAX_SEGMENTS, "max_words": MAX_WORDS,
    }:
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    return binding


def _read_wav(path: Path) -> tuple[dict[str, object], bytes]:
    maximum_size = 44 + MAX_AUDIO_SECONDS * SAMPLE_RATE * 2
    try:
        size = path.stat().st_size
        if not 46 <= size <= maximum_size:
            raise WorkerError("LOCAL_ASR_AUDIO_INVALID")
        data = path.read_bytes()
        if len(data) != size:
            raise WorkerError("LOCAL_ASR_AUDIO_INVALID")
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
            compression = wav.getcomptype()
            pcm = wav.readframes(frames)
            if wav.readframes(1):
                raise WorkerError("LOCAL_ASR_AUDIO_INVALID")
    except WorkerError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise WorkerError("LOCAL_ASR_AUDIO_INVALID") from exc
    if (
        channels != 1 or sample_width != 2 or sample_rate != SAMPLE_RATE
        or compression != "NONE" or not 1 <= frames <= MAX_AUDIO_SECONDS * SAMPLE_RATE
        or len(pcm) != frames * 2 or size != 44 + len(pcm)
    ):
        raise WorkerError("LOCAL_ASR_AUDIO_INVALID")
    return {
        "sha256": hashlib.sha256(data).hexdigest(), "size": size,
        "sample_rate": sample_rate, "channels": channels, "sample_width": sample_width,
        "frames": frames, "duration_ms": frames * 1000 // sample_rate,
    }, pcm


def _validate_request(payload: object, request_path: Path, receipt_path: Path) -> dict[str, object]:
    request = _exact_keys(payload, {
        "schema", "request_id", "binding_sha256", "binding", "source", "audio",
        "job_dir", "resource_root", "python", "worker", "model_dir", "model_files",
        "site_packages", "dependencies",
    })
    if request["schema"] != REQUEST_SCHEMA:
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    request_id = request["request_id"]
    binding_sha256 = request["binding_sha256"]
    if not isinstance(request_id, str) or not _OPAQUE_ID.fullmatch(request_id):
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    if not isinstance(binding_sha256, str) or not _HEX_64.fullmatch(binding_sha256):
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    binding = _validate_binding(request["binding"], binding_sha256)

    job_dir = _absolute_without_links(request["job_dir"], directory=True)
    request_file = _absolute_without_links(str(request_path))
    receipt_absolute = Path(os.path.abspath(receipt_path))
    if (
        request_file.parent != job_dir or receipt_absolute.parent != job_dir
        or receipt_path.exists() or receipt_path.is_symlink()
    ):
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    request["job_dir"] = job_dir

    root = _absolute_without_links(request["resource_root"], directory=True)
    python = _absolute_without_links(request["python"])
    worker = _absolute_without_links(request["worker"])
    if (
        os.path.normcase(str(python)) != os.path.normcase(str(Path(sys.executable).resolve(strict=True)))
        or os.path.normcase(str(worker)) != os.path.normcase(str(Path(__file__).resolve(strict=True)))
    ):
        raise WorkerError("LOCAL_ASR_RUNTIME_INVALID")
    _validate_bound_file(python, binding["python"], root=root)
    _validate_bound_file(worker, binding["worker"], root=worker.parent)

    model_binding = _exact_keys(binding["model"], {"id", "revision", "files"})
    if model_binding["id"] != MODEL_ID or model_binding["revision"] != MODEL_REVISION:
        raise WorkerError("LOCAL_ASR_MODEL_MANIFEST_INVALID")
    model_dir = _absolute_without_links(request["model_dir"], directory=True)
    if not _contained(model_dir, root):
        raise WorkerError("LOCAL_ASR_RESOURCE_OUTSIDE_ROOT")
    bound_files = model_binding["files"]
    request_files = request["model_files"]
    if not isinstance(bound_files, list) or not isinstance(request_files, list) or len(bound_files) != 4 or len(request_files) != 4:
        raise WorkerError("LOCAL_ASR_MODEL_MANIFEST_INVALID")
    bound_by_name: dict[str, dict[str, object]] = {}
    for raw in bound_files:
        item = _exact_keys(raw, {"name", "size", "sha256"})
        name = item["name"]
        if not isinstance(name, str) or name not in MODEL_FILE_NAMES or name in bound_by_name:
            raise WorkerError("LOCAL_ASR_MODEL_MANIFEST_INVALID")
        bound_by_name[name] = item
    if set(bound_by_name) != MODEL_FILE_NAMES:
        raise WorkerError("LOCAL_ASR_MODEL_MANIFEST_INVALID")
    seen: set[str] = set()
    for raw in request_files:
        item = _exact_keys(raw, {"path", "size", "sha256"})
        path = _absolute_without_links(item["path"])
        name = path.name
        if path.parent != model_dir or name in seen or name not in MODEL_FILE_NAMES:
            raise WorkerError("LOCAL_ASR_MODEL_MANIFEST_INVALID")
        bound = bound_by_name[name]
        if item["size"] != bound["size"] or item["sha256"] != bound["sha256"]:
            raise WorkerError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
        _validate_bound_file(path, {"size": item["size"], "sha256": item["sha256"]}, root=model_dir)
        seen.add(name)
    if seen != MODEL_FILE_NAMES:
        raise WorkerError("LOCAL_ASR_MODEL_MANIFEST_INVALID")
    request["model_dir"] = model_dir

    packages = _exact_keys(request["site_packages"], {"path", "files", "size", "tree_sha256"})
    bound_packages = _exact_keys(binding["site_packages"], {"files", "size", "tree_sha256"})
    if {key: packages[key] for key in bound_packages} != bound_packages:
        raise WorkerError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
    site_packages = _absolute_without_links(packages["path"], directory=True)
    if not _contained(site_packages, root):
        raise WorkerError("LOCAL_ASR_RESOURCE_OUTSIDE_ROOT")
    expected_count = _exact_int(packages["files"], 1, MAX_RESOURCE_TREE_ENTRIES)
    expected_size = _exact_int(packages["size"], 1, MAX_RESOURCE_TREE_BYTES)
    expected_hash = packages["tree_sha256"]
    if not isinstance(expected_hash, str) or not _HEX_64.fullmatch(expected_hash):
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    count, size, digest = _tree_digest(site_packages)
    if count != expected_count or size != expected_size or not hmac.compare_digest(digest, expected_hash):
        raise WorkerError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
    request["site_packages"] = site_packages

    dependencies = request["dependencies"]
    if not isinstance(dependencies, dict) or not dependencies or len(dependencies) > 128:
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    for name, version in dependencies.items():
        if (
            not isinstance(name, str) or not _PACKAGE_NAME.fullmatch(name)
            or not isinstance(version, str) or not 0 < len(version) <= 120
        ):
            raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    dependencies = dict(sorted(dependencies.items()))
    if dependencies != binding["dependencies"]:
        raise WorkerError("LOCAL_ASR_RESOURCE_INTEGRITY_FAILED")
    request["dependencies"] = dependencies

    source = _exact_keys(request["source"], {"id", "sha256"})
    if not isinstance(source["id"], str) or not _OPAQUE_ID.fullmatch(source["id"]):
        raise WorkerError("LOCAL_ASR_SOURCE_INVALID")
    if not isinstance(source["sha256"], str) or not _HEX_64.fullmatch(source["sha256"]):
        raise WorkerError("LOCAL_ASR_SOURCE_INVALID")
    audio = _exact_keys(request["audio"], {
        "path", "sha256", "size", "sample_rate", "channels", "sample_width", "frames", "duration_ms",
    })
    audio_path = _absolute_without_links(audio["path"])
    actual_audio, pcm = _read_wav(audio_path)
    if any(audio.get(key) != value for key, value in actual_audio.items()):
        raise WorkerError("LOCAL_ASR_SOURCE_INTEGRITY_FAILED")
    if not hmac.compare_digest(str(actual_audio["sha256"]), source["sha256"]):
        raise WorkerError("LOCAL_ASR_SOURCE_INTEGRITY_FAILED")
    request["audio"] = actual_audio
    request["pcm"] = pcm
    return request


def _versions(expected: dict[str, str]) -> dict[str, str]:
    actual: dict[str, str] = {}
    try:
        for name, expected_version in expected.items():
            version = importlib.metadata.version(name)
            if version != expected_version:
                raise WorkerError("LOCAL_ASR_RUNTIME_INVALID")
            actual[name] = version
    except importlib.metadata.PackageNotFoundError as exc:
        raise WorkerError("LOCAL_ASR_RUNTIME_INVALID") from exc
    return actual


def _milliseconds(value: object, duration_ms: int) -> int:
    seconds = _finite_number(value)
    milliseconds = int(round(seconds * 1000))
    if not 0 <= milliseconds <= duration_ms:
        raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
    return milliseconds


def _build_segments(raw_segments: object, duration_ms: int) -> list[dict[str, object]]:
    try:
        iterator = iter(raw_segments)
    except TypeError as exc:
        raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID") from exc
    result: list[dict[str, object]] = []
    total_words = 0
    prior_end = 0
    try:
        for index, raw in enumerate(iterator):
            if index >= MAX_SEGMENTS:
                raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
            start = _milliseconds(getattr(raw, "start"), duration_ms)
            end = _milliseconds(getattr(raw, "end"), duration_ms)
            if start < prior_end or end <= start:
                raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
            prior_end = end
            text = _bounded_text(getattr(raw, "text"), MAX_SEGMENT_TEXT_CHARS)
            avg_logprob = _finite_number(getattr(raw, "avg_logprob"))
            no_speech_prob = _finite_number(getattr(raw, "no_speech_prob"))
            temperature = _finite_number(getattr(raw, "temperature"))
            if avg_logprob > 0 or not 0 <= no_speech_prob <= 1 or temperature != 0:
                raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
            raw_words = getattr(raw, "words")
            if not isinstance(raw_words, list):
                raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
            words: list[dict[str, object]] = []
            prior_word_start = start
            for word_index, raw_word in enumerate(raw_words):
                total_words += 1
                if total_words > MAX_WORDS:
                    raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
                word_start = _milliseconds(getattr(raw_word, "start"), duration_ms)
                word_end = _milliseconds(getattr(raw_word, "end"), duration_ms)
                if word_start < start or word_end > end or word_start < prior_word_start or word_end < word_start:
                    raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
                prior_word_start = word_start
                word_text = _bounded_text(getattr(raw_word, "word"), MAX_WORD_TEXT_CHARS)
                probability = _finite_number(getattr(raw_word, "probability"))
                if not 0 <= probability <= 1:
                    raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
                words.append({
                    "id": f"asrword_{index:04d}_{word_index:04d}",
                    "start_ms": word_start, "end_ms": word_end,
                    "text": word_text, "probability": probability,
                })
            result.append({
                "id": f"asrseg_{index:04d}", "start_ms": start, "end_ms": end,
                "text": text, "avg_logprob": avg_logprob,
                "no_speech_prob": no_speech_prob, "temperature": temperature,
                "words": words,
            })
    except WorkerError:
        raise
    except (AttributeError, TypeError) as exc:
        raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID") from exc
    return result


def _transcribe(request: dict[str, object]) -> dict[str, object]:
    versions = _versions(request["dependencies"])
    try:
        import numpy as np
        from faster_whisper import WhisperModel

        pcm = request["pcm"]
        waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if waveform.ndim != 1 or waveform.size != request["audio"]["frames"] or not bool(np.isfinite(waveform).all()):
            raise WorkerError("LOCAL_ASR_AUDIO_INVALID")
        model = WhisperModel(
            str(request["model_dir"]), device="cpu", compute_type=COMPUTE_TYPE,
            cpu_threads=CPU_THREADS, num_workers=NUM_WORKERS, local_files_only=True,
        )
        runtime_model = model.model
        if getattr(runtime_model, "device", None) != "cpu" or getattr(runtime_model, "compute_type", None) != COMPUTE_TYPE:
            raise WorkerError("LOCAL_ASR_CPU_REQUIRED")
        raw_segments, info = model.transcribe(
            waveform, language=LANGUAGE, task=TASK, beam_size=1, best_of=1,
            temperature=0.0, condition_on_previous_text=False,
            word_timestamps=True, vad_filter=False, log_progress=False,
        )
        if getattr(info, "language", None) != LANGUAGE:
            raise WorkerError("LOCAL_ASR_PROPOSAL_INVALID")
        segments = _build_segments(raw_segments, request["audio"]["duration_ms"])
    except WorkerError:
        raise
    except Exception as exc:
        raise WorkerError("LOCAL_ASR_TRANSCRIPTION_FAILED") from exc
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "ok",
        "request_id": request["request_id"],
        "binding_sha256": request["binding_sha256"],
        "source": request["source"],
        "audio": request["audio"],
        "model": {"id": MODEL_ID, "revision": MODEL_REVISION},
        "task": {
            "language": LANGUAGE, "task": TASK, "beam_size": 1, "best_of": 1,
            "temperature": 0.0, "condition_on_previous_text": False,
            "word_timestamps": True, "vad_filter": False,
        },
        "segments": segments,
        "runtime": {
            "device": "cpu", "compute_type": COMPUTE_TYPE,
            "cpu_threads": CPU_THREADS, "num_workers": NUM_WORKERS,
            "dependencies": versions,
        },
        "limitations": {
            "machine_proposal_only": True, "human_review_required": True,
            "speaker_identity_inferred": False,
        },
    }


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    encoded = json.dumps(receipt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        encoded = json.dumps({
            "schema": RECEIPT_SCHEMA, "status": "error",
            "error": {"code": "LOCAL_ASR_PROPOSAL_INVALID"},
        }, separators=(",", ":")).encode("utf-8")
    try:
        with path.open("xb") as output:
            output.write(encoded)
    except OSError:
        pass


def _safe_cli_paths(request_value: str, receipt_value: str) -> tuple[Path, Path]:
    request_path = _absolute_without_links(request_value)
    receipt_path = Path(receipt_value)
    if not receipt_path.is_absolute() or receipt_path.exists() or receipt_path.is_symlink():
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    receipt_absolute = Path(os.path.abspath(receipt_path))
    if receipt_absolute.parent != request_path.parent:
        raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
    return request_path, receipt_absolute


def main() -> int:
    _scrub_environment()
    if len(sys.argv) != 3:
        return 2
    try:
        request_path, receipt_path = _safe_cli_paths(sys.argv[1], sys.argv[2])
        if request_path.stat().st_size > MAX_REQUEST_BYTES:
            raise WorkerError("LOCAL_ASR_REQUEST_INVALID")
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        request = _validate_request(payload, request_path, receipt_path)
        receipt = _transcribe(request)
    except (OSError, UnicodeError, json.JSONDecodeError, WorkerError) as exc:
        code = exc.code if isinstance(exc, WorkerError) else "LOCAL_ASR_REQUEST_INVALID"
        if "receipt_path" in locals():
            _write_receipt(receipt_path, {
                "schema": RECEIPT_SCHEMA, "status": "error", "error": {"code": code},
            })
        return 2
    except Exception:
        if "receipt_path" in locals():
            _write_receipt(receipt_path, {
                "schema": RECEIPT_SCHEMA, "status": "error",
                "error": {"code": "LOCAL_ASR_TRANSCRIPTION_FAILED"},
            })
        return 2
    _write_receipt(receipt_path, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
