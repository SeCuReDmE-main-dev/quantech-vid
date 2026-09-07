# Local voice CPU pilot boundary

## Status

This is an isolated pilot worker, not an activated QuaNTecH-ViD narration mode. It is not wired into the API, renderer, MCP tools, WebMCP tools, or UI. Silent narration remains the production default. A passing synthetic contract test is not evidence that Kokoro inference works on the operator's CPU.

The pilot supports one stock configuration only:

- voice: `af_heart`
- language: `en-us`
- speed: `1.0`
- input: non-empty Unicode text, at most 1,000 Python characters
- output: mono, 16-bit PCM WAV at 24 kHz, at most 60 seconds
- provider: exactly `CPUExecutionProvider`

It does not accept voice arrays, uploaded voices, voice blending, cloning, identity claims, biometrics, provider fallback, downloads, HTTP requests, or API keys.

## Components

`quantech_vid/local_voice.py` is the standard-library application-side wrapper. It validates and binds the isolated Python executable, worker source, complete site-packages tree, dependency versions, model, voices archive, and bundled eSpeak library/data tree. It invokes the worker with the repository's bounded process-tree helper using `shell=False`, then validates the filtered receipt and WAV before returning a result.

`quantech_vid/kokoro_worker.py` is a standalone worker. Run it with the explicit isolated interpreter and `-I`. Before importing NumPy, ONNX Runtime, phonemizer, or Kokoro, it clears the inherited environment except for minimal operating-system/temp variables and validates all resource paths, hashes, sizes, and tree digests. It supplies the bundled eSpeak paths explicitly and creates ONNX Runtime with only `CPUExecutionProvider`.

`-I` and the in-process environment scrub reduce Python import and configuration influence. They are not an operating-system sandbox and do not isolate Windows credentials available to the worker process. This pilot relies on fixed audited code and resources, a closed request, no network implementation, a bounded process tree, and operator-controlled execution. Stronger OS isolation remains an integration decision.

The worker performs no resource acquisition. Installation and artifact acquisition are separate operator actions governed by the hash-locked requirements and provenance record.

## Application-side usage

The wrapper discovers the fixed resource layout under an isolated root:

```text
<isolated-root>/
  models/
    kokoro-v1.0.onnx
    voices-v1.0.bin
  venv/
    Scripts/python.exe                 # Windows
    Lib/site-packages/                 # Windows
```

Example application-side construction, without running inference:

```python
from pathlib import Path

from quantech_vid.local_voice import LocalVoicePilot, LocalVoiceResources

resources = LocalVoiceResources.discover(Path(r"C:\isolated\kokoro-pilot"))
pilot = LocalVoicePilot(resources)
binding_payload, binding_sha256 = pilot.binding()
```

The actual bounded invocation is:

```python
result = pilot.synthesize(
    "A short English narration.",
    Path(r"C:\job-local-directory"),
    timeout=180,
    cancelled=lambda: False,
)
```

The job directory must already exist, must not be a symlink/junction, and must be dedicated to the job. The wrapper creates unique request, receipt, and WAV filenames and does not overwrite existing files.

## Worker CLI

```powershell
& '<isolated-python>' -I '<absolute-worker.py>' '<absolute-request.json>' '<absolute-receipt.json>'
```

The request and receipt paths must be siblings in the job directory. The receipt and output WAV must not already exist. The process exit code is zero only with an `ok` receipt.

The exact request shape is:

```json
{
  "schema": "quantech.local-voice.request.v1",
  "request_id": "lv_<opaque-id>",
  "binding_sha256": "<64 lowercase hex>",
  "text": "A short English narration.",
  "voice": "af_heart",
  "language": "en-us",
  "speed": 1.0,
  "job_dir": "<absolute existing job directory>",
  "output_wav": "<absolute new sibling .wav path>",
  "resource_root": "<absolute isolated root>",
  "model": {
    "path": "<absolute model path inside resource_root>",
    "size": 325505369,
    "sha256": "<exact FP32 release digest from LOCAL_VOICE_PROVENANCE.md>"
  },
  "voices": {
    "path": "<absolute voices path inside resource_root>",
    "size": 28214398,
    "sha256": "<exact voices release digest from LOCAL_VOICE_PROVENANCE.md>"
  },
  "site_packages": {
    "path": "<absolute isolated site-packages path>",
    "files": 0,
    "size": 0,
    "tree_sha256": "<64 lowercase hex>"
  },
  "dependencies": {
    "kokoro-onnx": "0.6.1"
  },
  "espeak": {
    "library_path": "<absolute bundled eSpeak library path>",
    "library_size": 0,
    "library_sha256": "<64 lowercase hex>",
    "data_path": "<absolute bundled eSpeak data directory>",
    "data_files": 0,
    "data_size": 0,
    "data_tree_sha256": "<64 lowercase hex>"
  },
  "max_audio_seconds": 60
}
```

