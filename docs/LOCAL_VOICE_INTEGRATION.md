# Local Kokoro CPU production integration boundary

## Status

The backend accepts and signs an explicit `local_kokoro_cpu` narration mode. `Settings.local_voice_runtime_root` is optional and defaults to `None`. The API composition root constructs the fixed pilot only when the operator explicitly configures `QUANTECH_VID_LOCAL_VOICE_ROOT` and the complete resource binding succeeds. Invalid or absent resources leave the silent studio usable. Startup performs no synthesis or downloads. No user environment file is changed by this implementation.

Health reports only `experimental_local_voice_configured`; it is not a claim of perceptual quality. Every plan and approved render rechecks the binding. A synthetic-authority integration on 2026-09-07 produced real 15-second MP4 and WebM files with passing QA and eight receipts. Planning took 19.061 seconds and the complete flow 166.436 seconds on that run. This is not browser-human approval proof or a latency guarantee.

Silent narration remains the default. The local mode is experimental. CPU memory measurement is complete, but third-party packaging/notices review and human perceptual review remain qualification gates. It must not be presented as production-ready.

The isolated FP32 pilot produced 10.707 seconds of audio with a measured worker peak working set of 1,002,774,528 bytes and sampled aggregate peak of 1,033,342,976 bytes. The complete isolated footprint was 672,043,983 bytes, including the retained but unqualified INT8 artifact, and measured end-to-end wall time was 91.038 seconds. These are observations from one host, not general performance guarantees. Repeated synthesis is not bitwise waveform-deterministic; each returned WAV is integrity-hashed and validated, but callers must not require its digest to equal a prior run.

## Client contract

Clients use the existing render-plan request. There are no client-supplied model paths, hashes, providers, runtime roots, or voice embeddings.

```http
POST /api/v2/render-plans
Content-Type: application/json
Authorization: Bearer <scoped agent session>
```

```json
{
  "project_id": "prj_<opaque-id>",
  "revision": 1,
  "locale": "en",
  "profile": "square",
  "narration_mode": "local_kokoro_cpu",
  "max_duration_seconds": 600,
  "max_output_bytes": 500000000
}
```

Local-mode constraints:

- The immutable selected track must be English.
- Its `voice` must be omitted, `null`, or exactly `af_heart`.
- Narration must contain 1–1,000 characters.
- The signed project timeline remains bounded by the existing 600-second plan limit.
- Generated spoken audio is independently capped at 60 seconds.
- If spoken audio exceeds the project timeline, the job fails; it is never cut to fit.
- If it is shorter, the existing audio normalizer pads it to the timeline.

The returned signed plan contains:

```json
{
  "narration_mode": "local_kokoro_cpu",
  "provider_resource_modes": {
    "narration": "local-kokoro-cpu",
    "render": "local-ffmpeg",
    "local_voice": "<server-computed resource binding SHA-256>",
    "local_voice_voice": "af_heart",
    "local_voice_language": "en-us"
  }
}
```

`provider_resource_modes` is server evidence, not a configuration surface. Clients must not calculate or edit it. Normal human authorization, scoped agent identity, immutable revision matching, single-use grant consumption, and idempotent run submission remain mandatory.

The eight studio tools expose the same narration-mode choice through `quantech_stage_render`. Staging creates no grant and performs no render.

## Filtered errors

Planning can return:

| Code | Status | Meaning |
|---|---:|---|
| `LOCAL_VOICE_UNAVAILABLE` | 503 | No qualified server-owned runtime is injected. |
| `LOCAL_VOICE_INTEGRITY_FAILED` | 409 | Configured resources cannot produce a valid binding. |
| `LOCAL_VOICE_LOCALE_UNSUPPORTED` | 422 | The selected local track is not English. |
| `LOCAL_VOICE_VOICE_NOT_ALLOWED` | 422 | The immutable track requests a non-stock voice. |
| `LOCAL_VOICE_TEXT_INVALID` | 422 | Narration is empty or exceeds 1,000 characters. |

Approved jobs can additionally fail with:

- `LOCAL_VOICE_BINDING_MISMATCH`
- `LOCAL_VOICE_AUDIO_EXCEEDS_TIMELINE`
- `LOCAL_VOICE_AUDIO_INVALID`
- `LOCAL_VOICE_CPU_REQUIRED`
- `LOCAL_VOICE_SYNTHESIS_TIMEOUT`
- `LOCAL_VOICE_SYNTHESIS_FAILED`
- `LOCAL_VOICE_CANCELLED`

No error includes model paths, runtime paths, narration text, subprocess output, environment values, or exception details. There is no OpenAI fallback.

## Signed execution sequence

1. A scoped agent requests a plan for an immutable project revision.
2. The store validates locale, stock voice, text length, project target, sources, and current revision.
3. The server-owned pilot computes its exact resource binding.
4. The plan digest signs that binding together with project, sources, originals, output target, narration mode, and limits.
5. A human authorizes the exact plan for the scoped agent.
6. The agent consumes the matching single-use grant.
7. Immediately before rendering, the store revalidates the plan, revision, sources, originals, scene-3D binding when applicable, and local-voice binding.
8. The service passes only the injected pilot and the binding already signed in the plan to the renderer.
9. The pilot returns a CPU-only receipt and bounded WAV. The renderer rejects a mismatched binding/provider or audio longer than the timeline before normalization.
10. After rendering and QA, the store recomputes all persisted/runtime bindings again before successful artifact receipts are issued.

Any resource change between these stages fails closed.

## Silent compatibility

Existing silent requests retain:

```json
{
  "narration_mode": "silent",
  "provider_resource_modes": {
    "narration": "local-silent",
    "render": "local-ffmpeg"
  }
}
```

No local runtime is probed for silent planning or rendering, and the prior canonical plan-hash payload remains unchanged.

## Server composition still required

A later, separately reviewed composition change may:

1. Read the optional server-owned `QUANTECH_VID_LOCAL_VOICE_ROOT` setting.
2. Discover and validate `LocalVoiceResources` under that root.
3. Construct `LocalVoicePilot` only after qualification gates pass.
4. Inject that pilot into `ProductionStore`.

The CPU/memory measurement portion of this gate is complete for the pilot host. Perceptual acceptance and redistribution/notices review remain outstanding.

Until the separately reviewed composition change is made, `local_kokoro_cpu` plan requests deterministically return `LOCAL_VOICE_UNAVAILABLE`. The current code does not inspect the optional root automatically, download assets, install dependencies, start a worker, or run inference at server startup.
