from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import Settings
from .schemas import ProjectManifest, Scene
from .subtitles import write_subtitles
from .tts import silent_wav, synthesize


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "seguisb.ttf" if bold else "segoeui.ttf"
    path = Path("C:/Windows/Fonts") / name
    return ImageFont.truetype(str(path), size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
        elif current:
            lines.append(current)
            current = word
        else:
            lines.append(word)
            current = ""
    if current:
        lines.append(current)
    return lines


def compose_frame(asset: Path, scene: Scene, width: int, height: int, locale: str) -> Image.Image:
    source = Image.open(asset).convert("RGB")
    if scene.fit == "contain":
        canvas = Image.new("RGB", (width, height), "#06131a")
        fitted = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
        canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    else:
        canvas = ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS)

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    vertical = height > width
    panel_top = int(height * (0.62 if vertical else 0.64))
    overlay_draw.rectangle((0, panel_top, width, height), fill=(3, 17, 20, 220))
    accent = "#68f5d1"
    margin = int(width * (0.07 if vertical else 0.055))
    title_font = _font(max(34, int(width * (0.052 if vertical else 0.027))), bold=True)
    body_font = _font(max(22, int(width * (0.032 if vertical else 0.015))))
    label_font = _font(max(18, int(width * (0.023 if vertical else 0.011))), bold=True)
    y = panel_top + int(height * 0.035)
    overlay_draw.text((margin, y), f"SYNTHIA  /  {locale.upper()}", font=label_font, fill=accent)
    y += int(label_font.size * 1.9)
    title, body = scene.copy_for(locale)
    for line in _wrap(overlay_draw, title, title_font, width - 2 * margin):
        overlay_draw.text((margin, y), line, font=title_font, fill="white")
        y += int(title_font.size * 1.2)
    y += int(body_font.size * 0.35)
    for line in _wrap(overlay_draw, body, body_font, width - 2 * margin)[:3]:
        overlay_draw.text((margin, y), line, font=body_font, fill="#d7e8e5")
        y += int(body_font.size * 1.35)
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def _probe(path: Path) -> str:
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    return result.stderr


def verify_media(path: Path, width: int, height: int, fps: int) -> dict:
    from moviepy import VideoFileClip

    probe = _probe(path).lower()
    checks = {
        "exists": path.exists() and path.stat().st_size > 1024,
        "h264": "video: h264" in probe,
        "aac": "audio: aac" in probe,
        "yuv420p": "yuv420p" in probe,
        "dimensions": f"{width}x{height}" in probe,
        "fps": f"{fps} fps" in probe,
    }
    try:
        with VideoFileClip(str(path)) as clip:
            checks["duration"] = clip.duration > 0
            checks["audio"] = clip.audio is not None and clip.audio.duration > 0
            sample_times = [0.15, max(0.15, clip.duration / 2), max(0.15, clip.duration - 0.2)]
            brightness = []
            for second in sample_times:
                frame = clip.get_frame(min(second, max(0, clip.duration - 0.05)))
                brightness.append(float(np.mean(frame)))
            checks["not_black"] = min(brightness) > 4.0
            checks["sample_brightness"] = brightness
    except Exception as exc:
        checks["duration"] = False
        checks["audio"] = False
        checks["not_black"] = False
        checks["sample_error"] = str(exc)
    checks["passed"] = all(checks.values())
    return checks


def render_project(
    settings: Settings,
    manifest_path: Path,
    manifest: ProjectManifest,
    locale: str,
    profile_name: str,
    output_dir: Path,
    narration_mode: str,
    progress: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[Path]:
    from moviepy import AudioFileClip, ImageClip, concatenate_videoclips

    notify = progress or (lambda _: None)
    is_cancelled = cancelled or (lambda: False)
    profile = next(item for item in manifest.profiles if item.name == profile_name)
    track = next(item for item in manifest.locales if item.locale == locale)
    output_dir.mkdir(parents=True, exist_ok=True)
    notify(5)

    if narration_mode == "silent":
        narration = silent_wav(output_dir / "narration.wav", manifest.duration)
        cache_hit, narration_hash = False, sha256_file(narration)
    else:
        narration, cache_hit, narration_hash = synthesize(
            settings, track.narration, locale, manifest.duration, voice=track.voice
        )
    notify(20)

    clips = []
    frame_paths: list[Path] = []
    for index, scene in enumerate(manifest.scenes):
        if is_cancelled():
            raise InterruptedError("Render cancelled")
        asset = settings.require_allowed_path(manifest_path.parent / scene.asset)
        frame = compose_frame(asset, scene, profile.width, profile.height, locale)
        frame_path = output_dir / f"scene-{index + 1:02}.png"
        frame.save(frame_path, quality=95)
        frame_paths.append(frame_path)
        clips.append(ImageClip(np.array(frame)).with_duration(scene.duration))
        notify(20 + int(35 * (index + 1) / len(manifest.scenes)))

    video = concatenate_videoclips(clips, method="compose")
    audio = AudioFileClip(str(narration))
    final = video.with_audio(audio)
    basename = f"{manifest.slug}-{locale}-{profile.name}"
    mp4 = output_dir / f"{basename}.mp4"
    webm = output_dir / f"{basename}.webm"
    final.write_videofile(
        str(mp4), fps=profile.fps, codec="libx264", audio_codec="aac",
        ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"], logger=None,
    )
    notify(75)
    final.write_videofile(
        str(webm), fps=profile.fps, codec="libvpx-vp9", audio_codec="libvorbis",
        ffmpeg_params=["-pix_fmt", "yuv420p"], logger=None,
    )
    final.close()
    audio.close()
    video.close()
    for clip in clips:
        clip.close()
    notify(88)

    srt = output_dir / f"{basename}.srt"
    vtt = output_dir / f"{basename}.vtt"
    write_subtitles(track.narration, manifest.duration, srt, vtt)
    poster = output_dir / f"{basename}-poster.png"
    Image.open(frame_paths[0]).save(poster)
    qa_path = output_dir / f"{basename}-qa.json"
    qa = verify_media(mp4, profile.width, profile.height, profile.fps)
    qa.update({"duration_expected": manifest.duration, "profile": profile.model_dump()})
    qa_path.write_text(json.dumps(qa, indent=2), encoding="utf-8")

    provenance_path = output_dir / f"{basename}-provenance.json"
    provenance = {
        "schema_version": "1.0", "created_at": datetime.now(timezone.utc).isoformat(),
        "project": manifest.slug, "locale": locale, "profile": profile.model_dump(),
        "source_url": str(manifest.source_url) if manifest.source_url else None,
        "manifest_sha256": sha256_file(manifest_path),
        "assets": [{"path": scene.asset, "sha256": sha256_file(manifest_path.parent / scene.asset)} for scene in manifest.scenes],
        "narration": {"mode": narration_mode, "model": settings.tts_model, "voice": track.voice, "hash": narration_hash, "cache_hit": cache_hit},
        "disclosure": manifest.disclosure,
    }
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    notify(100)
    return [mp4, webm, srt, vtt, poster, provenance_path, qa_path]
