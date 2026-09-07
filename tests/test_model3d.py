from __future__ import annotations

import hashlib
import io
import json
import math
import struct
from dataclasses import FrozenInstanceError

import pytest
from PIL import Image

from quantech_vid.model3d import Model3DError, inspect_model3d


def _document(binary_length: int = 42) -> dict:
    return {
        "asset": {"version": "2.0", "generator": "synthetic test fixture"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
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


def _triangle_binary(*, first: float = 0.0, indices: tuple[int, int, int] = (0, 1, 2)) -> bytes:
    positions = struct.pack("<9f", first, 0, 0, 1, 0, 0, 0, 1, 0)
    return positions + struct.pack("<3H", *indices)


def _glb(document: dict, binary: bytes, *, raw_json: bytes | None = None) -> bytes:
    raw = raw_json if raw_json is not None else json.dumps(
        document, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    raw += b" " * ((-len(raw)) % 4)
    padded_binary = binary + b"\x00" * ((-len(binary)) % 4)
    body = (
        struct.pack("<II", len(raw), 0x4E4F534A) + raw
        + struct.pack("<II", len(padded_binary), 0x004E4942) + padded_binary
    )
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


def _valid() -> bytes:
    binary = _triangle_binary()
    return _glb(_document(len(binary)), binary)


def test_minimal_triangle_returns_immutable_inert_metadata() -> None:
    payload = _valid()
    metadata = inspect_model3d(payload)
    assert metadata.sha256 == hashlib.sha256(payload).hexdigest()
    assert metadata.media_type == "model/gltf-binary"
    assert metadata.gltf_version == "2.0"
    assert metadata.generator == "synthetic test fixture"
    assert metadata.extensions == ()
    assert (metadata.scene_count, metadata.node_count, metadata.mesh_count) == (1, 1, 1)
    assert (metadata.primitive_count, metadata.vertex_count, metadata.triangle_count) == (1, 3, 1)
    with pytest.raises(FrozenInstanceError):
        metadata.size = 0  # type: ignore[misc]
    assert not hasattr(metadata, "rights")


@pytest.mark.parametrize("offset,value", [(0, b"FAIL"), (4, struct.pack("<I", 1)), (8, struct.pack("<I", 1))])
def test_header_magic_version_and_length_are_exact(offset: int, value: bytes) -> None:
    payload = bytearray(_valid())
    payload[offset:offset + 4] = value
    with pytest.raises(Model3DError, match="MODEL3D_CONTAINER_INVALID"):
        inspect_model3d(bytes(payload))


def test_chunk_order_unknown_chunk_and_duplicate_json_are_rejected() -> None:
    payload = bytearray(_valid())
    payload[16:20] = struct.pack("<I", 0x004E4942)
    with pytest.raises(Model3DError, match="MODEL3D_CONTAINER_INVALID"):
        inspect_model3d(bytes(payload))
    payload = _valid()
    json_length = struct.unpack_from("<I", payload, 12)[0]
    binary_header = 20 + json_length
    changed = bytearray(payload)
    changed[binary_header + 4:binary_header + 8] = struct.pack("<I", 0x12345678)
    with pytest.raises(Model3DError, match="MODEL3D_CONTAINER_INVALID"):
        inspect_model3d(bytes(changed))
    raw = b'{"asset":{"version":"2.0","version":"2.0"}}'
    with pytest.raises(Model3DError, match="MODEL3D_JSON_INVALID"):
        inspect_model3d(_glb({}, b"\x00\x00\x00\x00", raw_json=raw))


def test_binary_padding_must_be_zero() -> None:
    payload = bytearray(_valid())
    payload[-1] = 1
    with pytest.raises(Model3DError, match="MODEL3D_CONTAINER_INVALID"):
        inspect_model3d(bytes(payload))


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda doc: doc["buffers"][0].update(uri="external.bin"), "MODEL3D_EXTERNAL_RESOURCE_REJECTED"),
        (lambda doc: doc.update(extensionsUsed=["KHR_draco_mesh_compression"]), "MODEL3D_EXTENSION_UNSUPPORTED"),
        (lambda doc: doc.update(animations=[]), "MODEL3D_UNSUPPORTED"),
        (lambda doc: doc.update(cameras=[]), "MODEL3D_UNSUPPORTED"),
        (lambda doc: doc["accessors"][0].update(sparse={"count": 1}), "MODEL3D_UNSUPPORTED"),
    ],
)
def test_external_resources_extensions_compression_sparse_cameras_and_animation_are_rejected(mutation, code: str) -> None:
    binary = _triangle_binary()
    document = _document(len(binary))
    mutation(document)
    with pytest.raises(Model3DError, match=code):
        inspect_model3d(_glb(document, binary))


