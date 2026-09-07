import io
import struct
import wave

import pytest

from PIL import Image

from quantech_vid.audio_sources import (
    AudioSourceError, inspect_asr_pcm_source, inspect_pcm_source,
    waveform_preview_png,
)


def fixture(*, rate=16000, channels=1, width=2, frames=160):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as output:
        output.setnchannels(channels); output.setsampwidth(width); output.setframerate(rate)
        output.writeframes(bytes(frames * channels * width))
    return stream.getvalue()


@pytest.mark.parametrize('channels', [1, 2])
def test_pcm_inspection_is_pure_and_keeps_whole_source_fingerprint(channels):
    payload = fixture(channels=channels)
    result = inspect_pcm_source(payload)
    assert result.frames == 160 and result.duration == .01 and result.channels == channels
    assert result.sample_width == 2 and result.size == len(payload) and len(result.sha256) == 64
    assert payload == fixture(channels=channels)


@pytest.mark.parametrize('payload', [b'not audio', b'', fixture(width=1), fixture(channels=3), fixture(rate=7999), fixture(rate=96001), fixture(frames=0)])
def test_rejects_nonqualified_media(payload):
    with pytest.raises(AudioSourceError):
        inspect_pcm_source(payload)


def test_refuses_truncation_trailing_data_and_oversized_declared_frames():
    payload = fixture()
    for invalid in (payload[:-2], payload + b'private trailing bytes', payload[:40] + struct.pack('<I', 16000 * 2 * 301) + payload[44:]):
        with pytest.raises(AudioSourceError):
            inspect_pcm_source(invalid)


def test_exact_five_minute_limit_and_no_hidden_rights_or_model_action():
    accepted = inspect_pcm_source(fixture(rate=8000, frames=8000 * 300))
    assert accepted.duration == 300
    assert not hasattr(accepted, 'allowed_operations')
    with pytest.raises(AudioSourceError, match='AUDIO_SOURCE_DURATION_REJECTED'):
        inspect_pcm_source(fixture(rate=8000, frames=8000 * 300 + 1))


def test_asr_profile_is_exact_canonical_mono_16khz_and_reports_zero_energy():
    payload = fixture(frames=160)
    source = inspect_asr_pcm_source(payload)
    assert source.exact_zero_energy is True and source.sample_rate == 16000
    nonzero = bytearray(payload); nonzero[44] = 1
    assert inspect_asr_pcm_source(bytes(nonzero)).exact_zero_energy is False
    for invalid in (fixture(rate=8000), fixture(channels=2), payload[:36] + b'JUNK' + payload[40:]):
        with pytest.raises(AudioSourceError, match='AUDIO_SOURCE_FORMAT_REJECTED'):
            inspect_asr_pcm_source(invalid)


def test_waveform_preview_is_deterministic_bounded_and_decodable():
    payload = fixture(frames=3200)
    first = waveform_preview_png(payload)
    assert first == waveform_preview_png(payload) and len(first) < 100_000
    with Image.open(io.BytesIO(first)) as image:
        assert image.format == 'PNG' and image.mode == 'RGB' and image.size == (1280, 320)
