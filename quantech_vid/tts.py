from __future__ import annotations

import hashlib
import json
import os
import wave
from pathlib import Path

import imageio_ffmpeg

from .config import Settings
from .process import run_command


def narration_key(text: str, voice: str, model: str) -> str:
    payload = json.dumps({"text": text, "voice": voice, "model": model}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def silent_wav(path: Path, duration: float, sample_rate: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(duration * sample_rate)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frames)
    return path


def normalize_audio(source: Path, target: Path, duration: float) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-af", "apad", "-t", f"{duration:.3f}",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(target),
    ]
    run_command(command, timeout=max(30.0, min(180.0, duration * 3.0)))
    return target


def synthesize(
    settings: Settings,
    text: str,
    locale: str,
    duration: float,
    voice: str | None = None,
    model: str | None = None,
    force: bool = False,
) -> tuple[Path, bool, str]:
    selected_voice = voice or (settings.tts_voice_fr if locale == "fr" else settings.tts_voice_en)
    selected_model = model or settings.tts_model
    key = narration_key(text, selected_voice, selected_model)
    cached = settings.data_dir / "tts-cache" / f"{key}.wav"
    if cached.exists() and not force:
        return cached, True, key
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for narrated renders")

    from openai import OpenAI

    raw = settings.data_dir / "tts-cache" / f"{key}.mp3"
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    with client.audio.speech.with_streaming_response.create(
        model=selected_model,
        voice=selected_voice,
        input=text,
        response_format="mp3",
    ) as response:
        response.stream_to_file(raw)
    normalize_audio(raw, cached, duration)
    raw.unlink(missing_ok=True)
    return cached, False, key