def test_buffer_view_and_accessor_ranges_are_checked() -> None:
    binary = _triangle_binary()
    document = _document(len(binary))
    document["bufferViews"][0]["byteLength"] = 35
    with pytest.raises(Model3DError, match="MODEL3D_BUFFER_BOUNDS_INVALID"):
        inspect_model3d(_glb(document, binary))
    document = _document(len(binary))
    document["bufferViews"][0]["byteOffset"] = 20
    with pytest.raises(Model3DError, match="MODEL3D_BUFFER_BOUNDS_INVALID"):
        inspect_model3d(_glb(document, binary))


def test_position_contract_bounds_and_index_target_are_exact() -> None:
    binary = _triangle_binary()
    document = _document(len(binary))
    document["accessors"][0].pop("min")
    with pytest.raises(Model3DError, match="MODEL3D_ACCESSOR_INVALID"):
        inspect_model3d(_glb(document, binary))
    document = _document(len(binary))
    document["accessors"][0]["min"] = [2, 0, 0]
    with pytest.raises(Model3DError, match="MODEL3D_ACCESSOR_INVALID"):
        inspect_model3d(_glb(document, binary))
    document = _document(len(binary))
    document["bufferViews"][1]["target"] = 34962
    with pytest.raises(Model3DError, match="MODEL3D_INDEX_INVALID"):
        inspect_model3d(_glb(document, binary))


def test_actual_positions_are_bounded_and_must_fit_declared_bounds() -> None:
    binary = _triangle_binary(first=1e30)
    with pytest.raises(Model3DError, match="MODEL3D_NONFINITE_GEOMETRY"):
        inspect_model3d(_glb(_document(len(binary)), binary))
    binary = _triangle_binary(first=0.75)
    document = _document(len(binary))
    document["accessors"][0]["max"] = [0.5, 1, 0]
    with pytest.raises(Model3DError, match="MODEL3D_ACCESSOR_INVALID"):
        inspect_model3d(_glb(document, binary))


def test_malformed_primitive_types_are_filtered() -> None:
    binary = _triangle_binary()
    for key, value in (("type", []), ("componentType", 5126.0)):
        document = _document(len(binary))
        document["accessors"][0][key] = value
        with pytest.raises(Model3DError) as caught:
            inspect_model3d(_glb(document, binary))
        assert caught.value.code == "MODEL3D_ACCESSOR_INVALID"
    document = _document(len(binary))
    document["bufferViews"][0]["buffer"] = False
    with pytest.raises(Model3DError) as caught:
        inspect_model3d(_glb(document, binary))
    assert caught.value.code == "MODEL3D_INVALID"
    document = _document(len(binary))
    document["meshes"][0]["primitives"][0]["mode"] = 4.0
    with pytest.raises(Model3DError) as caught:
        inspect_model3d(_glb(document, binary))
    assert caught.value.code == "MODEL3D_INVALID"


def test_unicode_and_recursion_fail_through_filtered_public_error() -> None:
    binary = _triangle_binary()
    raw = b'{"asset":{"version":"2.0","generator":"\\ud800"}}'
    with pytest.raises(Model3DError) as caught:
        inspect_model3d(_glb({}, binary, raw_json=raw))
    assert caught.value.code == "MODEL3D_INVALID"
    assert caught.value.__cause__ is None
    deep = b"[" * 1_500 + b"0" + b"]" * 1_500
    with pytest.raises(Model3DError) as caught:
        inspect_model3d(_glb({}, binary, raw_json=deep))
    assert caught.value.code == "MODEL3D_INVALID"
    assert caught.value.__cause__ is None


def test_attribute_semantics_are_exact() -> None:
    binary = _triangle_binary()
    document = _document(len(binary))
    document["meshes"][0]["primitives"][0]["attributes"]["NORMAL"] = 1
    with pytest.raises(Model3DError, match="MODEL3D_ACCESSOR_INVALID"):
        inspect_model3d(_glb(document, binary))


