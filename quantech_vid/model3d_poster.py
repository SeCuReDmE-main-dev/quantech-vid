from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, UnidentifiedImageError

from .model3d import inspect_model3d
from .process import run_command


POSTER_SIZE = 1_024
MAX_POSTER_BYTES = 10_000_000
MAX_BUNDLE_BYTES = 2_000_000
DEFAULT_DEADLINE_SECONDS = 30.0
MAX_DEADLINE_SECONDS = 60.0
CAMERA_ANGLES = ("front", "right", "back", "left")
_HEX_64 = re.compile(r"^[a-f0-9]{64}$")
REQUEST_SCHEMA = "quantech.model3d-poster.request.v1"
RECEIPT_SCHEMA = "quantech.model3d-poster.receipt.v1"
MAX_REQUEST_BYTES = 4_096
MAX_RECEIPT_BYTES = 8_192


class Model3DPosterError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Model3DPosterUnavailable(Model3DPosterError):
    pass


@dataclass(frozen=True)
class Model3DPosterProof:
    path: Path
    source_sha256: str
    poster_sha256: str
    poster_size: int
    width: int
    height: int
    camera_angles: tuple[str, ...]
    renderer_binding: str
    object_count: int
    geometry_count: int
    material_count: int
    vertex_count: int
    triangle_count: int
    network_attempts: tuple[str, ...]


@dataclass(frozen=True)
class _Bundle:
    payload: bytes
    sha256: str
    size: int
    binding: str


def _fail(code: str) -> None:
    raise Model3DPosterError(code)


def _bundle_identity() -> _Bundle:
    root = Path(__file__).resolve().parent.parent / "packages" / "scene3d"
    path = root / "dist" / "model-poster.js"
    package_path = root / "package.json"
    try:
        if path.is_symlink() or not path.is_file() or not package_path.is_file():
            raise OSError
        payload = path.read_bytes()
        if not 1 <= len(payload) <= MAX_BUNDLE_BYTES:
            raise ValueError
        package = json.loads(package_path.read_text(encoding="utf-8"))
        version = package["dependencies"]["three"]
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError
    except (OSError, KeyError, RecursionError, TypeError, UnicodeError, ValueError):
        raise Model3DPosterUnavailable("MODEL3D_POSTER_RENDERER_UNAVAILABLE") from None
    digest = hashlib.sha256(payload).hexdigest()
    return _Bundle(payload, digest, len(payload), f"three@{version}:model-poster:sha256:{digest}")


def _output_target(output_dir: Path) -> tuple[Path, Path]:
    if not isinstance(output_dir, Path) or not output_dir.is_absolute():
        _fail("MODEL3D_POSTER_OUTPUT_INVALID")
    try:
        lexical = Path(os.path.abspath(output_dir))
        root = output_dir.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("MODEL3D_POSTER_OUTPUT_INVALID")
    if os.path.normcase(str(lexical)) != os.path.normcase(str(root)) or not root.is_dir():
        _fail("MODEL3D_POSTER_OUTPUT_INVALID")
    try:
        home = Path.home().resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("MODEL3D_POSTER_OUTPUT_INVALID")
    if root == Path(root.anchor) or root == home:
        _fail("MODEL3D_POSTER_OUTPUT_INVALID")
    target = root / "model3d-poster.png"
    if target.exists() or target.is_symlink() or target.parent != root:
        _fail("MODEL3D_POSTER_OUTPUT_INVALID")
    return root, target


def _worker_script() -> Path:
    path = Path(__file__).with_name("model3d_poster_worker.py")
    if path.is_symlink() or not path.is_file():
        raise Model3DPosterUnavailable("MODEL3D_POSTER_RENDERER_UNAVAILABLE")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise Model3DPosterUnavailable("MODEL3D_POSTER_RENDERER_UNAVAILABLE") from None


def _python_executable() -> Path:
    try:
        # Preserve the venv entry path: resolving its symlink before launching
        # would silently select the base interpreter and lose installed packages.
        path = Path(os.path.abspath(sys.executable))
        if not path.resolve(strict=True).is_file():
            raise OSError
        return path
    except (OSError, RuntimeError, ValueError):
        raise Model3DPosterUnavailable("MODEL3D_POSTER_RENDERER_UNAVAILABLE") from None


def _check_cancelled(callback: Callable[[], bool]) -> bool:
    try:
        value = callback()
    except Exception:
        raise Model3DPosterError("MODEL3D_POSTER_CANCEL_INVALID") from None
    if not isinstance(value, bool):
        _fail("MODEL3D_POSTER_CANCEL_INVALID")
    return value


