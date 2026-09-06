# Offline Three.js production pipeline

QuaNTecH-ViD can render an optional procedural Three.js visual for a scene. This is a synthetic representation, not evidence or a scientific result. The original admitted source, its hash, claim provenance, human disclosure and approval boundary remain authoritative.

## Contract and plan binding

`SceneV2.visual_3d` is optional and omitted when absent, preserving historical version 2 document hashes. It contains only a six-value template kind, up to eight short text lines, a six-digit hex accent and `none`, `spin` or `pulse` animation. The scene title and duration remain the canonical `SceneV2` values. There is no URL, filesystem path, HTML, shader or executable field.

When a project contains a 3D scene, plan preparation verifies the local runtime and binds `three@<version>:sha256:<browser.js hash>` into `provider_resource_modes.scene3d`. Execution recalculates that identity before rendering. A missing bundle/browser returns `THREED_RENDERER_UNAVAILABLE`; a changed approved bundle returns `THREED_BUNDLE_MISMATCH`. Neither condition silently falls back to the 2D source image. Projects without `visual_3d` retain the existing 2D renderer.

## Capture

The product renderer starts a fresh headless Playwright Chromium process. It does not use a persistent user-data directory, cookies, an account or an existing browser session. All browser requests are aborted. The validated config is passed as an evaluation argument to the pinned local IIFE, which creates `QuaNTechScene3D.createSceneRenderer` on a blank in-memory canvas.

For every output frame, Python calls `renderAt(frameIndex / fps)` and captures the real canvas pixels. The Pillow compositor then adds the QuaNTecH-ViD/SecuredMe label, claim-status banner, original scene text and project disclosure. FFmpeg consumes a numbered local PNG sequence using structured arguments with `shell=False`, producing the same MP4, WebM, subtitle, poster, provenance and QA outputs as the 2D path.

## Bounds and cleanup

- Profiles are 320–1920 pixels per axis and at most 2,073,600 pixels.
- A project may capture at most 1,800 3D frames.
- Frame storage is limited to the smaller of the approved output limit and 1 GB.
- The capture deadline is 180 seconds.
- Cancellation closes the page, isolated context, browser and Playwright driver, then removes only the generated frame directories.
- The existing single-worker approved-render queue prevents concurrent heavy renders.

Node graph tests do not qualify pixels. The Python suite performs a real 320×320 Chromium capture at 12 fps, checks animation frame differences, and runs the frames through FFmpeg and media QA.
