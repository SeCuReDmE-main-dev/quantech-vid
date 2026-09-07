# Production tool catalogue

QuaNTecH-ViD exposes one catalogue and one dispatcher through three surfaces:

- Python callers use `ToolService.dispatch(name, arguments)`.
- The paired API exposes `GET /api/v2/tools/catalog` and `POST /api/v2/tools/{name}` to agent clients only.
- `quantech-vid-mcp` publishes the same catalogue over official MCP stdio and forwards calls to the paired loopback API.

The implementation pins the official Python MCP SDK at `mcp==2.1.1`, the stable v2 release
supporting the 2026-07-28 protocol. The low-level SDK server is intentional: it advertises the
exact shared JSON Schema 2020-12 dictionaries, while `ToolService` applies the same Pydantic
`extra=forbid` validation used by direct and API calls. See the [official Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[low-level server guide](https://py.sdk.modelcontextprotocol.io/v2/advanced/low-level-server/),
and [PyPI release](https://pypi.org/project/mcp/2.1.1/).

## Authority boundary

All eight tools run as an already-paired **agent**. The HTTP API authenticates the agent bearer
token outside the tool arguments. The stdio process reads `QUANTECH_VID_AGENT_TOKEN` and
`QUANTECH_VID_API_ORIGIN` from its process environment and sends the credential only in the
loopback `Authorization` header. Tokens, CSRF values, operator pairing codes, human sessions,
and grant-minting inputs do not appear in any tool schema or result.

The bearer session is persisted against exactly one human-owned `project_id` and `revision`, plus
an explicit source allowlist. Every tool repeats that project/revision binding in its input, but the
server-side session scope—not the caller-supplied IDs—is authoritative. Same-owner projects,
unscoped revisions, plans, jobs, artifacts, and sources are rejected. An additional admitted source
can be proposed only when the human included its owner-verified ID while creating that scoped agent.
Historical agent sessions without scope are invalid.

Human authority stays exclusively in the paired production API. No MCP tool can pair a session,
register an agent, authorize or revoke a production grant, access a local path, fetch a URL,
invoke a shell, or call a paid provider. `quantech_run_approved_render` supplies only a plan ID,
its bound project/revision, and an idempotency key; SQLite independently finds and atomically
consumes an unexpired, unrevoked server-side grant for that exact agent and plan.

## Exact tools

1. `quantech_inspect_project` — read a specific authorized revision.
2. `quantech_stage_source_import` — propose adding an already-admitted opaque source ID.
3. `quantech_analyze_visual_asset` — return bounded structural image metadata. Semantic vision is explicitly `unavailable` with `VISION_PROVIDER_NOT_CONFIGURED`.
4. `quantech_stage_storyboard` — return a deterministic proposal/diff; no canonical write.
5. `quantech_stage_scene_changes` — return scene merge proposals; no canonical write.
6. `quantech_stage_render` — persist an immutable, bounded silent/local-FFmpeg `RenderPlan`; no approval.
7. `quantech_run_approved_render` — queue only a matching approved plan, preserving server-side idempotency.
8. `quantech_inspect_production_result` — read the bound job state and byte receipts without paths or download tokens.

Every input contains `project_id`, `revision`, and a bounded `timeout_ms`. Scene collections are
limited to 128; nested objects reject unknown properties; narration and localized copy have
explicit length limits; every declared track locale must have a scene title. Oversize, malformed,
wrong-actor, wrong-project, stale-revision, missing-approval, timeout, and internal failures return
closed, path-free reason codes.

Proposal results use the application-specific `quantech-scene-proposal-v1` format, not RFC 6902
JSON Patch. Storyboard append returns one `add /scenes/-` operation per scene; scene edits use an
explicit `merge` operation keyed by scene ID. The complete candidate document is validated before
delivery, including the 128-scene ceiling, unique scene IDs, declared sources, locale copy, and
string bounds.

If the local deadline is crossed after a plan or job has already been persisted, the dispatcher
returns that recoverable plan/job identity with `deadline_exceeded=true` and
`TOOL_DEADLINE_EXCEEDED_AFTER_COMMIT`; it never hides a committed mutation behind a bare timeout.
Plan creation is content-idempotent through the unique plan hash, and job creation requires the
same idempotency key. Non-mutating calls that exceed their deadline return `TOOL_TIMEOUT`.

Job cancellation remains the paired API operation `POST /api/v2/renders/{job_id}/cancel`; the
eight-name tool contract intentionally adds no ninth authority-bearing tool. Agents inspect the
subsequent `cancelled`, `failed`, or completed state with the eighth tool.

## Stdio launch

Provide an existing agent credential to the child process without putting it on its command line:

```powershell
$env:QUANTECH_VID_API_ORIGIN = "http://127.0.0.1:7476"
$env:QUANTECH_VID_AGENT_TOKEN = "<retrieve from the operator-controlled credential store>"
quantech-vid-mcp
```

The stdio server writes protocol frames only to stdout. It does not load a human credential,
start another agent, open a browser, or obtain production approval.
