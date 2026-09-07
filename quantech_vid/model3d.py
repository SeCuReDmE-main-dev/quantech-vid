from __future__ import annotations

import hashlib
import io
import json
import math
import re
import struct
import warnings
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError


MAX_GLB_BYTES = 20_000_000
MAX_JSON_BYTES = 1_000_000
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 20_000
MAX_JSON_STRING_BYTES = 256_000
MAX_NODES = 128
MAX_MESHES = 64
MAX_PRIMITIVES = 128
MAX_ACCESSORS = 256
MAX_BUFFER_VIEWS = 256
MAX_MATERIALS = 64
MAX_IMAGES = 32
MAX_TEXTURES = 32
MAX_SKINS = 4
MAX_JOINTS = 128
MAX_MORPH_TARGETS = 64
MAX_VERTICES = 200_000
MAX_TRIANGLES = 400_000
MAX_TEXTURE_DIMENSION = 4_096
MAX_TEXTURE_PIXELS = 16_000_000
MAX_ACCESSOR_COMPONENTS = 5_000_000
MAX_FLOAT_MAGNITUDE = 1_000_000.0
BOUND_ABSOLUTE_TOLERANCE = 1e-6
BOUND_RELATIVE_TOLERANCE = 1e-6

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
ALLOWED_EXTENSIONS: frozenset[str] = frozenset()
_ATTRIBUTE = re.compile(r"^(POSITION|NORMAL|TANGENT|TEXCOORD_[01]|COLOR_0|JOINTS_0|WEIGHTS_0)$")
_COMPONENT_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_TYPE_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Model3DError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Model3DMetadata:
    sha256: str
    size: int
    media_type: str
    gltf_version: str
    generator: str | None
    copyright: str | None
    extensions: tuple[str, ...]
    scene_count: int
    node_count: int
    mesh_count: int
    primitive_count: int
    material_count: int
    image_count: int
    texture_count: int
    skin_count: int
    joint_count: int
    morph_target_count: int
    vertex_count: int
    triangle_count: int
    texture_pixel_count: int


