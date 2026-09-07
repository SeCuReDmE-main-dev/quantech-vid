"""Bounded PCM source inspection; no import, model invocation or project mutation."""
from __future__ import annotations

import hashlib
import io
import math
import wave
from dataclasses import dataclass

MAX_SOURCE_BYTES = 50_000_000
MAX_AUDIO_SECONDS = 300


class AudioSourceError(ValueError):
    pass


@dataclass(frozen=True)
class PCMSource:
    sha256: str
    size: int
    frames: int
    sample_rate: int
    channels: int
    sample_width: int
    duration: float


def inspect_pcm_source(payload: bytes) -> PCMSource:
    """Accept complete uncompressed PCM16 WAV only. Errors contain no source data."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SOURCE_BYTES:
        raise AudioSourceError('AUDIO_SOURCE_SIZE_REJECTED')
    if len(payload) < 44 or payload[:4] != b'RIFF' or payload[8:12] != b'WAVE':
        raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED')
    if int.from_bytes(payload[4:8], 'little') + 8 != len(payload):
        raise AudioSourceError('AUDIO_SOURCE_LENGTH_MISMATCH')
    try:
        with wave.open(io.BytesIO(payload), 'rb') as audio:
            channels, width, rate, frames, compression, _ = audio.getparams()
            if channels not in {1, 2} or width != 2 or not 8000 <= rate <= 96000 or compression != 'NONE':
                raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED')
            duration = frames / rate
            if frames < 1 or not math.isfinite(duration) or duration > MAX_AUDIO_SECONDS:
                raise AudioSourceError('AUDIO_SOURCE_DURATION_REJECTED')
            expected = frames * channels * width
            if expected > MAX_SOURCE_BYTES or len(audio.readframes(frames + 1)) != expected:
                raise AudioSourceError('AUDIO_SOURCE_LENGTH_MISMATCH')
    except AudioSourceError:
        raise
    except (wave.Error, EOFError, ValueError, OSError, OverflowError) as exc:
        raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED') from exc
    return PCMSource(hashlib.sha256(payload).hexdigest(), len(payload), frames, rate, channels, width, duration)