The zero values above are shape placeholders. The wrapper always fills them with measured nonzero values and includes every installed distribution/version in `dependencies`; operators should not hand-author this request.

Success receipt:

```json
{
  "schema": "quantech.local-voice.receipt.v1",
  "status": "ok",
  "request_id": "lv_<opaque-id>",
  "binding_sha256": "<request binding>",
  "voice": "af_heart",
  "language": "en-us",
  "speed": 1.0,
  "audio": {
    "sha256": "<WAV SHA-256>",
    "size": 4844,
    "sample_rate": 24000,
    "channels": 1,
    "sample_width": 2,
    "frames": 2400,
    "duration_ms": 100
  },
  "runtime": {
    "providers": ["CPUExecutionProvider"],
    "dependencies": {"kokoro-onnx": "0.6.1"}
  }
}
```

Failures use only a stable code:

```json
{
  "schema": "quantech.local-voice.receipt.v1",
  "status": "error",
  "error": {"code": "LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED"}
}
```

No exception detail, path, input text, subprocess output, or environment value is returned to the caller.

## Integrity and archive checks

The official GitHub release API for release `203169879` publishes the exact artifact sizes and SHA-256 digests embedded above. The wrapper and worker both compare those values before inference.

The release's `kokoro-v1.0.int8.onnx` artifact is not qualified for this pilot. A real CPU check with the pinned ONNX Runtime 1.20.1 failed closed at its quantized `ConvInteger` node before producing audio. Old INT8 files may remain in the isolated operator directory, but discovery selects only the fixed FP32 filename and digest. There is no automatic INT8/FP32 model fallback.

Before NumPy allocation, the worker parses the ZIP central directory and every NPY header using the standard library. It rejects duplicate, encrypted, nested, unsupported-compression, object-dtype, oversized, malformed, and dimensionally invalid entries. Compressed input is capped at 64 MiB; declared uncompressed array data is capped at 64 MiB, 128 entries, 1,000,000 elements per voice, and 50,000,000 total elements. It then opens `voices-v1.0.bin` with `numpy.load(..., allow_pickle=False)`, checks the same entry set, requires floating-point arrays with finite values, and requires `af_heart`. The Hugging Face Pickle checkpoint is neither used nor supported.

The wrapper recalculates the complete binding after the child exits. Any model, voices, eSpeak, dependency-tree, interpreter, or worker change causes a binding mismatch and rejects the output.

## Licensing and provenance boundary

- `kokoro-onnx` code is MIT licensed.
- The Kokoro model repository declares Apache-2.0 for the model weights and distributed stock voice artifact.
- `phonemizer` and eSpeak NG carry GPL-family obligations. They are currently confined to an operator-managed isolated environment and are not redistributed in application packages. Redistribution requires a dedicated notices/source-offer and packaging review.
- `ff_siwis` is not enabled by this pilot. Its SIWIS CC BY 4.0 attribution must be completed before a French stock-voice mode is activated.
- `af_heart` is treated only as a named stock model voice. The application must not claim that it represents, identifies, clones, or authenticates any person.
- Personal or cloned voices are outside this boundary.

## Activation gate

Do not connect this pilot to RenderPlan, rendering, API, UI, MCP, or WebMCP until an operator records a real invocation with:

- exact binding and official artifact digests;
- exact CPU-only provider receipt;
- dependency health result;
- valid nonempty WAV receipt;
- wall time, real-time factor, peak RSS, and installed disk footprint;
- confirmation that no network/provider key was used during inference;
- reviewed third-party notices.

Integration must keep `silent` as the default, make `local_kokoro_cpu` explicit, bind the resource digest into the approved RenderPlan, recheck it before and after rendering, and fail closed without OpenAI fallback.
