# Optional isolated English transcription pilot

Acquisition checked 2026-09-07 UTC. The proposed pilot is not an activated studio transcription feature. No model or runtime is bundled in Git, and no user media was sent to a provider.

## Immutable upstream model

Source: https://huggingface.co/Systran/faster-whisper-tiny.en/tree/7d45cf02c1ed72d240c0dbf99d544d19bef1b5a3

The model card declares MIT and describes a CTranslate2 conversion of OpenAI Whisper tiny.en. Stored weights are FP16; the pilot explicitly requests CPU `int8_float32`. The first `int8` alias request was refused because the runtime reported `int8_float32`; the contract was made explicit rather than relaxed. This is not a `.pt` or `.pth` checkpoint and no pickle loader is used. The card's model-name download example is not used in the worker.

Each downloaded file's length was checked against the official tree API. The binary matched its published LFS SHA-256. For ordinary files, the Git blob hash (including its blob header) matched the immutable tree object before calculating the SHA-256 below.

| File | Bytes | SHA-256 |
|---|---:|---|
| model.bin | 75537502 | 1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a |
| config.json | 2317 | 14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5 |
| tokenizer.json | 2128466 | 929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df |
| vocabulary.txt | 422309 | ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf |

## Runtime

`requirements-asr-cpu-py313.txt` fixes all 27 binary Windows x64/CPython 3.13 dependencies and wheel hashes, resolved from PyPI. Installation in a new isolated environment using `--only-binary=:all: --require-hashes` passed, as did `pip check` and a dated vulnerability audit. No main application dependency was replaced. Version-specific implementation: https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1

Complete local resources and a local-only model path are mandatory: absent tokenizer files must fail before a pretrained lookup. Runtime settings disable hub downloads and telemetry; environment flags alone are not an OS network sandbox. Binary dependencies including PyAV/FFmpeg have separate licensing obligations; no blanket MIT or redistributable-installer claim is made.

The initial CTranslate2 4.6.0 candidate installed but failed import because it required removed `pkg_resources`. The operator selected the official CPython3.13 Windows wheel for 4.8.2, which removes that dependency, instead of weakening or patching installed library code. Its PyPI SHA-256 is locked. Upstream change history: https://github.com/OpenNMT/CTranslate2/releases . The first resolution report is retained privately as a failed-candidate record; installation health alone was not counted as runtime success.

## Observed synthetic pilot — 2026-09-07 UTC

CPU inference on the studio's authorized stock synthetic voice completed on 30-second and five-minute PCM fixtures: respectively 2/20 segments and 30/300 words, with zero normalized word edits against the repeated synthetic script. Wall times were 62.517 and 70.145 seconds including resource integrity checks. This is not a human-reference accuracy benchmark or perceptual qualification.

The negative fixtures expose a release limitation: 30 seconds of digital silence produced the hallucinated word “you”; low-amplitude synthetic noise produced `LOCAL_ASR_PROPOSAL_INVALID` and no accepted result. None of these outputs was inserted into a project or captions. No confidence threshold was relaxed to turn this into a passing quality result. This pilot remains absent from the production API/UI pending a safe review workflow and further qualification.

## Qualification still required

Installation and file hashes do not prove transcription quality. Human-reference word and timestamp errors, broader silence/noise behavior, operating-system network isolation, CPU utilization, cancellation and the proposal review workflow remain open. Machine text is an untrusted proposal, not a factual certification, speaker identity or automatically accepted caption.
