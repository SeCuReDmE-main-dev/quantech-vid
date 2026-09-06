# Production API v2

This API is a local, paired control plane. It does not treat loopback as authentication.
The v1 render, narration, capture, path-validation, and compatibility-token routes return
`410 LEGACY_API_DISABLED`; the CLI renderer remains available for explicit local operator use.

Start the service through its explicit factory: `python -m uvicorn quantech_vid.api:default_app --factory --host 127.0.0.1 --port 7476`. Importing
`quantech_vid.api` alone does not load `.env`, create runtime files, or start a worker.

## Boundary and credentials

- Every `/api/v2` request requires the configured exact `Host` value.
- Every mutating request requires the configured exact `Origin` value and has a 1 MB streamed body limit.
- `POST /api/v2/pair` consumes an operator-provided one-time code of at least 16 characters. The code is injected at startup and is never returned by health or bootstrap APIs.
- Human sessions use a bearer token plus `X-CSRF-Token` for mutations. Agent clients use a
  distinct bearer token scoped in SQLite to one existing project revision and an explicit source
  allowlist. They cannot create agents, mutate canonical projects, or mint production grants.
- `DELETE /api/v2/session`, `DELETE /api/v2/agents/{agent_id}`, and
  `DELETE /api/v2/production-grants/{grant_id}` provide revocation.
- Tokens belong in process memory or an OS credential store, never URLs or browser local storage.

Errors have the public shape `{"error":{"code":"...","message":"..."}}`. Validation and
runtime failures do not echo submitted content, local paths, provider details, or exception text.

## First complete local flow

1. `POST /api/v2/pair` as the human operator.
2. `POST /api/v2/sources/sample` as the human to create a deterministic, non-personal PNG onboarding fixture; `POST /api/v2/sources/upload` with a browser-selected raw file body and the `X-File-Name`, `X-Rights-Basis`, `X-Rights-Reference`, and optional `X-Source-Origin` headers; or `POST /api/v2/sources/admit` with a trusted one-time native selection handle. Encode the three free-text header values exactly once with JavaScript `encodeURIComponent`; the server rejects malformed escapes and decodes once, so Unicode names and literal percent signs round-trip without making encoded traversal valid. Upload accepts decoded PNG/JPEG/WebP or strict UTF-8 text, preserves the original privately, and admits a metadata-stripped derived PNG.
3. `POST /api/v2/projects` with a `SceneProjectV2`. Update through `PUT /api/v2/projects/{id}` with `base_revision`; stale writes fail with `REVISION_CONFLICT`.
4. `POST /api/v2/agents` as the paired human with `agent_id`, `label`, `project_id`, and
   `revision`. Sources already declared by that revision enter the scope. Optional `source_ids`
   authorize additional owner-verified sources for proposal staging; possession of an opaque ID is
   never sufficient. A changed project or revision requires a new agent session and revocation of
   the prior agent.
5. `POST /api/v2/render-plans`. The immutable plan binds the project and revision hashes, every source hash, locale, output profile, local-silent/local-FFmpeg modes, and resource limits.
6. `POST /api/v2/render-plans/{id}/authorize` as the human, naming the agent, exact
   `project_id`/`revision`, and an expiry. The plan and agent scope must match all three values.
7. `POST /api/v2/renders/run-approved` as that agent with an `Idempotency-Key` of at least 16 characters. A retry with the same key and request returns the original job; reuse for another request fails.
8. `GET /api/v2/renders/{job_id}` returns state and, after completion, artifact names, roles, media types, sizes, and SHA-256 receipts. It never returns internal paths. Fetch bytes from `GET /api/v2/renders/{job_id}/artifacts/{name}` with the bearer header and convert the response to a browser `Blob`; never put a token in a URL. Video reads verify the artifact and QA hashes, require passing MP4 and WebM QA, and accept a single range no larger than 8 MiB. `POST /api/v2/renders/{job_id}/cancel` is race-safe.

Only one heavy render may run. Queued work survives restart; an ambiguous `running` job is
recovered as `failed/RESTART_INTERRUPTED` and must be explicitly resubmitted. A receipt proves
the bytes produced by this execution, not factual correctness, pedagogical quality, or license
validity. Provenance and rights fields are operator declarations requiring human review.

## Intentionally unavailable

`POST /api/v2/sources/import-url` returns `501 NETWORK_IMPORT_DISABLED`. Redirect-by-redirect
address policy, DNS rebinding resistance, response streaming limits, and content decoding are
not yet implemented, so accepting remote URLs would create an SSRF boundary violation.

`GET /api/v2/sources` and `GET /api/v2/projects` provide path-free inventories. Humans see their
owned inventory; agents see only their persisted revision and source allowlist. Agent sessions
created by an older schema without a persisted scope fail closed with `AGENT_SCOPE_REQUIRED`.
`GET /api/v2/legacy-projects` lists only bundled, schema-valid v1 slugs; the human-only
`POST /api/v2/legacy-projects/{slug}/import` copies the original manifest and referenced assets
to private storage before producing revision 1 of a v2 project. It never accepts a client path.

This slice accepts uploads up to 50 MB and decoded images up to 40 million pixels. A native
selection-handle broker, deletion/retention controls for originals, and the eight MCP/WebMCP
wrappers remain separate work. Their absence must be shown as unavailable, not inferred from
the existence of the production API.

The server mounts `apps/studio/dist` when a build is present, otherwise the legacy `studio`
directory, with a same-origin content-security policy. Runtime and artifact directories are
never part of the static mount.
