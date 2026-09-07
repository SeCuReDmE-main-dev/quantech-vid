# AGENTS.md

## Mission

Build QuaNTecH-ViD into a provider-neutral side-panel studio whose videos earn a measurable preference from podcast creators over generic source-to-video baselines.

## Current truth

The repository currently contains a tested local FastAPI/SQLite/direct-FFmpeg renderer, studio UI, project manifests, Chrome loopback capture bridge, subtitles, provenance, and QA artifacts.

The v2 studio now implements paired/scoped API/MCP tools, native WebMCP registration, immutable revisions, human-approved production, claim provenance and a shared procedural Three.js preview/render module. Native browser-agent invocation and external scoring remain unqualified. The full side panel, provider-driven production, avatar workflow and publishing adapters are still planned work. Preserve this distinction in code and documentation; consult docs/STUDIO_QA.md for bounded runtime evidence rather than treating a passing test as a completed release.

## Product invariants

- Podcast creators are the primary users.
- Creator voice and avatar require explicit consent, provenance, revocation, and deletion paths.
- Provider-specific logic stays behind `ProviderAdapter` contracts.
- Official APIs and trusted MCP servers receive priority.
- Browser bridges remain visible, versioned, approved, and experimental.
- Rendering, identity creation, upload, and publication require approval.
- Ambiguous mutations read status before retrying with the same idempotency key.
- Every traced failure that changes code adds an eval.

## Validation

```powershell
python -m pytest -q
cd plugins\chrome
npm ci
npm run test:contract
npm run build
```

Keep secrets, signing keys, runtime output, generated extension packages, and provider tokens outside Git.