def test_skins_and_morph_targets_are_not_in_the_initial_profile() -> None:
    binary = _triangle_binary()
    document = _document(len(binary))
    document["skins"] = [{"joints": [0]}]
    with pytest.raises(Model3DError, match="MODEL3D_UNSUPPORTED"):
        inspect_model3d(_glb(document, binary))
    document = _document(len(binary))
    document["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
    with pytest.raises(Model3DError, match="MODEL3D_UNSUPPORTED"):
        inspect_model3d(_glb(document, binary))
    document = _document(len(binary))
    document["meshes"][0]["primitives"][0]["attributes"]["JOINTS_0"] = 0
    with pytest.raises(Model3DError, match="MODEL3D_UNSUPPORTED"):
        inspect_model3d(_glb(document, binary))


def test_nonfinite_geometry_and_out_of_range_indices_are_rejected() -> None:
    binary = _triangle_binary(first=math.nan)
    with pytest.raises(Model3DError, match="MODEL3D_NONFINITE_GEOMETRY"):
        inspect_model3d(_glb(_document(len(binary)), binary))
    binary = _triangle_binary(indices=(0, 1, 3))
    with pytest.raises(Model3DError, match="MODEL3D_INDEX_INVALID"):
        inspect_model3d(_glb(_document(len(binary)), binary))


def test_graph_cycles_multiple_parents_and_unreachable_nodes_are_rejected() -> None:
    binary = _triangle_binary()
    document = _document(len(binary))
    document["nodes"] = [{"mesh": 0, "children": [1]}, {"children": [0]}]
    with pytest.raises(Model3DError, match="MODEL3D_GRAPH_INVALID"):
        inspect_model3d(_glb(document, binary))
    document = _document(len(binary))
    document["nodes"].append({})
    with pytest.raises(Model3DError, match="MODEL3D_GRAPH_INVALID"):
        inspect_model3d(_glb(document, binary))
    document = _document(len(binary))
    document["nodes"] = [{"mesh": 0, "children": [2]}, {"children": [2]}, {}]
    document["scenes"][0]["nodes"] = [0, 1]
    with pytest.raises(Model3DError, match="MODEL3D_GRAPH_INVALID"):
        inspect_model3d(_glb(document, binary))


def _with_image(image_bytes: bytes, mime: str) -> bytes:
    geometry = _triangle_binary()
    image_offset = len(geometry)
    binary = geometry + image_bytes
    document = _document(len(binary))
    document["bufferViews"].append({
        "buffer": 0, "byteOffset": image_offset, "byteLength": len(image_bytes),
    })
    document["images"] = [{"bufferView": 2, "mimeType": mime}]
    document["textures"] = [{"source": 0}]
    return _glb(document, binary)


def _png(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "navy").save(output, format="PNG")
    return output.getvalue()


def _jpeg(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "navy").save(output, format="JPEG")
    return output.getvalue()


def test_embedded_png_is_decoded_and_counted_without_uri() -> None:
    payload = _with_image(_png(4, 3), "image/png")
    metadata = inspect_model3d(payload)
    assert metadata.image_count == 1
    assert metadata.texture_count == 1
    assert metadata.texture_pixel_count == 12


def test_embedded_jpeg_is_decoded_with_exact_media_type() -> None:
    metadata = inspect_model3d(_with_image(_jpeg(3, 2), "image/jpeg"))
    assert (metadata.image_count, metadata.texture_pixel_count) == (1, 6)


def test_image_mime_mismatch_and_dimension_limit_are_rejected() -> None:
    with pytest.raises(Model3DError, match="MODEL3D_IMAGE_INVALID"):
        inspect_model3d(_with_image(_png(4, 3), "image/jpeg"))
    with pytest.raises(Model3DError, match="MODEL3D_IMAGE_INVALID"):
        inspect_model3d(_with_image(_png(4_097, 1), "image/png"))


def test_image_uri_and_data_uri_are_both_rejected() -> None:
    binary = _triangle_binary()
    for uri in ("texture.png", "data:image/png;base64,AAAA"):
        document = _document(len(binary))
        document["images"] = [{"uri": uri}]
        with pytest.raises(Model3DError, match="MODEL3D_EXTERNAL_RESOURCE_REJECTED"):
            inspect_model3d(_glb(document, binary))


def test_input_type_and_size_are_bounded() -> None:
    with pytest.raises(Model3DError, match="MODEL3D_SIZE_INVALID"):
        inspect_model3d(bytearray(_valid()))  # type: ignore[arg-type]
    with pytest.raises(Model3DError, match="MODEL3D_SIZE_INVALID"):
        inspect_model3d(b"glTF")
    with pytest.raises(Model3DError, match="MODEL3D_SIZE_INVALID"):
        inspect_model3d(b"\x00" * 20_000_001)
