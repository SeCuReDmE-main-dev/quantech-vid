# QuaNTecH-ViD

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**A local video workshop, with the creator in control.**

QuaNTecH-ViD is an open-source studio for turning admitted sources into editable scenes and inspectable video files. Its qualified v2 path is currently **silent local production**:

**Source → storyboard → saved revision → bounded plan → human approval → FFmpeg render → verified files.**

This is not yet the complete 3D, avatar, or multi-provider studio. The sections below distinguish working code, observed runtime behavior, and remaining work.

## Product quality contract

The longer-term quality goal is faithful, editable output that creators can evaluate against alternatives using the same source material. That comparative benchmark has not been completed; no preference or quality superiority is claimed.

QuaNTecH-ViD focuses on:

- faithful claims linked to source evidence;
- scene-by-scene creative control;
- a reusable visual identity across episodes;
- creator-owned voice and consented avatar workflows;
- 16:9, 9:16, captions, thumbnails, manifests, and QA reports;
- visible approvals before costly or public actions;
- traces and evals for agent decisions, provider calls, and recovery loops.

The earlier [quality bar](docs/MVP_QUALITY_BAR.md) and [nine-day build plan](docs/9_DAY_BUILD_PLAN.md) are design background, not evidence that every feature is delivered.

## Architecture

```text
React studio / shared MCP and progressive WebMCP tools
  -> paired loopback API
  -> admitted sources and versioned project
  -> immutable render plan
  -> separate human authorization
  -> one local FFmpeg job at a time
  -> files, hashes, provenance and QA receipt
```

Agents can inspect and propose changes, stage a plan, or execute a plan already authorized by the human. None of the eight tools can create an approval, publish, pay, or request account credentials.

## Working core

- React/TypeScript studio with light/dark themes, storyboard, scene ordering, undo/redo and local draft recovery;
- explicit synthetic-sample, PNG, JPEG, WebP and UTF-8 text admission, with rights declarations and source hashes;
- FastAPI bound to loopback, exact Host/Origin checks, one-time pairing, revocable sessions and server-enforced production authorization;
- SQLite versioned projects, render queue, idempotency, cancellation and interruption recovery;
- direct, bounded FFmpeg execution through `imageio-ffmpeg`;
- licensed platform fonts (Segoe UI on Windows, DejaVu/Liberation on Linux) with a bundled Pillow fallback and optional `QUANTECH_VID_FONT_REGULAR` / `QUANTECH_VID_FONT_BOLD` overrides;
- MP4, WebM, SRT, VTT, poster, provenance, and QA artifacts;
- authenticated file retrieval with byte count and SHA-256 verification before playback;
- eight shared API/MCP tool contracts and progressive native WebMCP registration;
- filtered connection inspectors for Codex, Copilot and Antigravity, separate from production availability.

A successful receipt proves the files checked by the renderer, not factual accuracy, media rights, human identity or teaching effectiveness. The current browser editor is qualified in English; legacy manifests also support French.

## Quick start

Python 3.13 is the tested development runtime (package minimum: 3.12). The studio build pins Node 24.18.1 and npm 11.16.0. Run the following in PowerShell:

```powershell
git clone https://github.com/SeCuReDmE-main-dev/quantech-vid.git
cd quantech-vid
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
npm --prefix packages/scene3d ci --ignore-scripts
npm --prefix packages/scene3d run build
npm --prefix apps/studio ci --ignore-scripts
npm --prefix apps/studio run build
python -m uvicorn quantech_vid.api:default_app --factory --host 127.0.0.1 --port 7476
```

In another terminal using the same virtual environment and data directory, run `quantech-vid pairing-code`. Enter that one-time, ten-minute code at `http://127.0.0.1:7476`. Keep it private. A fresh code supports reconnecting after a browser restart; it does not restore browser credentials or approvals. Do not overwrite an existing `.env`.

For a disposable, synthetic-only QA instance that does not load your `.env`, use `python tools/serve_studio_qa.py --port 8791` after building the frontend. It prints an operator code and uses a temporary data directory. The same-origin built application is the qualified browser path.

## Legacy CLI

