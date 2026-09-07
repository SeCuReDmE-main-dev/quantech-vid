# Exact-original source lineage

Browser uploads admitted after this feature carry an optional, closed
`SourceProvenance.original` descriptor:

```json
{
  "name": "lesson.md",
  "sha256": "<64 lowercase hex characters>",
  "size": 1234,
  "media_type": "text/markdown",
  "transformation": "literal-text-preview-v1"
}
```

The transformation is either `literal-text-preview-v1` for UTF-8 text and
Markdown or `rgb-png-v1` for admitted PNG/JPEG/WebP images. The server creates
this descriptor from the streamed bytes. Selection-handle admission and legacy
migration accept a smaller declaration schema and reject an `original` field;
they never invent lineage. Historic sources and plans remain valid without it.

The byte-exact original stays in the private runtime store. An additive SQLite
relationship associates its private path with the opaque `SourceAsset` ID. The
path never appears in API responses, plans, logs, or artifacts. Authenticated
source listing returns the descriptor to the owning human (and only to an agent
already scoped to that source through the existing project/revision contract).

When linked originals exist, a new render plan includes `original_hashes`. The
field is omitted when empty so historic plan serialization and hashes remain
unchanged. Planning and approved execution both require the linked file,
descriptor, stored relationship, size, and SHA-256 to agree. Failure is closed
as `SOURCE_ORIGINAL_INTEGRITY_FAILED`; a plan-to-database mismatch is
`PLAN_INTEGRITY_FAILED`.

Execution repeats this check after rendering, before emitting provenance and
successful receipts. A changed or missing original fails the job even if the
renderer already produced media. This is a pre/post-render integrity check,
not a claim of continuous filesystem isolation or protection against an
administrator changing bytes after the final check.

A successful render emits a receipted `source-lineage` JSON artifact. It binds
the project revision/hash and render plan/hash to each original hash, derived
hash, media type, size, and transformation. It intentionally omits the original
filename and every private path. Hash agreement proves byte identity and the
local transformation record; it does not independently verify authorship,
rights, factual content, or scientific validity.

Deletion and retention controls for exact originals remain an explicit open
item. This slice does not serve original bytes and does not backfill old files
by guessing from free-form provenance notes.
