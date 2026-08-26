# Competitive landscape — three separate architectures

## Google

**Google Vids** is a collaborative video workspace. It combines prompt-and-document drafting, scenes, stock media, recording, AI voiceovers, avatars, transitions, captions, Workspace sharing, and export.

QuaNTecH integration target: Drive/Docs/Slides intake, Vids handoff, and a consented browser bridge when deterministic control is available.

## Microsoft

**Microsoft Clipchamp** is the closest Microsoft equivalent to Google Vids. Microsoft 365 Copilot Create can generate a script, AI narration, and stock-footage draft; Clipchamp supplies the editing timeline, captions, recording, media, brand controls, aspect ratios, and export.

This is a Microsoft architecture. Microsoft Copilot, Clipchamp, OneDrive, SharePoint, Stream, Graph, and Azure services remain Microsoft products even when a model dependency originates elsewhere.

QuaNTecH integration target: OneDrive/SharePoint asset exchange, Clipchamp handoff, and provider capabilities exposed through a Microsoft adapter.

## OpenAI

**Sora** is the closest OpenAI creative-video product, especially for generated clips, storyboard, remix, recut, loop, and blend. It is a generative scene surface rather than a complete Workspace-style collaborative editor.

**OpenAI Agents SDK** orchestrates agents, tools, handoffs, guardrails, approvals, and traces. **Codex** builds and repairs the repository and workflows. They support QuaNTecH-ViD rather than replacing its editing surface.

The Videos/Sora API documented on 2026-08-26 carries a deprecation notice with shutdown scheduled for 2026-09-24. QuaNTecH therefore treats Sora API calls as experimental and replaceable. The durable OpenAI path uses Agents SDK, stable media capabilities available at runtime, provider contracts, and the local renderer.

## Product position

```text
Google Vids       = collaborative Google video workspace
Microsoft Clipchamp = Microsoft video editor and Copilot Create destination
OpenAI Sora       = OpenAI generative video and storyboard surface
QuaNTecH-ViD      = provider-neutral podcast-to-video cockpit and quality layer
```

QuaNTecH-ViD competes through source fidelity, creator-owned voice and avatar, provider portability, reusable art direction, scene-level revision, local rendering, approvals, provenance, and evals.

## Official references

- [Google Vids](https://workspace.google.com/products/vids/)
- [Create video with Microsoft Copilot and Clipchamp](https://support.microsoft.com/en-us/clipchamp/how-to-create-video-with-copilot)
- [Clipchamp feature comparison](https://support.microsoft.com/en-us/clipchamp/feature-comparison-between-clipchamp-for-work-and-personal-versions)
- [OpenAI Sora](https://openai.com/sora/)
- [OpenAI Videos API deprecation notice](https://developers.openai.com/api/reference/typescript/resources/videos/methods/create)
