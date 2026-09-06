from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import Settings
from .process import run_command
from .schemas import ProjectManifest, Scene
from .subtitles import write_subtitles
from .tts import silent_wav, synthesize


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _font_candidates(bold: bool) -> list[Path]:
    env_name = "QUANTECH_VID_FONT_BOLD" if bold else "QUANTECH_VID_FONT_REGULAR"
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    windows_name = "seguisb.ttf" if bold else "segoeui.ttf"
    liberation_name = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    candidates = []
    if os.getenv(env_name):
        candidates.append(Path(os.environ[env_name]))
    candidates.extend(
        [
            Path(__file__).parent / "assets" / "fonts" / filename,
            Path("C:/Windows/Fonts") / windows_name,
            Path("/usr/share/fonts/truetype/dejavu") / filename,
            Path("/usr/share/fonts/truetype/liberation2") / liberation_name,
        ]
    )
    return candidates


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _font_candidates(bold):
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


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
    with Image.open(asset) as opened:
        source = opened.convert("RGB")
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
    result = run_command(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path],
        timeout=20,
        check=False,
    )
    return result.stderr.decode("utf-8", errors="replace")


def _duration_from_probe(probe: str) -> float:
    match = re.search(r"duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe, re.IGNORECASE)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _sample_brightness(path: Path, second: float) -> float:
    result = run_command(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            "-ss", f"{second:.3f}", "-i", path, "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ],
        timeout=20,
    )
    return sum(result.stdout) / len(result.stdout) if result.stdout else 0.0


def _duration_check(duration: float, expected_duration: float | None, fps: int) -> tuple[bool, float, float | None]:
    tolerance = max(2 / fps, 0.05)
    delta = None if expected_duration is None else abs(duration - expected_duration)
    passed = duration > 0 and (delta is None or delta <= tolerance)
    return passed, tolerance, delta


def verify_media(
    path: Path, width: int, height: int, fps: int, expected_duration: float | None = None
) -> dict:
    probe = _probe(path).lower()
    duration = _duration_from_probe(probe)
    duration_passed, duration_tolerance, duration_delta = _duration_check(duration, expected_duration, fps)
    checks = {
        "exists": path.exists() and path.stat().st_size > 1024,
        "h264": "video: h264" in probe,
        "aac": "audio: aac" in probe,
        "yuv420p": "yuv420p" in probe,
        "dimensions": f"{width}x{height}" in probe,
        "fps": f"{fps} fps" in probe,
        "duration": duration_passed,
        "duration_observed": duration,
        "duration_expected": expected_duration,
        "duration_tolerance": duration_tolerance,
        "duration_delta": duration_delta,
        "audio": "audio: aac" in probe,
    }
    try:
        sample_times = [0.05, max(0.05, duration / 2), max(0.05, duration - 0.05)]
        brightness = [_sample_brightness(path, second) for second in sample_times]
        checks["not_black"] = min(brightness) > 4.0
        checks["sample_brightness"] = brightness
    except Exception as exc:
        checks["not_black"] = False
        checks["sample_error"] = str(exc)
    required = ("exists", "h264", "aac", "yuv420p", "dimensions", "fps", "duration", "audio", "not_black")
    checks["passed"] = all(bool(checks[name]) for name in required)
    return checks


def verify_webm(path: Path, width: int, height: int, fps: int, expected_duration: float) -> dict:
    probe = _probe(path).lower()
    duration = _duration_from_probe(probe)
    duration_passed, duration_tolerance, duration_delta = _duration_check(duration, expected_duration, fps)
    checks = {
        "exists": path.exists() and path.stat().st_size > 1024,
        "vp9": "video: vp9" in probe,
        "vorbis": "audio: vorbis" in probe,
        "yuv420p": "yuv420p" in probe,
        "dimensions": f"{width}x{height}" in probe,
        "fps": f"{fps} fps" in probe,
        "duration": duration_passed,
        "duration_observed": duration,
        "duration_expected": expected_duration,
        "duration_tolerance": duration_tolerance,
        "duration_delta": duration_delta,
    }
    try:
        checks["sample_brightness"] = [_sample_brightness(path, max(0.05, duration / 2))]
        checks["not_black"] = checks["sample_brightness"][0] > 4.0
    except Exception as exc:
        checks["not_black"] = False
        checks["sample_error"] = str(exc)
    required = ("exists", "vp9", "vorbis", "yuv420p", "dimensions", "fps", "duration", "not_black")
    checks["passed"] = all(bool(checks[name]) for name in required)
    return checks


def _render_timeout(duration: float) -> float:
    return max(30.0, min(600.0, duration * 8.0))


def _render_mp4(
    frame_paths: list[Path], durations: list[float], narration: Path, target: Path,
    fps: int, total_duration: float, cancelled: Callable[[], bool],
) -> None:
    command: list[str | os.PathLike[str]] = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
    ]
    for frame, duration in zip(frame_paths, durations, strict=True):
        command.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.6f}", "-i", frame])
    command.extend(["-i", narration])
    filters = [f"[{index}:v]fps={fps},format=yuv420p,setsar=1[v{index}]" for index in range(len(frame_paths))]
    streams = "".join(f"[v{index}]" for index in range(len(frame_paths)))
    filters.append(f"{streams}concat=n={len(frame_paths)}:v=1:a=0[video]")
    command.extend(
        [
            "-filter_complex", ";".join(filters), "-map", "[video]", "-map", f"{len(frame_paths)}:a:0",
            "-t", f"{total_duration:.6f}", "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest", target,
        ]
    )
    run_command(command, timeout=_render_timeout(total_duration), cancelled=cancelled)


