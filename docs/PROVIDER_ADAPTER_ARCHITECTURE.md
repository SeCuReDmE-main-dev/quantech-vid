# Provider adapter architecture

## Goal

The Chrome side panel acts as one cockpit across tools the creator already uses. The project schema stays stable while provider adapters translate capabilities, authorization, jobs, errors, and artifacts.

## Adapter contract

```text
ProviderAdapter
  capabilities()
  connect(scopes)
  ingest(source_ref)
  create_storyboard(brief)
  generate_voice(voice_ref, script)
  generate_avatar(avatar_ref, consent_ref)
  render(project_ref, profile, idempotency_key)
  status(job_ref)
  export(artifact_ref, destination)
  revoke(identity_ref)
```

Every capability reports one availability class:

- `NATIVE`: implemented by the local QuaNTecH engine;
- `OFFICIAL_API`: supported provider API or SDK;
- `TRUSTED_MCP`: explicitly approved MCP server;
- `BROWSER_BRIDGE_EXPERIMENTAL`: visible, versioned browser workflow;
- `USER_HANDOFF`: prepared project passed to the user for completion;
- `UNAVAILABLE`: capability absent for the selected account or provider.

## Google path

- Drive, Docs, and Slides supply source material through supported Workspace access.
- Google Vids supplies scene editing, recording, stock media, voiceover, avatar, and collaboration capabilities available to the user’s account.
- The side panel prefers official interfaces. A browser bridge activates only with the user present, verifies the document and scene before each change, previews the mutation, and records a receipt.
- When a stable control interface is unavailable, QuaNTecH prepares the storyboard, assets, narration, captions, and handoff package for insertion in Vids.

## OpenAI path

- OpenAI Agents SDK owns orchestration, traces, handoffs, guardrails, and approval-aware tools.
- Official media capabilities handle narration, images, and other supported generation surfaces.
- Codex works on repository-scoped construction, test creation, trace diagnosis, and repair.
- The provider adapter advertises only capabilities confirmed for the active account and API version.
- Sora remains an optional generative-clip adapter. The currently documented Videos API is deprecated with shutdown scheduled for 2026-09-24, so the MVP keeps it outside the durable core.

## Microsoft path

- Clipchamp is the Microsoft editing and export surface closest to Google Vids.
- Microsoft 365 Copilot Create can prepare a script, narration, stock media, and a Clipchamp draft for eligible accounts.
- OneDrive, SharePoint, Stream, Graph, Azure Speech, Clipchamp, and Microsoft Copilot remain inside a Microsoft adapter.
- This provider boundary stays separate from OpenAI Agents SDK, Codex, Sora, and OpenAI media APIs.
- The MVP begins with capability discovery and a handoff package; deeper control follows an official API or a deterministic user-approved bridge.

## Generic MCP gateway

1. Discover metadata through a trusted registry or user-supplied endpoint.
2. Display publisher, transport, requested scopes, tools, and data route.
3. Allowlist the exact tools needed for the project.
4. Ask for approval before identity, render, delete, upload, or publish actions.
5. Keep credentials in authorization headers or provider stores.
6. Trace tool identity, normalized arguments, disposition, and returned evidence IDs.
7. Disable a server when its contract changes until validation succeeds again.

## Browser loop safety

The bridge fingerprints tab, document, scene, action, normalized inputs, and visible result. Repeated state with zero progress triggers one structured recovery. A further repetition produces `LOOP_BLOCKED` and a focused human request. Ambiguous publishing or rendering actions call status before retry.
