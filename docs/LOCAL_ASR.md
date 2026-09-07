# Local ASR CPU pilot boundary

Status: bounded offline pilot, absent from the production API and UI. This is not
a production-ready transcription feature and it does not make speech-recognition
output a source fact.

## Public Python contract

`quantech_vid.local_asr` exposes:

```python
resources = LocalAsrResources.discover(isolated_runtime_root)
pilot = LocalAsrPilot(resources)
result = pilot.transcribe(
    source_id,
    source_sha256,
    verified_pcm16_mono_16khz_wav,
    existing_job_directory,
    timeout=600,
    cancelled=None,
)
```

`source_id` is an opaque 1–80 character identifier containing only letters,
digits, `_`, or `-`. `source_sha256` must be the SHA-256 of the exact WAV bytes.
The input is a canonical RIFF/WAVE file with a 44-byte header, mono signed
16-bit PCM, 16 kHz, no compression, and a duration from one frame through 300
seconds. The parent and child each read the file into a bounded buffer, validate
its structure and hash, and the child converts the PCM buffer to a NumPy float32
waveform. The user-controlled file path is never passed to `transcribe`.

`LocalAsrResult.receipt` is a closed receipt. It contains at most 128 segments
and 4,096 word observations with bounded text and finite, ordered timestamps.
It also binds the source and audio hashes, model revision, fixed task settings,
actual dependency versions, and exact CPU/`int8_float32` runtime declaration.

The worker request and receipt schemas are:

- `quantech.local-asr.request.v1`
- `quantech.local-asr.receipt.v1`
- resource binding `quantech.local-asr.binding.v1`
- worker protocol `faster-whisper-cpu-pilot-v1`

Requests and receipts are direct siblings in an existing job directory. The
worker is launched with an argument vector and `shell=False` through the shared
bounded process helper, which terminates the process tree on cancellation or
timeout.

## Fixed pilot

The pilot admits only this immutable English model manifest:

| Resource | Bytes | SHA-256 |
| --- | ---: | --- |
| `config.json` | 2,317 | `14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5` |
| `model.bin` | 75,537,502 | `1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a` |
| `tokenizer.json` | 2,128,466 | `929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df` |
| `vocabulary.txt` | 422,309 | `ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf` |

Model ID: `Systran/faster-whisper-tiny.en`

Revision: `7d45cf02c1ed72d240c0dbf99d544d19bef1b5a3`

The model card declares MIT. The runtime binding covers only the four files
that CTranslate2/faster-whisper require; a retained README or provenance record
is not a model input. Original OpenAI pickle checkpoints are not admitted.

All inference parameters are fixed:

```text
device=cpu
compute_type=int8_float32
cpu_threads=2
num_workers=1
language=en
task=transcribe
beam_size=1
best_of=1
temperature=0.0
condition_on_previous_text=false
word_timestamps=true
vad_filter=false
local_files_only=true
```

The runtime root, interpreter, worker, installed dependency tree and versions,
and four model files are hashed into the binding. The worker validates the
binding and resources before importing NumPy or faster-whisper. The parent
recomputes both the resource binding and input WAV hash after the child exits.

The current qualified CTranslate2 candidate is 4.8.2. On this CPU, explicitly
requesting `int8_float32` produces the same exact runtime-reported compute type;
the ambiguous `int8` alias is not accepted by the receipt validator. CTranslate2 4.6.0 was
rejected in the isolated Python 3.13 environment because it imported the
removed `pkg_resources` interface. The complete root-owned hash-locked
requirements and provenance receipt remain the acquisition authority.

## Offline and isolation boundary

At worker entry, before third-party imports, the process clears the inherited
environment except operating-system and temporary-directory values. It then
sets Hugging Face and Transformers offline flags, disables telemetry, disables
tokenizer parallelism, fixes common CPU thread variables at two, and uses an
absolute local model directory with `local_files_only=True`.

These controls prevent the intended code path from selecting a remote model or
tokenizer. They are not an operating-system sandbox, firewall proof, credential
isolation during Python startup, or proof that every dependency lacks network
capability. Deployment must still use an isolated account/environment and an
external network-denial test. `-I` isolates Python import behavior; it is not a
security sandbox.

The implementation does not use pickle, Torch checkpoints, model download
helpers, URLs, Hugging Face tokens, or cache-based model selection during a job.

## Proposal semantics

Every receipt declares:

```json
{
  "machine_proposal_only": true,
  "human_review_required": true,
  "speaker_identity_inferred": false
}
```

Recognized text may hallucinate, omit, substitute, or mis-time speech. It must
not be described as verified, independently corroborated, speaker-identified,
or a factual statement from the source. Prompt-like words in audio are inert
proposal text, never instructions.

The pilot has no production store, project revision, approval, transcript,
render, API, MCP, WebMCP, or UI integration. A future integration must persist
the receipt separately as a proposal, require visible human review, and create
a new immutable project revision when accepted. It must never silently populate
canonical transcript segments.

## Filtered failures

Callers receive stable codes without library exceptions, paths, stdout, or
stderr. Principal categories are:

- `LOCAL_ASR_SOURCE_INVALID` / `LOCAL_ASR_SOURCE_INTEGRITY_FAILED`
- `LOCAL_ASR_AUDIO_INVALID`
- `LOCAL_ASR_RESOURCE_MISSING`, `LOCAL_ASR_RESOURCE_LINK_REJECTED`,
  `LOCAL_ASR_RESOURCE_OUTSIDE_ROOT`, or `LOCAL_ASR_RESOURCE_INTEGRITY_FAILED`
- `LOCAL_ASR_MODEL_MANIFEST_INVALID` / `LOCAL_ASR_RUNTIME_INVALID`
- `LOCAL_ASR_CPU_REQUIRED`
- `LOCAL_ASR_PROPOSAL_INVALID`
- `LOCAL_ASR_CANCELLED` / `LOCAL_ASR_TRANSCRIPTION_TIMEOUT`
- `LOCAL_ASR_TRANSCRIPTION_FAILED`

## Qualification still required

Before any integration or activation, run the root-owned isolated environment
with network externally denied and no Hugging Face tokens or caches. Record:

1. the hash-locked dependency receipt, `pip check`, audit, model hashes, and
   license/notices review;
2. CTranslate2 CPU-supported compute types and actual model device/compute type;
3. exact worker binding, stdout/stderr absence, and absence of network activity;
4. wall time, real-time factor, peak working set, CPU utilization, and disk use
   for licensed 30-second and five-minute English fixtures;
5. word error rate and timestamp error against human references, plus silence,
   noise, prompt-in-audio, malformed WAV, cancellation, and repeat-run tests.

Passing structural tests is not evidence of transcription accuracy, useful
quality, perceptual fitness, deterministic output, or production readiness.
