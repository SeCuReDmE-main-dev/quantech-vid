# Engine connection contract

QuaNTecH-ViD has three provider-specific connection inspectors behind one public
`EngineConnection` model. This slice does **not** expose prompting or inference. Installation,
client version, runtime presence, authentication, credential expiry, rights, quota, and production
readiness remain separate fields; `unknown` is never promoted to `available` or `confirmed`.

## Safety boundary

- Discovery runs only `codex --version`, `copilot --version`, or `agy --version`, with argument
  arrays, no shell, a sanitized child environment, bounded output, deadline/cancellation handling,
  and descendant cleanup.
- Codex authentication inspection uses managed `codex app-server --stdio`, performs the required
  `initialize` / `initialized` handshake, and sends only `account/read` with
  `refreshToken: false`. It may read `account/rateLimits/read` after confirmed ChatGPT auth.
- Adapter results never contain email addresses, account IDs, cookies, passwords, access tokens,
  refresh tokens, process output, filesystem paths, or raw provider errors.
- No adapter starts login, logout, a model turn, a prompt, a native tool, or an artifact write.
- A future human click may invoke the documented provider-managed login journey identified by
  `connection_action.action_id`. These identifiers are plans for UI wiring, not login endpoints.
- Production stays `unavailable` for all three engines until native shell/file tools are proven
  disabled by enforceable runtime configuration. Prompt instructions are not accepted as that
  security boundary.
- Existing API agent credentials are server-enforced for one project and revision. External
  engines must receive only a capability minted for that exact scope; changing the project or
  revision requires a new session and revocation of the old one. This is covered by
  `tests/test_api.py::test_agent_scope_is_enforced_for_same_owner_projects_revisions_sources_and_plans`.
- Provider output labelled `SUCCESS` is insufficient. A future execution must return a provider-
  bound artifact ID and SHA-256 accepted by `validate_artifact_receipt`.

## Provider adapters

### OpenAI Codex

`CodexEngineAdapter` uses the stable App Server account protocol, not the experimental
`dynamicTools` or `item/tool/call` flow. A non-null supported `account` object confirms only the
authentication mode. It does not confirm model rights. `account/read` does not expose a documented
credential expiry, so `expires_at` remains null rather than being guessed.

Future connection action: `codex_managed_chatgpt_login`. Only a later explicit human click may
start the official managed ChatGPT `account/login/start` browser flow.

Primary source: [OpenAI Codex App Server documentation](https://learn.chatgpt.com/docs/app-server).

### GitHub Copilot

`CopilotEngineAdapter` discovers the optional `github-copilot-sdk` Python distribution separately
from a `copilot` runtime. The SDK is not added to QuaNTecH-ViD dependencies in this slice. Starting
the SDK can consult explicit tokens, environment variables, stored OAuth credentials, and GitHub
CLI credentials, so discovery deliberately does not start it or infer auth from installation.

Future connection action: `copilot_interactive_github_oauth`. It represents the official
interactive GitHub OAuth flow only. Copilot SDK production remains unavailable because the SDK is
in public preview and an enforceable native-tool allowlist has not been integrated.

Primary sources: [GitHub Copilot SDK authentication](https://docs.github.com/en/copilot/how-tos/copilot-sdk/auth/authenticate)
and the [official Python SDK repository](https://github.com/github/copilot-sdk/tree/main/python).

### Google Antigravity

`AntigravityEngineAdapter` resolves only the official `agy` binary. It never substitutes Gemini,
another Google client, or an arbitrary compatible-looking executable. The inspector does not launch
`agy` to test auth: official behavior may silently use a keyring session or open the browser if no
session exists. Therefore auth, rights, quota, and expiry remain unknown.

Future connection action: `antigravity_interactive_keyring_login`. A later human click may launch
the official interactive session. Headless `--sandbox` is not treated as proof that native tools
are absent because documented streams include command and file tools.

Primary sources: [Antigravity installation and authentication](https://antigravity.google/docs/cli/install/)
and [Antigravity headless mode](https://antigravity.google/docs/cli/headless/).

## Local observation on 2026-09-06

The filtered inspector reported:

- Codex CLI installed at version `0.149.1`; ChatGPT auth confirmed by `account/read`; current
  rate-limit bucket read successfully; rights unknown; production unavailable because native-tool
  isolation is not proven.
- GitHub Copilot Python SDK and CLI runtime missing; auth, rights, quota, and expiry unknown;
  production unavailable.
- Google Antigravity `agy` missing; auth, rights, quota, and expiry unknown; production unavailable.

The observation contains no account identity or credential and is not a deployment guarantee.
Run inspection again at decision time because installation, authentication, and quota can change.
