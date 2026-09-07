# Optional local voice provenance

This is a separately installed CPU pilot, not an enabled studio feature or bundled redistribution. No voice model, user recording, credential, or runtime is committed here. Silent production remains the default until the signed-plan integration is qualified.

## Fixed upstream artifacts

Source: https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1

GitHub release API ID203169879 reports the following SHA-256 digests. Local downloads from that exact release matched both size and digest on2026-09-07UTC.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| kokoro-v1.0.int8.onnx | 114119327 | ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c |
| voices-v1.0.bin | 28214398 | bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d |
| kokoro-v1.0.onnx (FP32 pilot replacement) | 325505369 | beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a |

The initial INT8 pilot failed during CPU session creation: ONNX Runtime 1.20.1 reported an unsupported `ConvInteger(10)` operation. The operator therefore selected the fixed official FP32 model for the next qualification attempt. This is an explicit pilot configuration change, not a runtime fallback. The INT8 artifact remains preserved as evidence and is not described as operational.

The `.pth` checkpoint is not used. Voice arrays must be inspected without pickle and within explicit archive/shape/size bounds.

## Licenses and scope

- Kokoro-ONNX0.6.1 code: MIT, copyright2025 github.com/thewh1teagle; installed wheel contains LICENSE. https://github.com/thewh1teagle/kokoro-onnx
- Kokoro model: upstream declares Apache-2.0. https://huggingface.co/hexgrad/Kokoro-82M
- The initial fixture uses stock `af_heart`, not a personal recording or identity claim. Upstream voice listing: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
- French `ff_siwis` carries a SIWIS attribution requirement; it is not enabled by this English-only pilot. Resolve and include that attribution before enabling it.
- Phonemizer declares GPLv3-or-later, and eSpeak-NG has separate copyleft licensing. Installing a separate local runtime does not make its components MIT. Bundled installers and redistribution require their own license/source/notice review; this pilot does not claim that review complete.

No per-person publicity or impersonation rights are asserted. The product must not offer cloning or impersonation through this stock-voice pilot.

## Dependency proof

`requirements-kokoro-cpu-py313.txt` pins the18 exact Windows x64 CPython3.13 wheels, including transitive dependencies. It is independent of the main application manifests. Isolated installation and `pip check` passed; `pip-audit` found no known vulnerabilities at the dated check. That is a snapshot, not a security guarantee. No GPU extra is installed.

The initial INT8 model and voice files total 142,333,725 bytes. The initial isolated environment plus those models measured 346,538,614 bytes before speech generation. The FP32 replacement adds 325,505,369 bytes while the rejected INT8 file is retained. Actual CPU provider, synthesis duration, memory use and output verification require a separate real inference receipt; installation alone is not that proof.

## First real FP32 proof

On 2026-09-07 UTC the isolated worker produced a stock English synthetic narration with exactly `CPUExecutionProvider`. The verified WAV is mono PCM16, 24,000 Hz, 256,976 frames, 513,996 bytes (10.707 seconds). SHA-256: `7a5fcebc196e478e9433b655e4434a05b8f3672c2fce5470256e5f50616b43b0`.

Wall time was 62.3 seconds including pre/post integrity checks; that is not a pure inference benchmark. The resource binding was `d35b76d1b6158b84d1b90180e8e45f13d4da6f77434fdb6992a629285a09a2f0`. Peak process memory and perceptual pronunciation quality were not measured in this run. This proves the isolated pilot, not signed studio integration, an OS network sandbox, French qualification or a redistributable installer.
