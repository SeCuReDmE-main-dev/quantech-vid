# AGENTS.md

## Mission

Build QuaNTecH-ViD into a provider-neutral side-panel studio whose videos earn a measurable preference from podcast creators over generic source-to-video baselines.

## Current truth

The repository currently contains a tested local FastAPI/SQLite/MoviePy renderer, studio UI, project manifests, Chrome loopback capture bridge, subtitles, provenance, and QA artifacts.

The full side panel, WebMCP tools, OpenAI Agents SDK orchestration, avatar workflow, Google Vids bridge, Microsoft Clipchamp handoff, and publishing adapters are planned MVP work. Preserve this distinction in code and documentation.

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
