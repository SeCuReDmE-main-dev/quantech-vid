# Vision boundary

`quantech_analyze_visual_asset` analyzes only a source's admitted raster
derivative. It never reads or returns an uploaded original, filesystem path, or
URL. The actor must have persisted source scope and the source must allow the
`analyze` operation.

The store reads one non-symlink file beneath `admitted-assets`, bounded to 10
MiB, and verifies its stored size and SHA-256 before returning an in-memory
buffer. The vision boundary then fully decodes that same buffer, accepts only
PNG, JPEG, or WebP whose decoded format matches its media type, and limits the
2D representation to 16,000,000 pixels. Filtered source failures use
`SOURCE_ANALYSIS_UNSUPPORTED_MEDIA`, `SOURCE_ANALYSIS_TOO_LARGE`, or
`SOURCE_ANALYSIS_INTEGRITY_FAILED`; source lookup/scope and operation failures
retain the shared `SOURCE_NOT_FOUND` and `SOURCE_OPERATION_FORBIDDEN` codes.

The production default has no detector transport, endpoint, token, discovery,
or activation logic. It reports `VISION_PROVIDER_NOT_CONFIGURED`. A future
pilot can be dependency-injected only after an operator independently verifies
process isolation, the exact module identity, and model-weights licensing. A
caller-supplied `local` boolean or a server status field is not locality proof.
The current shared CodeProject.AI server is therefore not contacted or changed.

The optional adapter accepts a closed subset of the CodeProject.AI 2.9.5 object
detection response documented at
<https://codeproject.github.io/codeproject.ai/api/api_reference.html>. It binds
the exact `moduleId`, requires a matching bounded `count`, finite confidence and
coordinates, and boxes inside the verified raster. Pixel boxes are normalized
to `[0,1]`; confidence normalization uses an explicit pilot binding (`percent`
or `unit`) rather than guessing. Unknown response fields and malformed,
out-of-bounds, non-finite, or module-mismatched results fail closed.

Returned observations are object labels, confidence, and normalized 2D boxes.
They are `proposal_only`: they do not mutate projects or authorize production,
and they are not identity, facial-recognition, biometric, depth, or 3D
inferences. MCP, WebMCP, and HTTP tool dispatch all share this same service
boundary. Synthetic injected transports test the contract; they are not proof
that a real YOLO module, route, isolation boundary, or licensed weights are
available. Actions 84 and 87 remain blocked until those facts are established.
