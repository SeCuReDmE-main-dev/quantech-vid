# Offline GLB poster renderer

This module provides the bounded renderer used by explicit
[GLB source admission](GLB_SOURCE_ADMISSION.md). It produces a static derivative;
it does not implement editable imported scenes or avatar workflows.

## Contract

```python
render_model3d_poster(
    payload: bytes,
    expected_sha256: str,
    output_dir: Path,
    cancelled: Callable[[], bool],
    deadline_seconds: float = 30.0,
) -> Model3DPosterProof
```

The byte buffer is first passed to `inspect_model3d`. The supplied lowercase
SHA-256 must match that inspected buffer. The current poster profile then
explicitly refuses models containing images or textures. The GLB parser already
refuses URIs, extensions, compression, skins, morph targets, animations, and
cameras, so this renderer accepts only plain self-contained core geometry.

`output_dir` must be an existing absolute directory whose resolved spelling does
not cross a symlink or junction. The renderer will not overwrite
`model3d-poster.png`. It validates the screenshot from memory as a single-frame
1024 by 1024 PNG of at most 10,000,000 bytes before creating that file.

The returned frozen proof contains source and poster hashes, poster size and
dimensions, the four fixed angles, bounded runtime graph counts, the exact
Three.js poster-bundle binding, and the network-attempt list. Success requires
that list to be empty. This proof is renderer evidence only; it grants no source
rights and does not authorize publication or avatar use.

## Browser boundary

`packages/scene3d/src/model-poster.ts` is bundled separately from the existing
scene renderer:

```powershell
cd packages\scene3d
npm run build
```

The normal CI build now invokes the separate `build:model-poster` script and
creates the ignored outputs `dist/model-poster.js` and
`dist/model-poster.js.LEGAL.txt`. It does not change the existing `index.ts` or
`dist/browser.js` bundle and adds no dependency.

The standalone renderer:

- calls `GLTFLoader.parseAsync` with a verified in-memory `ArrayBuffer` and an
  empty base path;
- installs a `LoadingManager.setURLModifier` that throws on every attempted
  resource resolution and configures no optional decoder;
- runs in a headless context with service workers blocked and every routed
  network request aborted and recorded;
- repeats finite graph, geometry, material, vertex, triangle, and index bounds
  after Three.js parsing;
- rejects runtime cameras, lights, skinned meshes, morph data, textures, and
  animations;
- uses a fixed fit, light rig, color pipeline, 1024-square canvas, and front,
  right, back, and left camera views arranged as a contact sheet;
- uses no animation clock and disposes loaded geometries, materials, textures,
  the renderer, and its WebGL context.

The parent copies the already-inspected GLB and the single-read, hash-bound bundle
bytes into a private job directory beneath `output_dir`. It invokes
`model3d_poster_worker.py` as the current Python interpreter with `-I`, fixed
request/receipt paths, `shell=False`, and the repository's supervised
`run_command` process helper. The worker rechecks both job-local hashes, keeps the
verified bytes in memory, and injects the exact bundle as script content; it does
not hash one bundle file and later load a different path into the page. Receipt
schema, counts, hashes, sizes, PNG bytes, and the empty network list are validated
again by the parent before the final exclusive file creation.

Before importing Pillow or Playwright, the worker replaces its inherited
environment with a small runtime allowlist: Windows/runtime temporary and home
paths, locale fields, and the optional explicit
`QUANTECH_SCENE3D_CHROMIUM` path. Proxy, token, provider, telemetry, and unrelated
application variables are not retained. Standard output and error are redirected
to the null sink, so worker/library diagnostics cannot disclose inherited values;
the receipt contains filtered codes only.

The deadline is constrained to 1 through 60 seconds. Timeout or cancellation
terminates and reaps the isolated worker through the existing process supervisor,
then removes the private job directory without creating the final PNG. On Windows,
the supervisor uses a kill-on-close Job Object when assignment succeeds; the
small inherited pre-assignment race and the helper's direct-process fallback when
Job assignment is unavailable remain documented limitations. `-I` and the child
boundary are not an operating-system sandbox.

## Error surface

Poster failures expose only bounded codes through `Model3DPosterError` or
`Model3DPosterUnavailable`, including hash, unsupported-profile, output,
deadline, runtime, PNG, network-attempt, and renderer-unavailable failures.
Cancellation uses `InterruptedError("Render cancelled")`, matching the existing
renderer convention. Structural GLB failures remain filtered `Model3DError`
codes from the parser. Parent-side filesystem, JSON, Unicode, recursion, type,
and malformed-receipt failures are also converted to filtered poster codes and
never include a local path or worker exception string.

## Remaining work

Source admission supplies actor-scoped rights declarations, original retention,
derivative registration and plan hash binding. Production approval remains
separate. Images need a separately qualified in-memory
blob-resource design before they can be enabled. VRM requires its own extension,
humanoid, licence, consent, revocation, and consumer work and is not supported by
this poster renderer.
