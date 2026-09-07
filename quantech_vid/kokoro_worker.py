from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import ast
import json
import math
import os
import re
import struct
import sys
import wave
import zipfile
from pathlib import Path


REQUEST_SCHEMA = "quantech.local-voice.request.v1"
RECEIPT_SCHEMA = "quantech.local-voice.receipt.v1"
PILOT_VOICE = "af_heart"
PILOT_LANGUAGE = "en-us"
PILOT_SPEED = 1.0
SAMPLE_RATE = 24_000
MAX_TEXT_CHARS = 1_000
MAX_AUDIO_SECONDS = 60
MAX_REQUEST_BYTES = 32 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_RESOURCE_TREE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 128
MAX_VOICE_ELEMENTS = 1_000_000
MAX_ARCHIVE_ELEMENTS = 50_000_000
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
    os.environ.clear()
    os.environ.update(retained)
    sys.dont_write_bytecode = True
    _OUTPUT_SINK = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _OUTPUT_SINK
    sys.stderr = _OUTPUT_SINK


def _exact_keys(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    return value


def _exact_int(value: object, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    if maximum is not None and value > maximum:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    return value


def _absolute_without_links(path_value: object, *, directory: bool = False) -> Path:
    if not isinstance(path_value, str) or not path_value or "\x00" in path_value:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    path = Path(path_value)
    if not path.is_absolute():
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkerError("LOCAL_VOICE_RESOURCE_MISSING") from exc
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise WorkerError("LOCAL_VOICE_RESOURCE_LINK_REJECTED")
    if directory != path.is_dir() or (not directory and not path.is_file()):
        raise WorkerError("LOCAL_VOICE_RESOURCE_INVALID")
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
                    raise WorkerError("LOCAL_VOICE_RESOURCE_INVALID")
                digest.update(chunk)
    except WorkerError:
        raise
    except OSError as exc:
        raise WorkerError("LOCAL_VOICE_RESOURCE_INVALID") from exc
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
                    raise WorkerError("LOCAL_VOICE_RESOURCE_INVALID")
                resolved = _absolute_without_links(str(current_path / name), directory=True)
                if not _contained(resolved, root):
                    raise WorkerError("LOCAL_VOICE_RESOURCE_LINK_REJECTED")
            for name in filenames:
                entries_seen += 1
                if entries_seen > MAX_RESOURCE_TREE_ENTRIES:
                    raise WorkerError("LOCAL_VOICE_RESOURCE_INVALID")
                entry = current_path / name
                resolved = _absolute_without_links(str(entry))
                if not _contained(resolved, root):
                    raise WorkerError("LOCAL_VOICE_RESOURCE_LINK_REJECTED")
                size, file_hash = _hash_file(entry, MAX_RESOURCE_TREE_BYTES - total)
                total += size
                count += 1
                relative = entry.relative_to(root).as_posix().encode("utf-8")
                digest.update(len(relative).to_bytes(4, "big"))
                digest.update(relative)
                digest.update(size.to_bytes(8, "big"))
                digest.update(bytes.fromhex(file_hash))
    except WorkerError:
        raise
    except OSError as exc:
        raise WorkerError("LOCAL_VOICE_RESOURCE_INVALID") from exc
    return count, total, digest.hexdigest()


def _validate_file(value: object, root: Path) -> Path:
    entry = _exact_keys(value, {"path", "size", "sha256"})
    path = _absolute_without_links(entry["path"])
    if not _contained(path, root):
        raise WorkerError("LOCAL_VOICE_RESOURCE_OUTSIDE_ROOT")
    expected_size = _exact_int(entry["size"], 1, MAX_RESOURCE_TREE_BYTES)
    expected_hash = entry["sha256"]
    if not isinstance(expected_hash, str) or not _HEX_64.fullmatch(expected_hash):
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    if path.stat().st_size != expected_size:
        raise WorkerError("LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED")
    size, digest = _hash_file(path, expected_size)
    if size != expected_size or not hmac.compare_digest(digest, expected_hash):
        raise WorkerError("LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED")
    return path


def _validate_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_CHARS:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    return value


def _validate_request(payload: object, request_path: Path, receipt_path: Path) -> dict[str, object]:
    request = _exact_keys(
        payload,
        {"schema", "request_id", "binding_sha256", "text", "voice", "language", "speed",
         "job_dir", "output_wav", "resource_root", "model", "voices", "site_packages",
         "dependencies", "espeak", "max_audio_seconds"},
    )
    if request["schema"] != REQUEST_SCHEMA:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    if not isinstance(request["request_id"], str) or not _OPAQUE_ID.fullmatch(request["request_id"]):
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    if not isinstance(request["binding_sha256"], str) or not _HEX_64.fullmatch(request["binding_sha256"]):
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    request["text"] = _validate_text(request["text"])
    if (
        request["voice"] != PILOT_VOICE
        or request["language"] != PILOT_LANGUAGE
        or isinstance(request["speed"], bool)
        or request["speed"] != PILOT_SPEED
        or request["max_audio_seconds"] != MAX_AUDIO_SECONDS
    ):
        raise WorkerError("LOCAL_VOICE_VOICE_NOT_ALLOWED")

    job_dir = _absolute_without_links(request["job_dir"], directory=True)
    request_file = _absolute_without_links(str(request_path))
    if request_file.parent != job_dir:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    receipt_absolute = Path(os.path.abspath(receipt_path))
    if not isinstance(request["output_wav"], str) or not Path(request["output_wav"]).is_absolute():
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    output_absolute = Path(os.path.abspath(request["output_wav"]))
    if receipt_path.exists() or receipt_path.is_symlink() or receipt_absolute.parent != job_dir:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    if Path(request["output_wav"]).exists() or Path(request["output_wav"]).is_symlink():
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    if output_absolute.parent != job_dir:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    request["job_dir"] = job_dir
    request["output_wav"] = output_absolute

    root = _absolute_without_links(request["resource_root"], directory=True)
    request["resource_root"] = root
    request["model"] = _validate_file(request["model"], root)
    request["voices"] = _validate_file(request["voices"], root)

    packages = _exact_keys(request["site_packages"], {"path", "files", "size", "tree_sha256"})
    site_packages = _absolute_without_links(packages["path"], directory=True)
    file_count = _exact_int(packages["files"], 1)
    tree_size = _exact_int(packages["size"], 1, MAX_RESOURCE_TREE_BYTES)
    tree_hash = packages["tree_sha256"]
    if not isinstance(tree_hash, str) or not _HEX_64.fullmatch(tree_hash):
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    actual_count, actual_size, actual_hash = _tree_digest(site_packages)
    if (
        actual_count != file_count
        or actual_size != tree_size
        or not hmac.compare_digest(actual_hash, tree_hash)
    ):
        raise WorkerError("LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED")
    request["site_packages"] = site_packages

    dependencies = request["dependencies"]
    if not isinstance(dependencies, dict) or not dependencies or len(dependencies) > 128:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    for name, version in dependencies.items():
        if (
            not isinstance(name, str)
            or not _PACKAGE_NAME.fullmatch(name)
            or not isinstance(version, str)
            or not 0 < len(version) <= 120
        ):
            raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    request["dependencies"] = dict(sorted(dependencies.items()))

    espeak = _exact_keys(
        request["espeak"],
        {"library_path", "library_size", "library_sha256", "data_path", "data_files",
         "data_size", "data_tree_sha256"},
    )
    library = _absolute_without_links(espeak["library_path"])
    library_size = _exact_int(espeak["library_size"], 1, MAX_RESOURCE_TREE_BYTES)
    library_hash = espeak["library_sha256"]
    if not isinstance(library_hash, str) or not _HEX_64.fullmatch(library_hash):
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    actual_size, actual_hash = _hash_file(library, library_size)
    if actual_size != library_size or not hmac.compare_digest(actual_hash, library_hash):
        raise WorkerError("LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED")
    data = _absolute_without_links(espeak["data_path"], directory=True)
    data_files = _exact_int(espeak["data_files"], 1)
    data_size = _exact_int(espeak["data_size"], 1, MAX_RESOURCE_TREE_BYTES)
    data_hash = espeak["data_tree_sha256"]
    if not isinstance(data_hash, str) or not _HEX_64.fullmatch(data_hash):
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    actual_files, actual_data_size, actual_data_hash = _tree_digest(data)
    if (
        actual_files != data_files
        or actual_data_size != data_size
        or not hmac.compare_digest(actual_data_hash, data_hash)
    ):
        raise WorkerError("LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED")
    request["espeak"] = {"library": library, "data": data}
    return request


def _npy_header(stream: object, uncompressed_size: int) -> tuple[str, int]:
    if stream.read(6) != b"\x93NUMPY":
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    version = stream.read(2)
    if version == b"\x01\x00":
        length_bytes = stream.read(2)
        if len(length_bytes) != 2:
            raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
        header_length = struct.unpack("<H", length_bytes)[0]
        prefix_size = 10
        encoding = "latin1"
    elif version in {b"\x02\x00", b"\x03\x00"}:
        length_bytes = stream.read(4)
        if len(length_bytes) != 4:
            raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
        header_length = struct.unpack("<I", length_bytes)[0]
        prefix_size = 12
        encoding = "utf-8" if version == b"\x03\x00" else "latin1"
    else:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    if not 1 <= header_length <= 10_000 or prefix_size + header_length > uncompressed_size:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    raw_header = stream.read(header_length)
    if len(raw_header) != header_length:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    try:
        header = ast.literal_eval(raw_header.decode(encoding).strip())
    except (UnicodeError, SyntaxError, ValueError) as exc:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID") from exc
    if not isinstance(header, dict) or set(header) != {"descr", "fortran_order", "shape"}:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    descriptor = header["descr"]
    shape = header["shape"]
    if (
        not isinstance(descriptor, str)
        or re.fullmatch(r"[<>=|]f(2|4|8)", descriptor) is None
        or not isinstance(header["fortran_order"], bool)
        or not isinstance(shape, tuple)
        or not 2 <= len(shape) <= 3
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in shape)
    ):
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    elements = math.prod(shape)
    if elements > MAX_VOICE_ELEMENTS:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    item_size = int(descriptor[-1])
    if prefix_size + header_length + elements * item_size != uncompressed_size:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    return descriptor, elements


def _preflight_npz_structure(voices_path: Path) -> set[str]:
    if voices_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    try:
        with zipfile.ZipFile(voices_path, "r") as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_ARCHIVE_ENTRIES:
                raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
            filenames = [entry.filename for entry in entries]
            if len(set(filenames)) != len(filenames):
                raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
            total_bytes = total_elements = 0
            names: set[str] = set()
            for entry in entries:
                if (
                    entry.is_dir()
                    or entry.flag_bits & 0x1
                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or not entry.filename.endswith(".npy")
                    or "/" in entry.filename
                    or "\\" in entry.filename
                ):
                    raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
                name = entry.filename[:-4]
                if not _PACKAGE_NAME.fullmatch(name) or name in names:
                    raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
                total_bytes += entry.file_size
                if total_bytes > MAX_ARCHIVE_BYTES:
                    raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
                with archive.open(entry, "r") as stream:
                    _, elements = _npy_header(stream, entry.file_size)
                total_elements += elements
                if total_elements > MAX_ARCHIVE_ELEMENTS:
                    raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
                names.add(name)
    except WorkerError:
        raise
    except (OSError, EOFError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID") from exc
    if PILOT_VOICE not in names:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    return names


def _preflight_voices(voices_path: Path) -> None:
    structural_names = _preflight_npz_structure(voices_path)
    import numpy as np

    try:
        archive = np.load(str(voices_path), allow_pickle=False, max_header_size=10_000)
    except (OSError, ValueError) as exc:
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID") from exc
    if not isinstance(archive, np.lib.npyio.NpzFile):
        try:
            archive.close()
        except AttributeError:
            pass
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    try:
        names = archive.files
        if set(names) != structural_names:
            raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
        total_elements = 0
        for name in names:
            if not isinstance(name, str) or not _PACKAGE_NAME.fullmatch(name):
                raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
            array = archive[name]
            if (
                not isinstance(array, np.ndarray)
                or array.dtype.kind not in "f"
                or not 2 <= array.ndim <= 3
                or any(dimension < 1 for dimension in array.shape)
                or array.size > MAX_VOICE_ELEMENTS
            ):
                raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
            total_elements += int(array.size)
            if total_elements > MAX_ARCHIVE_ELEMENTS or not bool(np.isfinite(array).all()):
                raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID")
    except (KeyError, OSError, ValueError, TypeError) as exc:
        if isinstance(exc, WorkerError):
            raise
        raise WorkerError("LOCAL_VOICE_ARCHIVE_INVALID") from exc
    finally:
        archive.close()


def _versions(expected: dict[str, str]) -> dict[str, str]:
    actual = {}
    try:
        for name, expected_version in expected.items():
            version = importlib.metadata.version(name)
            if version != expected_version:
                raise WorkerError("LOCAL_VOICE_RUNTIME_INVALID")
            actual[name] = version
    except importlib.metadata.PackageNotFoundError as exc:
        raise WorkerError("LOCAL_VOICE_RUNTIME_INVALID") from exc
    return actual


def _write_wav(path: Path, audio: object, sample_rate: object) -> dict[str, object]:
    import numpy as np

    if isinstance(sample_rate, bool) or sample_rate != SAMPLE_RATE:
        raise WorkerError("LOCAL_VOICE_AUDIO_INVALID")
    samples = np.asarray(audio)
    if samples.ndim != 1 or samples.dtype.kind not in "f" or not 1 <= samples.size <= SAMPLE_RATE * MAX_AUDIO_SECONDS:
        raise WorkerError("LOCAL_VOICE_AUDIO_INVALID")
    if not bool(np.isfinite(samples).all()):
        raise WorkerError("LOCAL_VOICE_AUDIO_INVALID")
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2", copy=False).tobytes()
    try:
        with path.open("xb") as output:
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
    except (OSError, wave.Error) as exc:
        raise WorkerError("LOCAL_VOICE_AUDIO_INVALID") from exc
    size, digest = _hash_file(path, 44 + SAMPLE_RATE * MAX_AUDIO_SECONDS * 2)
    frames = len(pcm) // 2
    if size != 44 + len(pcm):
        raise WorkerError("LOCAL_VOICE_AUDIO_INVALID")
    return {
        "sha256": digest,
        "size": size,
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "sample_width": 2,
        "frames": frames,
        "duration_ms": frames * 1000 // SAMPLE_RATE,
    }


def _synthesize(request: dict[str, object]) -> dict[str, object]:
    _preflight_voices(request["voices"])
    versions = _versions(request["dependencies"])
    try:
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        from kokoro_onnx.config import EspeakConfig

        session = ort.InferenceSession(
            str(request["model"]), providers=["CPUExecutionProvider"]
        )
        providers = session.get_providers()
        if providers != ["CPUExecutionProvider"]:
            raise WorkerError("LOCAL_VOICE_CPU_REQUIRED")
        espeak = request["espeak"]
        engine = Kokoro.from_session(
            session,
            str(request["voices"]),
            espeak_config=EspeakConfig(
                lib_path=str(espeak["library"]), data_path=str(espeak["data"])
            ),
        )
        audio, sample_rate = engine.create(
            request["text"],
            voice=PILOT_VOICE,
            speed=PILOT_SPEED,
            lang=PILOT_LANGUAGE,
            is_phonemes=False,
            continuous=False,
        )
    except WorkerError:
        raise
    except Exception as exc:
        raise WorkerError("LOCAL_VOICE_SYNTHESIS_FAILED") from exc
    audio_receipt = _write_wav(request["output_wav"], audio, sample_rate)
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "ok",
        "request_id": request["request_id"],
        "binding_sha256": request["binding_sha256"],
        "voice": PILOT_VOICE,
        "language": PILOT_LANGUAGE,
        "speed": PILOT_SPEED,
        "audio": audio_receipt,
        "runtime": {"providers": ["CPUExecutionProvider"], "dependencies": versions},
    }


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    encoded = json.dumps(receipt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "error",
            "error": {"code": "LOCAL_VOICE_SYNTHESIS_FAILED"},
        }
        encoded = json.dumps(receipt, separators=(",", ":")).encode("utf-8")
    try:
        with path.open("xb") as output:
            output.write(encoded)
    except OSError:
        pass


def _safe_cli_paths(request_value: str, receipt_value: str) -> tuple[Path, Path]:
    request_path = _absolute_without_links(request_value)
    receipt_path = Path(receipt_value)
    if not receipt_path.is_absolute() or receipt_path.exists() or receipt_path.is_symlink():
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    receipt_absolute = Path(os.path.abspath(receipt_path))
    if receipt_absolute.parent != request_path.parent:
        raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
    return request_path, receipt_absolute


def main() -> int:
    _scrub_environment()
    if len(sys.argv) != 3:
        return 2
    try:
        request_path, receipt_path = _safe_cli_paths(sys.argv[1], sys.argv[2])
        if request_path.stat().st_size > MAX_REQUEST_BYTES:
            raise WorkerError("LOCAL_VOICE_REQUEST_INVALID")
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        request = _validate_request(payload, request_path, receipt_path)
        receipt = _synthesize(request)
    except (OSError, UnicodeError, json.JSONDecodeError, WorkerError) as exc:
        code = exc.code if isinstance(exc, WorkerError) else "LOCAL_VOICE_REQUEST_INVALID"
        if "receipt_path" in locals():
            _write_receipt(
                receipt_path,
                {"schema": RECEIPT_SCHEMA, "status": "error", "error": {"code": code}},
            )
        return 2
    except Exception:
        _write_receipt(
            receipt_path,
            {"schema": RECEIPT_SCHEMA, "status": "error",
             "error": {"code": "LOCAL_VOICE_SYNTHESIS_FAILED"}},
        )
        return 2
    _write_receipt(receipt_path, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