def _render_webm(source: Path, target: Path, duration: float, cancelled: Callable[[], bool]) -> None:
    run_command(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", source, "-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "2",
            "-c:a", "libvorbis", "-pix_fmt", "yuv420p", target,
        ],
        timeout=_render_timeout(duration),
        cancelled=cancelled,
    )


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

    frame_paths: list[Path] = []
    for index, scene in enumerate(manifest.scenes):
        if is_cancelled():
            raise InterruptedError("Render cancelled")
        asset = settings.require_allowed_path(manifest_path.parent / scene.asset)
        frame = compose_frame(asset, scene, profile.width, profile.height, locale)
        frame_path = output_dir / f"scene-{index + 1:02}.png"
        frame.save(frame_path, quality=95)
        frame_paths.append(frame_path)
        notify(20 + int(35 * (index + 1) / len(manifest.scenes)))

    basename = f"{manifest.slug}-{locale}-{profile.name}"
    mp4 = output_dir / f"{basename}.mp4"
    webm = output_dir / f"{basename}.webm"
    try:
        _render_mp4(
            frame_paths, [scene.duration for scene in manifest.scenes], narration, mp4,
            profile.fps, manifest.duration, is_cancelled,
        )
        notify(75)
        _render_webm(mp4, webm, manifest.duration, is_cancelled)
    except BaseException:
        mp4.unlink(missing_ok=True)
        webm.unlink(missing_ok=True)
        raise
    notify(88)

    srt = output_dir / f"{basename}.srt"
    vtt = output_dir / f"{basename}.vtt"
    write_subtitles(track.narration, manifest.duration, srt, vtt)
    poster = output_dir / f"{basename}-poster.png"
    with Image.open(frame_paths[0]) as first_frame:
        first_frame.save(poster)
    qa_path = output_dir / f"{basename}-qa.json"
    qa = verify_media(mp4, profile.width, profile.height, profile.fps, manifest.duration)
    qa["webm"] = verify_webm(webm, profile.width, profile.height, profile.fps, manifest.duration)
    qa.update({"profile": profile.model_dump()})
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