def _fail(code: str = "MODEL3D_INVALID") -> None:
    raise Model3DError(code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("MODEL3D_JSON_INVALID")
        result[key] = value
    return result


def _object(value: Any, *, allowed: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail()
    if allowed is not None and not set(value).issubset(allowed):
        _fail("MODEL3D_UNSUPPORTED")
    return value


def _array(value: Any, *, maximum: int, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail()
    return value


def _integer(value: Any, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail()
    if maximum is not None and value > maximum:
        _fail()
    return value


def _number(value: Any, *, magnitude: float = 1_000_000.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("MODEL3D_NONFINITE_GEOMETRY")
    result = float(value)
    if not math.isfinite(result) or abs(result) > magnitude:
        _fail("MODEL3D_NONFINITE_GEOMETRY")
    return result


def _within_bound(actual: float, declared: float) -> float:
    return BOUND_ABSOLUTE_TOLERANCE + BOUND_RELATIVE_TOLERANCE * max(abs(actual), abs(declared))


def _index(value: Any, length: int) -> int:
    return _integer(value, 0, length - 1)


def _string(value: Any, maximum: int = 500) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        _fail()
    return value


def _optional_text(value: Any) -> str | None:
    return None if value is None else _string(value)


def _walk_json(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [0, 0]
    if depth > MAX_JSON_DEPTH:
        _fail("MODEL3D_COMPLEXITY_LIMIT")
    budget[0] += 1
    if budget[0] > MAX_JSON_ITEMS:
        _fail("MODEL3D_COMPLEXITY_LIMIT")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or "\x00" in key:
                _fail("MODEL3D_JSON_INVALID")
            budget[1] += len(key.encode("utf-8"))
            if key == "uri" or key == "extras":
                _fail("MODEL3D_EXTERNAL_RESOURCE_REJECTED" if key == "uri" else "MODEL3D_UNSUPPORTED")
            if key == "extensions":
                extensions = _object(child)
                if not set(extensions).issubset(ALLOWED_EXTENSIONS):
                    _fail("MODEL3D_EXTENSION_UNSUPPORTED")
            _walk_json(child, depth + 1, budget)
    elif isinstance(value, list):
        for child in value:
            _walk_json(child, depth + 1, budget)
    elif isinstance(value, str):
        budget[1] += len(value.encode("utf-8"))
    elif isinstance(value, float) and not math.isfinite(value):
        _fail("MODEL3D_JSON_INVALID")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        _fail("MODEL3D_JSON_INVALID")
    if budget[1] > MAX_JSON_STRING_BYTES:
        _fail("MODEL3D_COMPLEXITY_LIMIT")


def _parse_glb(payload: bytes) -> tuple[dict[str, Any], bytes]:
    if not isinstance(payload, bytes) or not 20 <= len(payload) <= MAX_GLB_BYTES:
        _fail("MODEL3D_SIZE_INVALID")
    try:
        magic, version, declared_length = struct.unpack_from("<III", payload, 0)
    except struct.error as exc:
        raise Model3DError("MODEL3D_CONTAINER_INVALID") from exc
    if magic != 0x46546C67 or version != 2 or declared_length != len(payload):
        _fail("MODEL3D_CONTAINER_INVALID")
    chunks: list[tuple[int, bytes]] = []
    offset = 12
    while offset < len(payload):
        if offset + 8 > len(payload):
            _fail("MODEL3D_CONTAINER_INVALID")
        length, kind = struct.unpack_from("<II", payload, offset)
        offset += 8
        if length % 4 or length < 1 or offset + length > len(payload):
            _fail("MODEL3D_CONTAINER_INVALID")
        chunks.append((kind, payload[offset:offset + length]))
        offset += length
    if offset != len(payload) or len(chunks) != 2 or chunks[0][0] != JSON_CHUNK or chunks[1][0] != BIN_CHUNK:
        _fail("MODEL3D_CONTAINER_INVALID")
    json_bytes, binary = chunks[0][1], chunks[1][1]
    if len(json_bytes) > MAX_JSON_BYTES:
        _fail("MODEL3D_COMPLEXITY_LIMIT")
    try:
        text = json_bytes.rstrip(b" ").decode("utf-8", errors="strict")
        if not text or "\x00" in text:
            _fail("MODEL3D_JSON_INVALID")
        document = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: _fail("MODEL3D_JSON_INVALID"))
    except Model3DError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Model3DError("MODEL3D_JSON_INVALID") from exc
    document = _object(document)
    _walk_json(document)
    return document, binary


def _validate_asset(document: dict[str, Any]) -> tuple[str | None, str | None, tuple[str, ...]]:
    allowed_top = {
        "asset", "scene", "scenes", "nodes", "meshes", "buffers", "bufferViews",
        "accessors", "materials", "images", "textures", "samplers", "skins",
        "extensionsUsed", "extensionsRequired", "extensions",
    }
    if not set(document).issubset(allowed_top):
        if "animations" in document or "cameras" in document:
            _fail("MODEL3D_UNSUPPORTED")
        _fail("MODEL3D_UNSUPPORTED")
    asset = _object(document.get("asset"), allowed={"version", "minVersion", "generator", "copyright"})
    if asset.get("version") != "2.0" or asset.get("minVersion", "2.0") != "2.0":
        _fail("MODEL3D_VERSION_UNSUPPORTED")
    used = _array(document.get("extensionsUsed", []), maximum=64)
    required = _array(document.get("extensionsRequired", []), maximum=64)
    if any(not isinstance(item, str) for item in used + required) or len(set(used)) != len(used) or len(set(required)) != len(required):
        _fail("MODEL3D_EXTENSION_UNSUPPORTED")
    if not set(used).issubset(ALLOWED_EXTENSIONS) or not set(required).issubset(ALLOWED_EXTENSIONS):
        _fail("MODEL3D_EXTENSION_UNSUPPORTED")
    if not set(required).issubset(used):
        _fail("MODEL3D_EXTENSION_UNSUPPORTED")
    if document.get("extensions") not in (None, {}):
        _fail("MODEL3D_EXTENSION_UNSUPPORTED")
    return _optional_text(asset.get("generator")), _optional_text(asset.get("copyright")), tuple(sorted(used))


def _validate_buffers(document: dict[str, Any], binary: bytes) -> list[dict[str, Any]]:
    buffers = _array(document.get("buffers"), minimum=1, maximum=1)
    buffer = _object(buffers[0], allowed={"byteLength", "name"})
    byte_length = _integer(buffer.get("byteLength"), 1, len(binary))
    if len(binary) - byte_length not in {0, 1, 2, 3}:
        _fail("MODEL3D_CONTAINER_INVALID")
    if any(binary[byte_length:]):
        _fail("MODEL3D_CONTAINER_INVALID")
    if "name" in buffer:
        _string(buffer["name"])
    views = _array(document.get("bufferViews"), minimum=1, maximum=MAX_BUFFER_VIEWS)
    result: list[dict[str, Any]] = []
    for raw in views:
        view = _object(raw, allowed={"buffer", "byteOffset", "byteLength", "byteStride", "target", "name"})
        _integer(view.get("buffer"), 0, 0)
        offset = _integer(view.get("byteOffset", 0), 0, byte_length)
        length = _integer(view.get("byteLength"), 1, byte_length)
        if offset + length > byte_length:
            _fail("MODEL3D_BUFFER_BOUNDS_INVALID")
        stride = view.get("byteStride")
        if stride is not None:
            stride = _integer(stride, 4, 252)
            if stride % 4:
                _fail("MODEL3D_BUFFER_BOUNDS_INVALID")
        target = view.get("target")
        if target is not None:
            target = _integer(target, 34962, 34963)
            if target not in {34962, 34963}:
                _fail()
        if "name" in view:
            _string(view["name"])
        result.append({"offset": offset, "length": length, "stride": stride, "target": target})
    return result


def _validate_accessors(document: dict[str, Any], views: list[dict[str, Any]], binary: bytes) -> list[dict[str, Any]]:
    raw_accessors = _array(document.get("accessors"), minimum=1, maximum=MAX_ACCESSORS)
    accessors: list[dict[str, Any]] = []
    total_components = 0
    for raw in raw_accessors:
        accessor = _object(raw, allowed={
            "bufferView", "byteOffset", "componentType", "normalized", "count", "type", "min", "max", "name",
        })
        view_index = _index(accessor.get("bufferView"), len(views))
        component_type = accessor.get("componentType")
        accessor_type = accessor.get("type")
        if isinstance(component_type, bool) or not isinstance(component_type, int):
            _fail("MODEL3D_ACCESSOR_INVALID")
        if not isinstance(accessor_type, str):
            _fail("MODEL3D_ACCESSOR_INVALID")
        if component_type not in _COMPONENT_SIZE or accessor_type not in _TYPE_COMPONENTS:
            _fail("MODEL3D_ACCESSOR_INVALID")
        count = _integer(accessor.get("count"), 1, MAX_VERTICES * 4)
        components = _TYPE_COMPONENTS[accessor_type]
        component_size = _COMPONENT_SIZE[component_type]
        element_size = components * component_size
        total_components += count * components
        if total_components > MAX_ACCESSOR_COMPONENTS:
            _fail("MODEL3D_COMPLEXITY_LIMIT")
        normalized = accessor.get("normalized", False)
        if not isinstance(normalized, bool) or (component_type == 5126 and normalized):
            _fail("MODEL3D_ACCESSOR_INVALID")
        offset = _integer(accessor.get("byteOffset", 0), 0)
        view = views[view_index]
        stride = view["stride"] or element_size
        if stride < element_size or stride % component_size or offset % component_size:
            _fail("MODEL3D_BUFFER_BOUNDS_INVALID")
        required = offset + (count - 1) * stride + element_size
        if required > view["length"] or (view["offset"] + offset) % component_size:
            _fail("MODEL3D_BUFFER_BOUNDS_INVALID")
        parsed_bounds: dict[str, list[float]] = {}
        for bound_name in ("min", "max"):
            if bound_name in accessor:
                bound = _array(accessor[bound_name], minimum=components, maximum=components)
                parsed_bounds[bound_name] = [_number(value) for value in bound]
        if "min" in accessor and "max" in accessor:
            if any(low > high for low, high in zip(parsed_bounds["min"], parsed_bounds["max"])):
                _fail("MODEL3D_ACCESSOR_INVALID")
        if "name" in accessor:
            _string(accessor["name"])
        actual_min: list[float] | None = None
        actual_max: list[float] | None = None
        if component_type == 5126:
            base = view["offset"] + offset
            for item in range(count):
                values = struct.unpack_from("<" + "f" * components, binary, base + item * stride)
                if any(not math.isfinite(value) or abs(value) > MAX_FLOAT_MAGNITUDE for value in values):
                    _fail("MODEL3D_NONFINITE_GEOMETRY")
                if actual_min is None:
                    actual_min = list(values)
                    actual_max = list(values)
                else:
                    actual_min = [min(current, value) for current, value in zip(actual_min, values)]
                    actual_max = [max(current, value) for current, value in zip(actual_max or (), values)]
        accessors.append({
            "view": view_index, "offset": offset, "component_type": component_type,
            "type": accessor_type, "count": count, "components": components,
            "stride": stride, "normalized": normalized,
            "has_min": "min" in accessor, "has_max": "max" in accessor,
            "declared_min": parsed_bounds.get("min"), "declared_max": parsed_bounds.get("max"),
            "actual_min": actual_min, "actual_max": actual_max,
        })
    return accessors


def _validate_texture_info(value: Any, texture_count: int) -> None:
    info = _object(value, allowed={"index", "texCoord"})
    _index(info.get("index"), texture_count)
    if "texCoord" in info:
        _integer(info["texCoord"], 0, 1)


def _validate_materials(document: dict[str, Any], texture_count: int) -> int:
    materials = _array(document.get("materials", []), maximum=MAX_MATERIALS)
    for raw in materials:
        material = _object(raw, allowed={
            "name", "pbrMetallicRoughness", "normalTexture", "occlusionTexture",
            "emissiveTexture", "emissiveFactor", "alphaMode", "alphaCutoff", "doubleSided",
        })
        if "name" in material:
            _string(material["name"])
        pbr = _object(material.get("pbrMetallicRoughness", {}), allowed={
            "baseColorFactor", "baseColorTexture", "metallicFactor",
            "roughnessFactor", "metallicRoughnessTexture",
        })
        if "baseColorFactor" in pbr:
            values = _array(pbr["baseColorFactor"], minimum=4, maximum=4)
            if any(not 0 <= _number(value) <= 1 for value in values):
                _fail()
        for key in ("metallicFactor", "roughnessFactor"):
            if key in pbr and not 0 <= _number(pbr[key]) <= 1:
                _fail()
        for key in ("baseColorTexture", "metallicRoughnessTexture"):
            if key in pbr:
                _validate_texture_info(pbr[key], texture_count)
        for key in ("normalTexture", "occlusionTexture", "emissiveTexture"):
            if key in material:
                info = _object(material[key])
                if key == "normalTexture":
                    allowed = {"index", "texCoord", "scale"}
                elif key == "occlusionTexture":
                    allowed = {"index", "texCoord", "strength"}
                else:
                    allowed = {"index", "texCoord"}
                if not set(info).issubset(allowed):
                    _fail("MODEL3D_UNSUPPORTED")
                _index(info.get("index"), texture_count)
                if "texCoord" in info:
                    _integer(info["texCoord"], 0, 1)
                if "scale" in info:
                    _number(info["scale"])
                if "strength" in info and not 0 <= _number(info["strength"]) <= 1:
                    _fail()
        if "emissiveFactor" in material:
            values = _array(material["emissiveFactor"], minimum=3, maximum=3)
            if any(not 0 <= _number(value) <= 1 for value in values):
                _fail()
        alpha_mode = material.get("alphaMode", "OPAQUE")
        if not isinstance(alpha_mode, str) or alpha_mode not in {"OPAQUE", "MASK", "BLEND"}:
            _fail()
        if "alphaCutoff" in material and not 0 <= _number(material["alphaCutoff"]) <= 1:
            _fail()
        if "doubleSided" in material and not isinstance(material["doubleSided"], bool):
            _fail()
    return len(materials)


def _validate_images_and_textures(
    document: dict[str, Any], views: list[dict[str, Any]], binary: bytes,
) -> tuple[int, int, int]:
    images = _array(document.get("images", []), maximum=MAX_IMAGES)
    total_pixels = 0
    for raw in images:
        image = _object(raw, allowed={"bufferView", "mimeType", "name"})
        view_index = _index(image.get("bufferView"), len(views))
        mime = image.get("mimeType")
        if not isinstance(mime, str):
            _fail("MODEL3D_IMAGE_INVALID")
        if mime not in {"image/png", "image/jpeg"} or views[view_index]["target"] is not None:
            _fail("MODEL3D_IMAGE_INVALID")
        view = views[view_index]
        encoded = binary[view["offset"]:view["offset"] + view["length"]]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(encoded)) as decoded:
                    width, height = decoded.size
                    expected = "PNG" if mime == "image/png" else "JPEG"
                    if decoded.format != expected:
                        _fail("MODEL3D_IMAGE_INVALID")
                    if not 1 <= width <= MAX_TEXTURE_DIMENSION or not 1 <= height <= MAX_TEXTURE_DIMENSION:
                        _fail("MODEL3D_IMAGE_INVALID")
                    if total_pixels + width * height > MAX_TEXTURE_PIXELS:
                        _fail("MODEL3D_COMPLEXITY_LIMIT")
                    if getattr(decoded, "n_frames", 1) != 1:
                        _fail("MODEL3D_IMAGE_INVALID")
                    decoded.load()
        except Model3DError:
            raise
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombWarning) as exc:
            raise Model3DError("MODEL3D_IMAGE_INVALID") from exc
        total_pixels += width * height
        if "name" in image:
            _string(image["name"])
    samplers = _array(document.get("samplers", []), maximum=MAX_TEXTURES)
    for raw in samplers:
        sampler = _object(raw, allowed={"magFilter", "minFilter", "wrapS", "wrapT", "name"})
        mag_filter = _integer(sampler.get("magFilter", 9729), 9728, 9729)
        min_filter = _integer(sampler.get("minFilter", 9987), 9728, 9987)
        wrap_s = _integer(sampler.get("wrapS", 10497))
        wrap_t = _integer(sampler.get("wrapT", 10497))
        if mag_filter not in {9728, 9729}:
            _fail()
        if min_filter not in {9728, 9729, 9984, 9985, 9986, 9987}:
            _fail()
        if wrap_s not in {33071, 33648, 10497} or wrap_t not in {33071, 33648, 10497}:
            _fail()
        if "name" in sampler:
            _string(sampler["name"])
    textures = _array(document.get("textures", []), maximum=MAX_TEXTURES)
    for raw in textures:
        texture = _object(raw, allowed={"sampler", "source", "name"})
        _index(texture.get("source"), len(images))
        if "sampler" in texture:
            _index(texture["sampler"], len(samplers))
        if "name" in texture:
            _string(texture["name"])
    return len(images), len(textures), total_pixels


def _read_indices(accessor: dict[str, Any], views: list[dict[str, Any]], binary: bytes) -> list[int]:
    if accessor["type"] != "SCALAR" or accessor["component_type"] not in {5121, 5123, 5125} or accessor["normalized"]:
        _fail("MODEL3D_INDEX_INVALID")
    fmt = {5121: "B", 5123: "H", 5125: "I"}[accessor["component_type"]]
    view = views[accessor["view"]]
    if view["target"] not in {None, 34963}:
        _fail("MODEL3D_INDEX_INVALID")
    base = view["offset"] + accessor["offset"]
    return [struct.unpack_from("<" + fmt, binary, base + index * accessor["stride"])[0]
            for index in range(accessor["count"])]


def _validate_meshes(
    document: dict[str, Any], accessors: list[dict[str, Any]], views: list[dict[str, Any]],
    binary: bytes, material_count: int,
) -> tuple[int, int, int, int, int]:
    meshes = _array(document.get("meshes"), minimum=1, maximum=MAX_MESHES)
    primitive_count = morph_count = vertices = triangles = 0
    for raw_mesh in meshes:
        mesh = _object(raw_mesh, allowed={"primitives", "weights", "name"})
        primitives = _array(mesh.get("primitives"), minimum=1, maximum=MAX_PRIMITIVES)
        primitive_count += len(primitives)
        if primitive_count > MAX_PRIMITIVES:
            _fail("MODEL3D_COMPLEXITY_LIMIT")
        for raw_primitive in primitives:
            primitive = _object(raw_primitive, allowed={"attributes", "indices", "material", "mode", "targets"})
            if _integer(primitive.get("mode", 4), 4, 4) != 4:
                _fail("MODEL3D_UNSUPPORTED")
            attributes = _object(primitive.get("attributes"))
            if "POSITION" not in attributes or any(not _ATTRIBUTE.fullmatch(key) for key in attributes):
                _fail("MODEL3D_ACCESSOR_INVALID")
            attribute_indices = {key: _index(value, len(accessors)) for key, value in attributes.items()}
            if "JOINTS_0" in attributes or "WEIGHTS_0" in attributes:
                _fail("MODEL3D_UNSUPPORTED")
            for semantic, accessor_index in attribute_indices.items():
                attribute = accessors[accessor_index]
                if views[attribute["view"]]["target"] not in {None, 34962}:
                    _fail("MODEL3D_ACCESSOR_INVALID")
                component_type = attribute["component_type"]
                accessor_type = attribute["type"]
                normalized = attribute["normalized"]
                valid = False
                if semantic == "POSITION":
                    valid = accessor_type == "VEC3" and component_type == 5126 and not normalized
                    valid = valid and attribute["has_min"] and attribute["has_max"]
                elif semantic == "NORMAL":
                    valid = accessor_type == "VEC3" and component_type == 5126 and not normalized
                elif semantic == "TANGENT":
                    valid = accessor_type == "VEC4" and component_type == 5126 and not normalized
                elif semantic.startswith("TEXCOORD_"):
                    valid = accessor_type == "VEC2" and (
                        (component_type == 5126 and not normalized)
                        or (component_type in {5121, 5123} and normalized)
                    )
                elif semantic == "COLOR_0":
                    valid = accessor_type in {"VEC3", "VEC4"} and (
                        (component_type == 5126 and not normalized)
                        or (component_type in {5121, 5123} and normalized)
                    )
                if not valid:
                    _fail("MODEL3D_ACCESSOR_INVALID")
            position = accessors[attribute_indices["POSITION"]]
            for actual, declared in zip(position["actual_min"], position["declared_min"]):
                if actual + _within_bound(actual, declared) < declared:
                    _fail("MODEL3D_ACCESSOR_INVALID")
            for actual, declared in zip(position["actual_max"], position["declared_max"]):
                if actual - _within_bound(actual, declared) > declared:
                    _fail("MODEL3D_ACCESSOR_INVALID")
            if position["type"] != "VEC3" or position["component_type"] != 5126 or position["normalized"]:
                _fail("MODEL3D_ACCESSOR_INVALID")
            if any(accessors[index]["count"] != position["count"] for index in attribute_indices.values()):
                _fail("MODEL3D_ACCESSOR_INVALID")
            vertices += position["count"]
            if vertices > MAX_VERTICES:
                _fail("MODEL3D_COMPLEXITY_LIMIT")
            if "indices" in primitive:
                index_values = _read_indices(accessors[_index(primitive["indices"], len(accessors))], views, binary)
                if len(index_values) % 3 or any(value >= position["count"] for value in index_values):
                    _fail("MODEL3D_INDEX_INVALID")
                triangles += len(index_values) // 3
            else:
                if position["count"] % 3:
                    _fail("MODEL3D_INDEX_INVALID")
                triangles += position["count"] // 3
            if triangles > MAX_TRIANGLES:
                _fail("MODEL3D_COMPLEXITY_LIMIT")
            if "material" in primitive:
                _index(primitive["material"], material_count)
            targets = _array(primitive.get("targets", []), maximum=MAX_MORPH_TARGETS)
            if targets:
                _fail("MODEL3D_UNSUPPORTED")
        if "weights" in mesh:
            _fail("MODEL3D_UNSUPPORTED")
        if "name" in mesh:
            _string(mesh["name"])
    return len(meshes), primitive_count, morph_count, vertices, triangles


def _validate_nodes_and_scenes(
    document: dict[str, Any], mesh_count: int,
) -> tuple[int, int, int, int]:
    nodes = _array(document.get("nodes"), minimum=1, maximum=MAX_NODES)
    skins = _array(document.get("skins", []), maximum=MAX_SKINS)
    if skins:
        _fail("MODEL3D_UNSUPPORTED")
    joint_total = 0
    children: list[list[int]] = []
    parent_count = [0] * len(nodes)
    for raw_node in nodes:
        node = _object(raw_node, allowed={
            "children", "mesh", "skin", "matrix", "translation", "rotation", "scale", "weights", "name",
        })
        if "matrix" in node and any(key in node for key in ("translation", "rotation", "scale")):
            _fail("MODEL3D_GRAPH_INVALID")
        for key, length, magnitude in (("matrix", 16, 1_000_000.0), ("translation", 3, 1_000.0),
                                        ("rotation", 4, 2.0), ("scale", 3, 1_000.0)):
            if key in node:
                values = _array(node[key], minimum=length, maximum=length)
                parsed = [_number(value, magnitude=magnitude) for value in values]
                if key == "rotation":
                    norm = math.sqrt(sum(value * value for value in parsed))
                    if not 0.999 <= norm <= 1.001:
                        _fail("MODEL3D_NONFINITE_GEOMETRY")
        if "mesh" in node:
            _index(node["mesh"], mesh_count)
        if "skin" in node:
            _fail("MODEL3D_UNSUPPORTED")
        if "weights" in node:
            _fail("MODEL3D_UNSUPPORTED")
        if "name" in node:
            _string(node["name"])
        node_children = _array(node.get("children", []), maximum=MAX_NODES)
        indices = [_index(value, len(nodes)) for value in node_children]
        if len(set(indices)) != len(indices):
            _fail("MODEL3D_GRAPH_INVALID")
        for child in indices:
            parent_count[child] += 1
            if parent_count[child] > 1:
                _fail("MODEL3D_GRAPH_INVALID")
        children.append(indices)
    scenes = _array(document.get("scenes"), minimum=1, maximum=4)
    scene_index = _index(document.get("scene"), len(scenes))
    if scene_index != 0 or len(scenes) != 1:
        _fail("MODEL3D_UNSUPPORTED")
    scene = _object(scenes[0], allowed={"nodes", "name"})
    roots = [_index(value, len(nodes)) for value in _array(scene.get("nodes"), minimum=1, maximum=MAX_NODES)]
    if len(set(roots)) != len(roots) or any(parent_count[root] for root in roots):
        _fail("MODEL3D_GRAPH_INVALID")
    state = [0] * len(nodes)
    def visit(index: int, depth: int) -> None:
        if depth > MAX_JSON_DEPTH or state[index] == 1:
            _fail("MODEL3D_GRAPH_INVALID")
        if state[index] == 2:
            return
        state[index] = 1
        for child in children[index]:
            visit(child, depth + 1)
        state[index] = 2
    for root in roots:
        visit(root, 1)
    if any(value != 2 for value in state):
        _fail("MODEL3D_GRAPH_INVALID")
    return len(scenes), len(nodes), len(skins), joint_total


def _inspect_model3d(payload: bytes) -> Model3DMetadata:
    document, binary = _parse_glb(payload)
    generator, copyright_value, extensions = _validate_asset(document)
    views = _validate_buffers(document, binary)
    accessors = _validate_accessors(document, views, binary)
    image_count, texture_count, texture_pixels = _validate_images_and_textures(document, views, binary)
    material_count = _validate_materials(document, texture_count)
    mesh_count, primitive_count, morph_count, vertices, triangles = _validate_meshes(
        document, accessors, views, binary, material_count,
    )
    scene_count, node_count, skin_count, joint_count = _validate_nodes_and_scenes(
        document, mesh_count,
    )
    return Model3DMetadata(
        sha256=hashlib.sha256(payload).hexdigest(), size=len(payload),
        media_type="model/gltf-binary", gltf_version="2.0",
        generator=generator, copyright=copyright_value, extensions=extensions,
        scene_count=scene_count, node_count=node_count, mesh_count=mesh_count,
        primitive_count=primitive_count, material_count=material_count,
        image_count=image_count, texture_count=texture_count, skin_count=skin_count,
        joint_count=joint_count, morph_target_count=morph_count,
        vertex_count=vertices, triangle_count=triangles,
        texture_pixel_count=texture_pixels,
    )


def inspect_model3d(payload: bytes) -> Model3DMetadata:
    """Inspect a bounded self-contained core GLB and return inert immutable metadata.

    This parser does not authorize rights, grant source operations, load VRM, or
    execute/fetch any resource referenced by the input. Malformed input is
    exposed only through a filtered ``Model3DError`` code.
    """
    try:
        return _inspect_model3d(payload)
    except Model3DError:
        raise
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError, struct.error):
        raise Model3DError("MODEL3D_INVALID") from None
