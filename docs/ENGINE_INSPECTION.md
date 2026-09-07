# Human-triggered engine inspection

The paired studio exposes one explicit Inspect button per installed-client
adapter. POST /api/v2/engines/{provider}/inspect requires the human session and
CSRF protection; agents cannot trigger it. Providers are a fixed allowlist.
One inspection runs at a time, with bounded adapter probes. No executable path,
command, credential or remote endpoint is accepted from the browser.

Inspection may read local client version, the Codex-managed account status
without token refresh, and its quota. It does not initiate login or a model
turn, install software, request a password or choose a paid fallback. Responses
are validated EngineConnection objects, with no raw provider payload or secret.
Failures are filtered. The UI shows the check timestamp rather than claiming
continuous monitoring.

Installation, authentication, model access, quota and production are distinct.
Production remains unavailable until provider execution and native-tool
isolation are qualified. A confirmed ChatGPT session alone does not satisfy
that requirement. Copilot SDK/CLI absence and Antigravity CLI absence remain
visible; no substitution with Gemini CLI or another provider is made.
