from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


_OUTPUT_SINK = None


def _scrub_environment() -> None:
    global _OUTPUT_SINK
    retained: dict[str, str] = {}
    for name in (
        "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR",
        "HOME", "USERPROFILE", "LOCALAPPDATA", "LANG", "LC_ALL",
        "QUANTECH_SCENE3D_CHROMIUM",
    ):
        value = os.environ.get(name)
        if value:
            retained[name] = value
    retained["DO_NOT_TRACK"] = "1"
    os.environ.clear()
    os.environ.update(retained)
    sys.dont_write_bytecode = True
    _OUTPUT_SINK = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _OUTPUT_SINK
    sys.stderr = _OUTPUT_SINK


_scrub_environment()

from PIL import Image, UnidentifiedImageError


REQUEST_SCHEMA = "quantech.model3d-poster.request.v1"
RECEIPT_SCHEMA = "quantech.model3d-poster.receipt.v1"
MAX_REQUEST_BYTES = 4_096
MAX_RECEIPT_BYTES = 8_192
MAX_SOURCE_BYTES = 20_000_000
MAX_BUNDLE_BYTES = 2_000_000
MAX_POSTER_BYTES = 10_000_000
POSTER_SIZE = 1_024
ANGLES = ("front", "right", "back", "left")
_HEX_64 = re.compile(r"^[a-f0-9]{64}$")


class WorkerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise WorkerError(code)


