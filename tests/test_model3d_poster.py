from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from PIL import Image

from quantech_vid.model3d import Model3DError
from quantech_vid.model3d_poster import (
    Model3DPosterError,
    render_model3d_poster,
)


def _binary() -> bytes:
    return struct.pack("<9f3H", 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 2)


def _document(binary_length: int = 42) -> dict:
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": binary_length}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36, "target": 34962},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
    }


def _glb(document: dict, binary: bytes) -> bytes:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 4)
    padded = binary + b"\x00" * ((-len(binary)) % 4)
    chunks = (
        struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
        + struct.pack("<II", len(padded), 0x004E4942) + padded
    )
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def _valid() -> bytes:
    binary = _binary()
    return _glb(_document(len(binary)), binary)


def _textured() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "navy").save(output, format="PNG")
    image = output.getvalue()
    geometry = _binary()
    binary = geometry + image
    document = _document(len(binary))
    document["bufferViews"].append({
        "buffer": 0, "byteOffset": len(geometry), "byteLength": len(image),
    })
    document["images"] = [{"bufferView": 2, "mimeType": "image/png"}]
    document["textures"] = [{"source": 0}]
    return _glb(document, binary)


def test_hash_profile_and_cancel_fail_before_renderer_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import quantech_vid.model3d_poster as module

    payload = _valid()
    monkeypatch.setattr(
        module, "_bundle_identity",
        lambda: (_ for _ in ()).throw(AssertionError("renderer must not be probed")),
    )
    with pytest.raises(Model3DPosterError, match="HASH_MISMATCH"):
        render_model3d_poster(payload, "0" * 64, tmp_path, lambda: False)
    textured = _textured()
    with pytest.raises(Model3DPosterError, match="PROFILE_UNSUPPORTED"):
        render_model3d_poster(
            textured, hashlib.sha256(textured).hexdigest(), tmp_path, lambda: False,
        )
    with pytest.raises(InterruptedError):
        render_model3d_poster(
            payload, hashlib.sha256(payload).hexdigest(), tmp_path, lambda: True,
        )
    assert list(tmp_path.glob("*.png")) == []


def test_output_and_deadline_contracts_are_bounded(tmp_path: Path) -> None:
    payload = _valid()
    digest = hashlib.sha256(payload).hexdigest()
    with pytest.raises(Model3DPosterError, match="OUTPUT_INVALID"):
        render_model3d_poster(payload, digest, Path("relative"), lambda: False)
    with pytest.raises(Model3DPosterError, match="OUTPUT_INVALID"):
        render_model3d_poster(payload, digest, tmp_path / "missing", lambda: False)
    with pytest.raises(Model3DPosterError, match="DEADLINE_INVALID"):
        render_model3d_poster(payload, digest, tmp_path, lambda: False, deadline_seconds=0)
    existing = tmp_path / "model3d-poster.png"
    existing.write_bytes(b"preserve")
    with pytest.raises(Model3DPosterError, match="OUTPUT_INVALID"):
        render_model3d_poster(payload, digest, tmp_path, lambda: False)
    assert existing.read_bytes() == b"preserve"


def test_invalid_glb_is_rejected_before_any_output(tmp_path: Path) -> None:
    payload = bytearray(_valid())
    payload[0:4] = b"FAIL"
    with pytest.raises(Model3DError, match="MODEL3D_CONTAINER_INVALID"):
        render_model3d_poster(
            bytes(payload), hashlib.sha256(payload).hexdigest(), tmp_path, lambda: False,
        )
    assert list(tmp_path.iterdir()) == []


def test_nonresponsive_worker_is_killed_on_timeout_and_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import quantech_vid.model3d_poster as module

    worker = tmp_path / "sleeping-worker.py"
    worker.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(module, "_worker_script", lambda: worker.resolve(strict=True))
    payload = _valid()
    digest = hashlib.sha256(payload).hexdigest()
    timeout_output = tmp_path / "timeout"
    timeout_output.mkdir()
    with pytest.raises(Model3DPosterError, match="DEADLINE_EXCEEDED"):
        render_model3d_poster(
            payload, digest, timeout_output, lambda: False, deadline_seconds=1,
        )
    assert list(timeout_output.iterdir()) == []

    cancel_output = tmp_path / "cancel"
    cancel_output.mkdir()
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        render_model3d_poster(
            payload, digest, cancel_output,
            lambda: time.monotonic() - started > 0.2,
            deadline_seconds=10,
        )
    assert list(cancel_output.iterdir()) == []


