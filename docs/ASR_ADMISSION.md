# Local ASR source admission and proposal boundary

Status: experimental integration with a separate studio proposal/review/apply
flow. No ASR runtime is configured by default. Mocked contract tests are not
evidence of real inference or transcription quality.

## Admitted WAV profile

The browser upload endpoint accepts an explicitly selected `.wav` only when it
is a canonical 44-byte-header RIFF/WAVE file containing uncompressed PCM16,
one channel, 16 kHz, and no more than 300 seconds. The exact uploaded bytes are
retained under the server-owned `source-originals` directory. A deterministic
1280x320 RGB waveform PNG is admitted as the render/preview asset. The two
SHA-256 values are distinct and remain visible in source provenance.

The transformation identifier is `pcm16-waveform-png-v1`. It does not mean
that speech was detected, transcribed, identified, or verified. Admission
records the operator's rights declaration; it does not independently prove a
license or consent.

## Explicit human proposal request

`POST /api/v2/sources/{source_id}/asr-proposals` accepts a closed body containing
the saved project ID and revision, locale `en`, the expected derivative and
original hashes, and `acknowledge_machine_proposal_only: true`.

The route requires the exact loopback Origin, an authenticated human session,
and its CSRF token. Agents and tool transports cannot invoke it. The source must
belong to the saved project and carry `analyze` permission. The server verifies
the original path, descriptor, size, profile, and SHA before compute.

An exact all-zero PCM payload is rejected before runtime discovery because the
qualified silence fixture produced a hallucinated word. This is only an exact
zero-energy check. It is not VAD, speech detection, or evidence that a non-zero
signal contains speech. An audio source longer than the whole project timeline
is also rejected before compute; no audio or segment is silently truncated,
shifted, or compressed.

Only one proposal worker may run at once. The lock is acquired before lazy
runtime discovery and resource binding. The worker has a 180-second deadline,
no automatic retry, and no cloud fallback or download path.

## Response semantics

The response schema is `quantech.asr-proposal.v1`. It binds the saved project
hash and revision, admitted PNG hash, original WAV hash, worker resource binding,
audio duration, and closed source-linked draft segments. It always declares:

```json
{
  "machine_proposal_only": true,
  "human_review_required": true,
  "speaker_identity_inferred": false,
  "vad_performed": false,
  "exact_zero_energy_rejected": true,
  "independent_verification": false
}
```

The endpoint does not write a project revision, transcript, plan, approval, or
job. Proposal text and its interval locator are untrusted machine draft data,
not a source certificate. New transcript provenance sidecars use v2 and describe
operator-authored-or-reviewed content, leaving the upstream method unattested.
They state only that automatic transcription did not occur during export, not
that no machine ever proposed the text. Historical v1 artifacts are unchanged.

The studio requires selection, acknowledgement, proposal, review into a temporary
caption draft, and Apply. Save, planning, approval and rendering remain distinct.
Unmount aborts the client request and ignores late responses; a worker already
started on the server remains bounded by its deadline, not guaranteed cancelled.

## Runtime and limits

`QUANTECH_VID_LOCAL_ASR_ROOT` is optional and absent by default. Health reports
only whether a root was configured; that flag is not proof of valid resources,
quality, network isolation, or successful inference. Discovery remains lazy and
occurs only after source, rights, hash, zero-energy, and timeline checks.

The isolated pilot remains fixed to English tiny.en on CPU with exact
`int8_float32`. The main application imports no faster-whisper dependency and
does not install, download, authenticate, publish, pay, infer speaker identity,
or create a voice profile.
