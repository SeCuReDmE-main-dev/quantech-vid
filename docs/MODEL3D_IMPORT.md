# Bounded GLB inspection boundary

Status: parser-only preparation for action 60. This is not a model import, viewer,
render path, avatar feature, or VRM implementation.

## API

`inspect_model3d(payload: bytes) -> Model3DMetadata` accepts an already-read byte
buffer. It never accepts a path, fetches a URI, writes a file, or changes source
rights. Success returns a frozen metadata record with the byte SHA-256, exact
size, fixed media type `model/gltf-binary`, glTF version, inert generator and
copyright strings, and structural counts. Those strings are descriptive input;
they are not trusted licence evidence and do not authorize use.

Failure raises `Model3DError` with one filtered code:

- `MODEL3D_SIZE_INVALID`
- `MODEL3D_CONTAINER_INVALID`
- `MODEL3D_JSON_INVALID`
- `MODEL3D_VERSION_UNSUPPORTED`
- `MODEL3D_EXTENSION_UNSUPPORTED`
- `MODEL3D_EXTERNAL_RESOURCE_REJECTED`
- `MODEL3D_UNSUPPORTED`
- `MODEL3D_BUFFER_BOUNDS_INVALID`
- `MODEL3D_ACCESSOR_INVALID`
- `MODEL3D_INDEX_INVALID`
- `MODEL3D_NONFINITE_GEOMETRY`
- `MODEL3D_GRAPH_INVALID`
- `MODEL3D_IMAGE_INVALID`
- `MODEL3D_COMPLEXITY_LIMIT`
- `MODEL3D_INVALID`

Callers must expose only the code, not parser exceptions or input content.

## Accepted subset

The implementation deliberately accepts a narrow, self-contained glTF 2.0 GLB:

- exactly one JSON chunk followed by one BIN chunk, with exact header length and
  four-byte chunk alignment;
- one buffer backed by that BIN chunk and no URI at any JSON depth;
- one scene at index zero, a fully reachable single-parent acyclic node forest,
  finite transforms, triangle primitives, bounded accessors, and in-range indices;
- core vertex attributes with their glTF component/type/normalization shapes;
- optional core PBR materials; skins and morph targets are not accepted by this
  initial profile because joint-index and deformation consumption are not yet
  validated end to end;
- optional PNG or JPEG images stored in non-target buffer views, whose decoded
  format, dimensions, and pixel count are verified with Pillow.

Duplicate JSON keys, `extras`, sparse accessors, animations, cameras, non-triangle
primitive modes, external/data URIs, and every extension are rejected. The empty
extension allowlist intentionally rejects Draco, Meshopt, Basis/KTX, GPU
instancing, lights, and all VRM extensions. A file being structurally valid under
this subset therefore does not mean that it is a VRM or a usable avatar.

## Resource limits

| Resource | Limit |
| --- | ---: |
| GLB bytes | 20,000,000 |
| JSON bytes | 1,000,000 |
| JSON nesting | 32 |
| JSON values / aggregate UTF-8 string bytes | 20,000 / 256,000 |
| nodes / meshes / primitives | 128 / 64 / 128 |
| accessors / buffer views | 256 / 256 |
| accessor components | 5,000,000 |
| materials / images / textures | 64 / 32 / 32 |
| skins / joints / morph targets accepted | 0 / 0 / 0 |
| vertices / triangles | 200,000 / 400,000 |
| image dimension / aggregate pixels | 4,096 / 16,000,000 |

Float accessor payloads and numeric transforms are checked for finite values and
an absolute magnitude no greater than 1,000,000. Declared `POSITION` minima and
maxima must contain the actual float32 values. Comparison permits only
`1e-6 + 1e-6 * max(abs(actual), abs(declared))` conversion tolerance.
Image dimensions are checked before Pillow fully decodes the encoded stream. The
overall byte cap also bounds every embedded image and BIN allocation.

## Required future integration work

A later source-admission feature must separately verify the admitted byte buffer
and source hash, enforce actor-scoped rights, keep originals private, derive a
safe poster, and bind the exact asset hash into the approved render plan. VRM
support additionally requires an explicit extension parser, humanoid constraints,
a compatible renderer/consumer, and human-confirmed licence and avatar-use terms.
None of those permissions or capabilities are inferred here.

This module is a defensive subset parser, not the Khronos conformance validator
and not an operating-system sandbox. Unsupported inputs fail closed.