def _exact(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail("MODEL3D_POSTER_REQUEST_INVALID")
    return value


def _strict_int(value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail("MODEL3D_POSTER_REQUEST_INVALID")
    return value


def _job_paths() -> tuple[Path, Path, Path]:
    if len(sys.argv) != 3:
        _fail("MODEL3D_POSTER_REQUEST_INVALID")
    request_lexical = Path(sys.argv[1])
    receipt_lexical = Path(sys.argv[2])
    if (not request_lexical.is_absolute() or not receipt_lexical.is_absolute()
            or request_lexical.is_symlink() or receipt_lexical.is_symlink()):
        _fail("MODEL3D_POSTER_REQUEST_INVALID")
    try:
        request = request_lexical.resolve(strict=True)
        root = request.parent.resolve(strict=True)
    except OSError:
        _fail("MODEL3D_POSTER_REQUEST_INVALID")
    receipt = root / "receipt.json"
    lexical_root = Path(os.path.abspath(request_lexical.parent))
    if (request.name != "request.json" or receipt_lexical != receipt
            or os.path.normcase(str(lexical_root)) != os.path.normcase(str(root))
            or request.parent != root or root.is_symlink()
            or receipt.exists() or receipt.is_symlink()):
        _fail("MODEL3D_POSTER_REQUEST_INVALID")
    return root, request, receipt


def _read_request(path: Path) -> dict[str, object]:
    try:
        if path.is_symlink() or not 1 <= path.stat().st_size <= MAX_REQUEST_BYTES:
            _fail("MODEL3D_POSTER_REQUEST_INVALID")
        request = json.loads(path.read_text(encoding="utf-8"))
    except WorkerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise WorkerError("MODEL3D_POSTER_REQUEST_INVALID") from None
    return _exact(request, {"schema", "source", "bundle", "output"})


def _read_bound_file(
    root: Path, descriptor: object, *, expected_name: str, maximum: int,
) -> tuple[bytes, str, int, dict[str, object]]:
    entry = _exact(descriptor, {"name", "sha256", "size"})
    if entry.get("name") != expected_name:
        _fail("MODEL3D_POSTER_REQUEST_INVALID")
    digest = entry.get("sha256")
    if not isinstance(digest, str) or not _HEX_64.fullmatch(digest):
        _fail("MODEL3D_POSTER_REQUEST_INVALID")
    size = _strict_int(entry.get("size"), 1, maximum)
    path = root / expected_name
    try:
        if path.is_symlink() or path.resolve(strict=True).parent != root or path.stat().st_size != size:
            _fail("MODEL3D_POSTER_RESOURCE_INVALID")
        payload = path.read_bytes()
    except WorkerError:
        raise
    except OSError:
        raise WorkerError("MODEL3D_POSTER_RESOURCE_INVALID") from None
    actual = hashlib.sha256(payload).hexdigest()
    if len(payload) != size or not hmac.compare_digest(actual, digest):
        _fail("MODEL3D_POSTER_RESOURCE_INVALID")
    return payload, actual, size, entry


def _validate_stats(value: object) -> dict[str, object]:
    stats = _exact(value, {
        "width", "height", "angles", "objectCount", "geometryCount", "materialCount",
        "vertexCount", "triangleCount",
    })
    if stats.get("angles") != list(ANGLES):
        _fail("MODEL3D_POSTER_RUNTIME_INVALID")
    _strict_int(stats.get("width"), POSTER_SIZE, POSTER_SIZE)
    _strict_int(stats.get("height"), POSTER_SIZE, POSTER_SIZE)
    _strict_int(stats.get("objectCount"), 2, 512)
    _strict_int(stats.get("geometryCount"), 1, 128)
    _strict_int(stats.get("materialCount"), 1, 64)
    _strict_int(stats.get("vertexCount"), 3, 200_000)
    _strict_int(stats.get("triangleCount"), 1, 400_000)
    return stats


def _validate_png(payload: bytes) -> tuple[str, int]:
    if not 1 <= len(payload) <= MAX_POSTER_BYTES:
        _fail("MODEL3D_POSTER_PNG_INVALID")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG" or image.size != (POSTER_SIZE, POSTER_SIZE):
                _fail("MODEL3D_POSTER_PNG_INVALID")
            image.load()
    except WorkerError:
        raise
    except (OSError, ValueError, UnidentifiedImageError):
        raise WorkerError("MODEL3D_POSTER_PNG_INVALID") from None
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _browser_executable(default: str) -> Path:
    configured = os.environ.get("QUANTECH_SCENE3D_CHROMIUM", default)
    path = Path(configured)
    try:
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            _fail("MODEL3D_POSTER_RENDERER_UNAVAILABLE")
        return path.resolve(strict=True)
    except OSError:
        raise WorkerError("MODEL3D_POSTER_RENDERER_UNAVAILABLE") from None


def _render(source: bytes, bundle: bytes) -> tuple[bytes, dict[str, object], list[str]]:
    try:
        bundle_text = bundle.decode("utf-8", errors="strict")
    except UnicodeError:
        raise WorkerError("MODEL3D_POSTER_RESOURCE_INVALID") from None
    playwright = browser = context = page = None
    attempts: list[str] = []
    try:
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        executable = _browser_executable(playwright.chromium.executable_path)
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(executable),
            args=[
                "--disable-background-networking", "--disable-component-update",
                "--disable-default-apps", "--disable-sync", "--metrics-recording-only",
                "--no-first-run", "--no-default-browser-check",
            ],
        )
        context = browser.new_context(
            viewport={"width": POSTER_SIZE, "height": POSTER_SIZE},
            service_workers="block", java_script_enabled=True,
        )
        page = context.new_page()

        def block_network(route: Any) -> None:
            if len(attempts) < 16:
                attempts.append(str(route.request.url)[:1_000])
            route.abort()

        page.route("**/*", block_network)
        page.set_content("<canvas id='poster' width='1024' height='1024'></canvas>")
        page.add_script_tag(content=bundle_text)
        import base64
        encoded = base64.b64encode(source).decode("ascii")
        stats = page.evaluate(
            """payload => QuaNTechModelPoster.renderModelPoster(
              document.getElementById('poster'), payload)""",
            encoded,
        )
        stats = _validate_stats(stats)
        if attempts:
            _fail("MODEL3D_POSTER_NETWORK_ATTEMPT")
        png = page.locator("#poster").screenshot(type="png")
        _validate_png(png)
        return png, stats, attempts
    except WorkerError:
        raise
    except Exception:
        if attempts:
            raise WorkerError("MODEL3D_POSTER_NETWORK_ATTEMPT") from None
        raise WorkerError("MODEL3D_POSTER_RENDERER_UNAVAILABLE") from None
    finally:
        if page is not None:
            try:
                page.evaluate("() => QuaNTechModelPoster.disposeModelPoster()")
            except Exception:
                pass
        for resource in (page, context, browser):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


def _write_receipt(path: Path, value: dict[str, object]) -> None:
    encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        _fail("MODEL3D_POSTER_RUNTIME_INVALID")
    with path.open("xb") as stream:
        stream.write(encoded)


def main() -> int:
    receipt: Path | None = None
    poster: Path | None = None
    try:
        root, request_path, receipt = _job_paths()
        request = _read_request(request_path)
        if request.get("schema") != REQUEST_SCHEMA:
            _fail("MODEL3D_POSTER_REQUEST_INVALID")
        source, source_hash, _, _ = _read_bound_file(
            root, request.get("source"), expected_name="model.glb", maximum=MAX_SOURCE_BYTES,
        )
        bundle_value = request.get("bundle")
        bundle_entry = _exact(bundle_value, {"name", "sha256", "size", "binding"})
        reduced_bundle = {key: bundle_entry[key] for key in ("name", "sha256", "size")}
        bundle, bundle_hash, bundle_size, _ = _read_bound_file(
            root, reduced_bundle, expected_name="model-poster.js", maximum=MAX_BUNDLE_BYTES,
        )
        binding = bundle_entry.get("binding")
        expected_binding = f"three@0.185.1:model-poster:sha256:{bundle_hash}"
        if binding != expected_binding:
            _fail("MODEL3D_POSTER_RESOURCE_INVALID")
        output = _exact(request.get("output"), {"name"})
        if output.get("name") != "poster.png":
            _fail("MODEL3D_POSTER_REQUEST_INVALID")
        poster = root / "poster.png"
        if poster.exists() or poster.is_symlink():
            _fail("MODEL3D_POSTER_REQUEST_INVALID")
        png, stats, attempts = _render(source, bundle)
        poster_hash, poster_size = _validate_png(png)
        with poster.open("xb") as stream:
            stream.write(png)
        _write_receipt(receipt, {
            "schema": RECEIPT_SCHEMA, "status": "ok", "source_sha256": source_hash,
            "bundle": {"sha256": bundle_hash, "size": bundle_size, "binding": binding},
            "poster": {
                "sha256": poster_hash, "size": poster_size,
                "width": POSTER_SIZE, "height": POSTER_SIZE,
            },
            "stats": stats, "network_attempts": attempts,
        })
        return 0
    except WorkerError as exc:
        if poster is not None:
            try:
                poster.unlink(missing_ok=True)
            except OSError:
                pass
        if receipt is not None and not receipt.exists():
            try:
                _write_receipt(receipt, {
                    "schema": RECEIPT_SCHEMA, "status": "error", "error": {"code": exc.code},
                })
            except Exception:
                pass
        return 2
    except Exception:
        if receipt is not None and not receipt.exists():
            try:
                _write_receipt(receipt, {
                    "schema": RECEIPT_SCHEMA, "status": "error",
                    "error": {"code": "MODEL3D_POSTER_RENDERER_UNAVAILABLE"},
                })
            except Exception:
                pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