def test_worker_scrubs_secret_sentinel_before_third_party_import(tmp_path: Path) -> None:
    import quantech_vid.model3d_poster as poster_module

    worker = Path(poster_module.__file__).with_name("model3d_poster_worker.py").resolve(strict=True)
    probe = tmp_path / "probe.py"
    result = tmp_path / "result.txt"
    probe.write_text(
        "import os,runpy\n"
        f"runpy.run_path({str(worker)!r})\n"
        f"open({str(result)!r},'w',encoding='utf-8').write("
        "'1' if 'QUANTECH_TEST_SECRET_SENTINEL' in os.environ else '0')\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["QUANTECH_TEST_SECRET_SENTINEL"] = "x" * 37
    completed = subprocess.run(
        [sys.executable, "-I", probe], check=True, capture_output=True, env=environment,
    )
    assert completed.stdout == completed.stderr == b""
    assert result.read_text(encoding="utf-8") == "0"


def test_forged_error_code_and_png_hash_are_filtered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import quantech_vid.model3d_poster as module

    payload = _valid()
    digest = hashlib.sha256(payload).hexdigest()

    def malformed_error(args, **_kwargs):
        receipt = Path(args[-1])
        receipt.write_text(json.dumps({
            "schema": module.RECEIPT_SCHEMA, "status": "error", "error": {"code": []},
        }), encoding="utf-8")
        return subprocess.CompletedProcess(args, 2, b"", b"")

    monkeypatch.setattr(module, "run_command", malformed_error)
    error_output = tmp_path / "error"
    error_output.mkdir()
    with pytest.raises(Model3DPosterError) as caught:
        render_model3d_poster(payload, digest, error_output, lambda: False)
    assert caught.value.code == "MODEL3D_POSTER_RENDERER_UNAVAILABLE"
    assert list(error_output.iterdir()) == []

    png_buffer = io.BytesIO()
    Image.new("RGB", (1024, 1024), "navy").save(png_buffer, format="PNG")
    png = png_buffer.getvalue()

    def forged_hash(args, **_kwargs):
        request_path, receipt_path = Path(args[-2]), Path(args[-1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        (receipt_path.parent / "poster.png").write_bytes(png)
        receipt_path.write_text(json.dumps({
            "schema": module.RECEIPT_SCHEMA,
            "status": "ok",
            "source_sha256": digest,
            "bundle": {
                "sha256": request["bundle"]["sha256"],
                "size": request["bundle"]["size"],
                "binding": request["bundle"]["binding"],
            },
            "poster": {
                "sha256": "0" * 64, "size": len(png), "width": 1024, "height": 1024,
            },
            "stats": {
                "width": 1024, "height": 1024,
                "angles": ["front", "right", "back", "left"],
                "objectCount": 2, "geometryCount": 1, "materialCount": 1,
                "vertexCount": 3, "triangleCount": 1,
            },
            "network_attempts": [],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(module, "run_command", forged_hash)
    hash_output = tmp_path / "hash"
    hash_output.mkdir()
    with pytest.raises(Model3DPosterError) as caught:
        render_model3d_poster(payload, digest, hash_output, lambda: False)
    assert caught.value.code == "MODEL3D_POSTER_RECEIPT_INVALID"
    assert list(hash_output.iterdir()) == []


def test_actual_headless_triangle_contact_sheet_is_verified_and_offline(tmp_path: Path) -> None:
    payload = _valid()
    proof = render_model3d_poster(
        payload, hashlib.sha256(payload).hexdigest(), tmp_path, lambda: False,
        deadline_seconds=30,
    )
    assert proof.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert proof.path == tmp_path / "model3d-poster.png"
    assert proof.poster_sha256 == hashlib.sha256(proof.path.read_bytes()).hexdigest()
    assert proof.poster_size == proof.path.stat().st_size
    assert (proof.width, proof.height) == (1024, 1024)
    assert proof.camera_angles == ("front", "right", "back", "left")
    assert proof.network_attempts == ()
    assert proof.renderer_binding.startswith("three@0.185.1:model-poster:sha256:")
    assert (proof.geometry_count, proof.vertex_count, proof.triangle_count) == (1, 3, 1)
    with Image.open(proof.path) as image:
        assert image.format == "PNG" and image.size == (1024, 1024)
        assert image.getbbox() is not None
    with pytest.raises(FrozenInstanceError):
        proof.width = 1  # type: ignore[misc]