def _strict_int(value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail("MODEL3D_POSTER_RUNTIME_INVALID")
    return value


def _validate_stats(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {
        "width", "height", "angles", "objectCount", "geometryCount", "materialCount",
        "vertexCount", "triangleCount",
    }:
        _fail("MODEL3D_POSTER_RUNTIME_INVALID")
    angles = value["angles"]
    if not isinstance(angles, list) or tuple(angles) != CAMERA_ANGLES:
        _fail("MODEL3D_POSTER_RUNTIME_INVALID")
    if _strict_int(value["width"], POSTER_SIZE, POSTER_SIZE) != POSTER_SIZE:
        _fail("MODEL3D_POSTER_RUNTIME_INVALID")
    if _strict_int(value["height"], POSTER_SIZE, POSTER_SIZE) != POSTER_SIZE:
        _fail("MODEL3D_POSTER_RUNTIME_INVALID")
    return {
        "object_count": _strict_int(value["objectCount"], 2, 512),
        "geometry_count": _strict_int(value["geometryCount"], 1, 128),
        "material_count": _strict_int(value["materialCount"], 1, 64),
        "vertex_count": _strict_int(value["vertexCount"], 3, 200_000),
        "triangle_count": _strict_int(value["triangleCount"], 1, 400_000),
    }


def _validate_png(payload: bytes) -> tuple[str, int]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_POSTER_BYTES:
        _fail("MODEL3D_POSTER_PNG_INVALID")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG" or image.size != (POSTER_SIZE, POSTER_SIZE):
                _fail("MODEL3D_POSTER_PNG_INVALID")
            image.load()
    except Model3DPosterError:
        raise
    except (OSError, ValueError, UnidentifiedImageError):
        raise Model3DPosterError("MODEL3D_POSTER_PNG_INVALID") from None
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _write_private(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
        os.chmod(path, 0o600)
    except OSError:
        raise Model3DPosterError("MODEL3D_POSTER_OUTPUT_INVALID") from None


def _read_receipt(path: Path) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECEIPT_BYTES:
            _fail("MODEL3D_POSTER_RECEIPT_INVALID")
        value = json.loads(path.read_text(encoding="utf-8"))
    except Model3DPosterError:
        raise
    except (OSError, RecursionError, TypeError, UnicodeError, ValueError):
        raise Model3DPosterError("MODEL3D_POSTER_RECEIPT_INVALID") from None
    if not isinstance(value, dict):
        _fail("MODEL3D_POSTER_RECEIPT_INVALID")
    return value


def _validate_receipt(
    receipt: dict[str, object], *, source_sha256: str, bundle: _Bundle,
) -> tuple[dict[str, int], str, int]:
    if set(receipt) != {
        "schema", "status", "source_sha256", "bundle", "poster", "stats", "network_attempts",
    } or receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("status") != "ok":
        _fail("MODEL3D_POSTER_RECEIPT_INVALID")
    if receipt.get("source_sha256") != source_sha256:
        _fail("MODEL3D_POSTER_RECEIPT_INVALID")
    binding = receipt.get("bundle")
    if not isinstance(binding, dict) or binding != {
        "sha256": bundle.sha256, "size": bundle.size, "binding": bundle.binding,
    }:
        _fail("MODEL3D_POSTER_RECEIPT_INVALID")
    poster = receipt.get("poster")
    if not isinstance(poster, dict) or set(poster) != {"sha256", "size", "width", "height"}:
        _fail("MODEL3D_POSTER_RECEIPT_INVALID")
    poster_hash = poster.get("sha256")
    if not isinstance(poster_hash, str) or not _HEX_64.fullmatch(poster_hash):
        _fail("MODEL3D_POSTER_RECEIPT_INVALID")
    poster_size = _strict_int(poster.get("size"), 1, MAX_POSTER_BYTES)
    if (_strict_int(poster.get("width"), POSTER_SIZE, POSTER_SIZE) != POSTER_SIZE
            or _strict_int(poster.get("height"), POSTER_SIZE, POSTER_SIZE) != POSTER_SIZE):
        _fail("MODEL3D_POSTER_RECEIPT_INVALID")
    attempts = receipt.get("network_attempts")
    if not isinstance(attempts, list) or attempts:
        _fail("MODEL3D_POSTER_NETWORK_ATTEMPT")
    return _validate_stats(receipt.get("stats")), poster_hash, poster_size


def render_model3d_poster(
    payload: bytes,
    expected_sha256: str,
    output_dir: Path,
    cancelled: Callable[[], bool],
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> Model3DPosterProof:
    """Render four fixed views of a verified plain GLB into one bounded PNG."""
    metadata = inspect_model3d(payload)
    if not isinstance(expected_sha256, str) or not _HEX_64.fullmatch(expected_sha256):
        _fail("MODEL3D_POSTER_HASH_INVALID")
    if not hmac.compare_digest(metadata.sha256, expected_sha256):
        _fail("MODEL3D_POSTER_HASH_MISMATCH")
    if metadata.image_count or metadata.texture_count:
        _fail("MODEL3D_POSTER_PROFILE_UNSUPPORTED")
    if not callable(cancelled):
        _fail("MODEL3D_POSTER_CANCEL_INVALID")
    if _check_cancelled(cancelled):
        raise InterruptedError("Render cancelled")
    if (isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or not 1 <= deadline_seconds <= MAX_DEADLINE_SECONDS):
        _fail("MODEL3D_POSTER_DEADLINE_INVALID")
    root, target = _output_target(output_dir)
    bundle = _bundle_identity()
    worker = _worker_script()
    python = _python_executable()
    job_path: Path | None = None
    created = False
    succeeded = False
    try:
        job_path = Path(tempfile.mkdtemp(prefix=".model3d-poster-job-", dir=root))
        os.chmod(job_path, 0o700)
        source_path = job_path / "model.glb"
        bundle_path = job_path / "model-poster.js"
        request_path = job_path / "request.json"
        receipt_path = job_path / "receipt.json"
        poster_path = job_path / "poster.png"
        _write_private(source_path, payload)
        _write_private(bundle_path, bundle.payload)
        request = {
            "schema": REQUEST_SCHEMA,
            "source": {"name": source_path.name, "sha256": metadata.sha256, "size": len(payload)},
            "bundle": {
                "name": bundle_path.name, "sha256": bundle.sha256,
                "size": bundle.size, "binding": bundle.binding,
            },
            "output": {"name": poster_path.name},
        }
        encoded_request = json.dumps(request, separators=(",", ":")).encode("utf-8")
        if len(encoded_request) > MAX_REQUEST_BYTES:
            _fail("MODEL3D_POSTER_REQUEST_INVALID")
        _write_private(request_path, encoded_request)
        try:
            completed = run_command(
                [python, "-I", worker, request_path, receipt_path],
                timeout=float(deadline_seconds), cancelled=lambda: _check_cancelled(cancelled), check=False,
            )
        except InterruptedError:
            raise
        except TimeoutError:
            raise Model3DPosterError("MODEL3D_POSTER_DEADLINE_EXCEEDED") from None
        except (OSError, TypeError, ValueError):
            raise Model3DPosterUnavailable("MODEL3D_POSTER_RENDERER_UNAVAILABLE") from None
        receipt = _read_receipt(receipt_path)
        if completed.returncode:
            error = receipt.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            allowed = {
                "MODEL3D_POSTER_NETWORK_ATTEMPT", "MODEL3D_POSTER_RUNTIME_INVALID",
                "MODEL3D_POSTER_PNG_INVALID", "MODEL3D_POSTER_RENDERER_UNAVAILABLE",
                "MODEL3D_POSTER_REQUEST_INVALID", "MODEL3D_POSTER_RESOURCE_INVALID",
            }
            selected = (
                code if isinstance(code, str) and code in allowed
                else "MODEL3D_POSTER_RENDERER_UNAVAILABLE"
            )
            raise Model3DPosterUnavailable(selected)
        stats, receipt_hash, receipt_size = _validate_receipt(
            receipt, source_sha256=metadata.sha256, bundle=bundle,
        )
        if poster_path.is_symlink() or not poster_path.is_file() or poster_path.stat().st_size > MAX_POSTER_BYTES:
            _fail("MODEL3D_POSTER_PNG_INVALID")
        png = poster_path.read_bytes()
        poster_sha256, poster_size = _validate_png(png)
        if (poster_size != receipt_size or not hmac.compare_digest(poster_sha256, receipt_hash)):
            _fail("MODEL3D_POSTER_RECEIPT_INVALID")
        if _check_cancelled(cancelled):
            raise InterruptedError("Render cancelled")
        with target.open("xb") as output:
            created = True
            output.write(png)
        succeeded = True
        return Model3DPosterProof(
            path=target, source_sha256=metadata.sha256,
            poster_sha256=poster_sha256, poster_size=poster_size,
            width=POSTER_SIZE, height=POSTER_SIZE, camera_angles=CAMERA_ANGLES,
            renderer_binding=bundle.binding, network_attempts=(),
            **stats,
        )
    except (InterruptedError, Model3DPosterError):
        raise
    except (OSError, RecursionError, RuntimeError, TypeError, UnicodeError, ValueError):
        raise Model3DPosterError("MODEL3D_POSTER_OUTPUT_INVALID") from None
    finally:
        if job_path is not None:
            try:
                shutil.rmtree(job_path)
            except OSError:
                pass
        if created and not succeeded:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
