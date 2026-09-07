# Original audio preview boundary

The studio can play an admitted WAV original only after a human operator explicitly
selects **Load original audio for review**. This surface exists to compare a local
ASR proposal with its source. It does not start transcription, narration, mixing,
rendering, or upload, and it never autoplays.

## HTTP contract

`GET /api/v2/sources/{source_id}/audio-preview` requires an authenticated human
session. Agent credentials are refused. Source ownership and
the source's `analyze` right are enforced by `audio_original_for_analysis` before
the original is read.

The source identifier must be the opaque `src_` identifier. On success the server
returns the verified bytes already held in memory with:

- `Content-Type: audio/wav`
- exact `Content-Length`
- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`

No filesystem path, signed URL, bearer token in a URL, or original filename is
returned. The server verifies the admitted derivative/original linkage, resolves
the original under the private `source-originals` root, rejects symlinks, rereads
at most 9,600,044 bytes, parses the complete canonical waveform, and compares its
byte length and SHA-256 with the stored original receipt before sending bytes.

Only a canonical 44-byte-header RIFF/WAVE file containing PCM signed 16-bit,
mono, 16 kHz audio is eligible. The bound is 300 seconds (9,600,044 bytes including
the header). Common filtered failures include `SOURCE_NOT_FOUND`,
`SOURCE_OPERATION_FORBIDDEN`, `SOURCE_ORIGINAL_INTEGRITY_FAILED`,
`AUDIO_PREVIEW_TOO_LARGE`, and `AUDIO_PREVIEW_INTEGRITY_FAILED`.

## Browser handling

The client validates the source metadata before requesting the endpoint, streams
the response under the same size bound, and verifies response media type, length,
and SHA-256. The route name is part of the small in-memory preview cache key, so a
raster derivative can never satisfy an audio request.

Playback uses a transient Blob URL and an HTML audio element with controls and
`preload="none"`. The URL is revoked when the operator closes the player, decoding
fails, the component unmounts, or the selected source changes. Responses arriving
after close/unmount are ignored. No preview is persisted by the studio.

## Limits

Playback is a human review aid, not evidence that speech is accurate, that a
speaker has a particular identity, or that the source has a particular licence or
provenance. The server does not repair, resample, trim, denoise, or transcribe the
audio through this endpoint.

Targeted coverage is in `tests/test_audio_preview.py` and
`apps/studio/tests/audio-preview.test.tsx`.
