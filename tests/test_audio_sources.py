import io
import struct
import wave

import pytest

from quantech_vid.audio_sources import AudioSourceError, inspect_pcm_source


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