```powershell
quantech-vid doctor
quantech-vid validate-project .\projects\synthia-promo\project.json
quantech-vid render-promo .\projects\synthia-promo\project.json --locale en --profile landscape --output .\runtime\renders\demo
quantech-vid verify-render .\runtime\renders\demo
```

Legacy narration can call a paid provider when explicitly configured by the operator. The v2 silent production path does not make those calls. Browser capture requires the separately installed Playwright browser; it is not needed for the synthetic v2 sample path.

## Agent tools and connection status

`quantech-vid-mcp` exposes the eight business tools through the official MCP SDK. Its transport accepts an explicitly provisioned, scoped agent token, never a human approval credential. See [production tools](docs/PRODUCTION_TOOLS.md).

The studio registers those tools only when native `document.modelContext` exists and the human enables them for a saved project. No native API is fabricated. Registration, successful agent invocation, and an external registry grade are three distinct proofs; see [browser QA](docs/STUDIO_QA.md).

The three [engine connection inspectors](docs/ENGINE_CONNECTIONS.md) distinguish installed clients, authentication, quota and production qualification. They do not silently initiate login, submit prompts, or switch to a paid API. A recognized ChatGPT session is not proof that its native tools are safely restricted for studio production.

## Chrome extension

```powershell
cd plugins\chrome
npm ci
npm run test:contract
npm run build
```

This package is the **legacy extension**, not the new v2 companion. Its contract tests and build remain available, but old mutation/capture API routes now fail closed with HTTP 410. Do not treat installing this package as proof of an integrated companion. The scoped Chrome/Edge companion, Firefox sidebar and other browser validations remain delivery work.

## Tests

```powershell
python -m pytest -q
python -m pip check
npm --prefix apps/studio run check
npm --prefix apps/studio audit --audit-level=low
cd plugins\chrome
npm run test:contract
npm run build
```

Tests cover manifests, path safety, subtitles, job recovery, cancellation, pairing, authorization refusal, immutable revisions, real short renders, transport contracts, UI sequencing, byte verification and the legacy extension contract. Simulated provider tests qualify contracts, not installed or authenticated production engines.

## Documentation

- [Production API v2 and security boundaries](docs/PRODUCTION_API_V2.md)
- [Author-declared statements and evidence provenance](docs/CLAIM_PROVENANCE.md)
- [Offline Three.js preview and production](docs/SCENE3D_PIPELINE.md)
- [Shared API/MCP/WebMCP business tools](docs/PRODUCTION_TOOLS.md)
- [Engine connection inspectors](docs/ENGINE_CONNECTIONS.md)
- [Observed studio/browser QA and remaining gaps](docs/STUDIO_QA.md)
- [MVP quality bar](docs/MVP_QUALITY_BAR.md)
- [Provider adapter architecture](docs/PROVIDER_ADAPTER_ARCHITECTURE.md)
- [Google, Microsoft, and OpenAI landscape](docs/COMPETITIVE_LANDSCAPE.md)
- [Forty-tool integration catalog](docs/INTEGRATION_CATALOG.md)
- [Nine-day build plan](docs/9_DAY_BUILD_PLAN.md)
- [Agent-loop eval plan](docs/evals/AGENT_LOOPING_EVAL_PLAN.md)
- [Validation baseline](docs/VALIDATION_BASELINE.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Project status

The silent local source-to-video path and an animated procedural Three.js diagram have been exercised in Chrome with real output playback. Six bounded 3D templates share the preview/export module; graph tests cover all six, while pixel and export qualification currently covers the diagram. Editable labels, a visible uncertainty banner and a claim-provenance receipt preserve the limits of the source material. Visual polish and full template/layout qualification remain open.

Still unqualified or unimplemented: imported 3D assets, avatars, local narration/transcription, provider-driven production, the new cross-browser companion, Windows packaging, CodeProject.AI inference, Scholarium integration, full accessibility qualification and external WebMCP scoring. File retention/deletion and large-output browser playback also need further work. See the dated QA record; do not infer release completeness from passing unit tests.

## License

MIT for this repository's code. See [LICENSE](LICENSE). Media, model weights, fonts and external SDKs retain their own licenses; a source's declared rights are not an independent license assessment.
