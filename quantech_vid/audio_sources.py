"""Bounded PCM source inspection; no import, model invocation or project mutation."""
from __future__ import annotations

import hashlib
import io
import math
import struct
import wave
from array import array
from dataclasses import dataclass

from PIL import Image, ImageDraw

MAX_SOURCE_BYTES = 50_000_000
MAX_AUDIO_SECONDS = 300
ASR_SAMPLE_RATE = 16_000
WAVEFORM_SIZE = (1280, 320)


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
    exact_zero_energy: bool


def inspect_pcm_source(payload: bytes) -> PCMSource:
    """Accept complete uncompressed PCM16 WAV only. Errors contain no source data."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SOURCE_BYTES:
        raise AudioSourceError('AUDIO_SOURCE_SIZE_REJECTED')
    if len(payload) < 44 or payload[:4] != b'RIFF' or payload[8:12] != b'WAVE':
        raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED')
    if int.from_bytes(payload[4:8], 'little') + 8 != len(payload):
        raise AudioSourceError('AUDIO_SOURCE_LENGTH_MISMATCH')
    pcm = b''
    try:
        with wave.open(io.BytesIO(payload), 'rb') as audio:
            channels, width, rate, frames, compression, _ = audio.getparams()
            if channels not in {1, 2} or width != 2 or not 8000 <= rate <= 96000 or compression != 'NONE':
                raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED')
            duration = frames / rate
            if frames < 1 or not math.isfinite(duration) or duration > MAX_AUDIO_SECONDS:
                raise AudioSourceError('AUDIO_SOURCE_DURATION_REJECTED')
            expected = frames * channels * width
            pcm = audio.readframes(frames + 1)
            if expected > MAX_SOURCE_BYTES or len(pcm) != expected:
                raise AudioSourceError('AUDIO_SOURCE_LENGTH_MISMATCH')
    except AudioSourceError:
        raise
    except (wave.Error, EOFError, ValueError, OSError, OverflowError) as exc:
        raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED') from exc
    return PCMSource(hashlib.sha256(payload).hexdigest(), len(payload), frames, rate,
                     channels, width, duration, not any(pcm))


def inspect_asr_pcm_source(payload: bytes) -> PCMSource:
    """Accept only the canonical 44-byte PCM16 mono/16 kHz pilot profile."""
    source = inspect_pcm_source(payload)
    try:
        if len(payload) < 44 or payload[12:16] != b'fmt ' or payload[36:40] != b'data':
            raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED')
        fmt_size, audio_format, channels, rate, byte_rate, block_align, bits = struct.unpack_from(
            '<IHHIIHH', payload, 16)
        data_size = struct.unpack_from('<I', payload, 40)[0]
    except (struct.error, TypeError) as exc:
        raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED') from exc
    if (fmt_size != 16 or audio_format != 1 or channels != 1 or rate != ASR_SAMPLE_RATE
            or byte_rate != ASR_SAMPLE_RATE * 2 or block_align != 2 or bits != 16
            or data_size != len(payload) - 44 or source.channels != 1
            or source.sample_width != 2 or source.sample_rate != ASR_SAMPLE_RATE):
        raise AudioSourceError('AUDIO_SOURCE_FORMAT_REJECTED')
    return source


def waveform_preview_png(payload: bytes) -> bytes:
    """Return a deterministic, metadata-free raster preview of qualified PCM."""
    source = inspect_asr_pcm_source(payload)
    samples = array('h')
    samples.frombytes(payload[44:])
    if struct.pack('=H', 1) != struct.pack('<H', 1):
        samples.byteswap()
    if len(samples) != source.frames:
        raise AudioSourceError('AUDIO_SOURCE_LENGTH_MISMATCH')
    width, height = WAVEFORM_SIZE
    center = height // 2
    canvas = Image.new('RGB', WAVEFORM_SIZE, '#0b3438')
    draw = ImageDraw.Draw(canvas)
    draw.line((0, center, width - 1, center), fill='#5f888b', width=1)
    for x in range(width):
        start = x * len(samples) // width
        if start >= len(samples):
            break
        end = max(start + 1, (x + 1) * len(samples) // width)
        block = samples[start:min(end, len(samples))]
        low, high = min(block), max(block)
        y_high = center - round(high * (center - 12) / 32768)
        y_low = center - round(low * (center - 12) / 32768)
        draw.line((x, y_high, x, y_low), fill='#f4d35e', width=1)
    output = io.BytesIO()
    canvas.save(output, format='PNG', optimize=False)
    return output.getvalue()
