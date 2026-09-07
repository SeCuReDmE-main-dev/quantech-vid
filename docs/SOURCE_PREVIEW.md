# Source preview contract

`GET /api/v2/sources/{source_id}/preview` is an authenticated, read-only route
for the already-admitted raster derivative of a source. It never serves the
uploaded original, reveals an internal path, or returns a path or URL.

The opaque source identifier must match `src_[0-9a-f]{32}`. Missing, malformed,
other-owner, and out-of-agent-scope identifiers all return `404
SOURCE_NOT_FOUND`. The existing actor rules apply: a human may access their own
source, while an agent may access only a source in its persisted scope. The
source must also admit the `render` operation; otherwise the response is `403
SOURCE_OPERATION_FORBIDDEN`.

Before returning a response, the server verifies one bounded in-memory buffer:

- the stored path is a non-symlink file beneath `admitted-assets`;
- stored and actual sizes are no more than 10 MiB and agree;
- the buffer SHA-256 agrees with the `SourceAsset` SHA-256;
- the media type is exactly `image/png`, `image/jpeg`, or `image/webp`;
- the decoded raster format agrees with that media type; and
- decoded dimensions contain no more than 16,000,000 pixels.

The file path is not reopened after verification. A successful response is the
verified buffer with the exact raster `Content-Type`, an exact `Content-Length`,
`X-Content-Type-Options: nosniff`, and `Cache-Control: no-store`.

Filtered failure codes are `413 SOURCE_PREVIEW_TOO_LARGE`, `409
SOURCE_PREVIEW_INTEGRITY_FAILED`, and `415 SOURCE_PREVIEW_UNSUPPORTED_MEDIA`.
Error bodies follow the shared envelope and do not contain paths or exception
details.

The studio exposes an explicit Inspect button; selection does not fetch a
preview. It verifies response length, media type and SHA-256 again before
creating a Blob image. The in-memory LRU cache holds at most four images and
20 MiB, keyed by source ID/hash/size/type. Project contents, revision, output
format, metadata or pairing changes clear it. Late responses from the previous
context are rejected and unmounted image URLs are revoked. Nothing is cached
in localStorage or in HTTP caches. A preview is a snapshot, not permission to
render: render authorization and current source integrity are checked again.

This cache covers source-image inspection, not a final scene-render cache.
