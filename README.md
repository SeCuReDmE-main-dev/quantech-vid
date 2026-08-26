# QuaNTecH-ViD

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Voice, made visual.**

QuaNTecH-ViD is an open-source, local-first production studio for podcast creators who want distinctive, source-faithful videos with control over scenes, voice, avatar, approvals, and publishing.

The working core already provides a FastAPI studio, SQLite render jobs, Playwright capture, MoviePy/FFmpeg rendering, OpenAI text-to-speech, a Chrome capture bridge, subtitles, provenance, and deterministic tests. The nine-day WebMCP build adds the agentic side panel and provider adapters described below.

## Product quality contract

The MVP succeeds when target podcast creators prefer its output over the same-source videos produced by Gemini Notebook and Google Vids in a repeatable blind comparison.

QuaNTecH-ViD focuses on:

- faithful claims linked to source evidence;
- scene-by-scene creative control;
- a reusable visual identity across episodes;
- creator-owned voice and consented avatar workflows;
- 16:9, 9:16, captions, thumbnails, manifests, and QA reports;
- visible approvals before costly or public actions;
- traces and evals for agent decisions, provider calls, and recovery loops.

Read the [MVP quality bar](docs/MVP_QUALITY_BAR.md) and [nine-day build plan](docs/9_DAY_BUILD_PLAN.md).

## Architecture

```text
Podcast creator
  -> Chrome side panel
  -> OpenAI Agents SDK + WebMCP tool contracts
  -> ProviderAdapter capability router
       |- QuaNTecH local renderer
       |- Google Workspace / Google Vids
       |- OpenAI media and agent tools
       |- voice and avatar providers
       `- publishing and automation tools
  -> approval
  -> render / export / publish
  -> provenance + QA + trace receipt
```

Provider support is capability-based. Official APIs and trusted MCP servers receive priority. Browser bridges remain explicit, versioned, consented, and user-visible.

## Working core

- FastAPI studio bound to loopback;
- SQLite render queue and restart recovery;
- local assets, captures, caches, and output;
- Playwright site capture;
- MoviePy with bundled FFmpeg;
- OpenAI narration cached by content hash;
- MP4, WebM, SRT, VTT, poster, provenance, and QA artifacts;
- Chrome extension contract limited to the local studio;
- French and English project manifests.

## Quick start

Python 3.12 or newer is required.

```powershell
git clone https://github.com/SeCuReDmE-main-dev/quantech-vid.git
cd quantech-vid
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
playwright install chromium
Copy-Item .env.example .env
python -m uvicorn quantech_vid.api:app --host 127.0.0.1 --port 7476
```

Open `http://127.0.0.1:7476`.

## CLI

```powershell
quantech-vid doctor
quantech-vid validate-project .\projects\synthia-promo\project.json
quantech-vid render-promo .\projects\synthia-promo\project.json --locale en --profile landscape --output .\runtime\renders\demo
quantech-vid verify-render .\runtime\renders\demo
```

## Chrome extension

```powershell
cd plugins\chrome
npm ci
npm run test:contract
npm run build
```

Load `plugins/chrome/dist` as an unpacked extension. The current extension sends approved page captures to the loopback studio. The full side-panel workflow is tracked in the roadmap.

## Tests

```powershell
python -m pytest -q
cd plugins\chrome
npm run test:contract
npm run build
```

The current suite covers manifests, path safety, TTS cache keys, subtitles, job recovery and cancellation, API endpoints, a real short render, and the loopback-only extension contract.

## Documentation

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

The local production core is operational and tested. The agentic side panel, avatar workflow, provider adapters, and competitive benchmark are active hackathon work. Roadmap features remain labelled until code and tests establish them.

## License

MIT. See [LICENSE](LICENSE).
