from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import shutil
import time
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import Settings
from .local_voice import (LocalVoiceError, LocalVoicePilot, MAX_AUDIO_SECONDS,
                          MAX_TEXT_CHARS, MODEL_FILENAME, PILOT_LANGUAGE,
                          PILOT_VOICE, SAMPLE_RATE)
from .process import run_command
from .scene3d import (SCENE3D_DEADLINE_SECONDS, Scene3DError, bundle_identity,
                      capture_scene_frames, validate_project_bounds)
from .schemas import ProjectManifest, Scene
from .subtitles import write_subtitles
from .tts import normalize_audio, silent_wav, synthesize


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
            chunk = ""
            for character in word:
                candidate = chunk + character
                if not chunk or draw.textbbox((0, 0), candidate, font=font)[2] <= width:
                    chunk = candidate
                else:
                    lines.append(chunk)
                    chunk = character
            current = chunk
    if current:
        lines.append(current)
    return lines


def _bounded_notice_lines(draw: ImageDraw.ImageDraw, text: str, width: int,
                          preferred_size: int, max_lines: int = 6) -> tuple[ImageFont.ImageFont, list[str]]:
    for size in range(preferred_size, 9, -1):
        font = _font(size, bold=True)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines:
            return font, lines
    font = _font(10, bold=True)
    lines = _wrap(draw, text, font, width)
    return font, lines[:max_lines]


def compose_frame(asset: Path, scene: Scene, width: int, height: int, locale: str,
                  disclosure: str = "") -> Image.Image:
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
    if scene.notice:
        banner_margin = max(12, int(width * 0.035))
        banner_font, notice_lines = _bounded_notice_lines(
            overlay_draw, scene.notice, width - 2 * banner_margin,
            preferred_size=max(12, min(24, int(width * 0.018))), max_lines=6,
        )
        line_height = max(12, int(banner_font.size * 1.18))
        banner_height = min(panel_top - 4, banner_margin * 2 + line_height * (len(notice_lines) + 1))
        overlay_draw.rectangle((0, 0, width, banner_height), fill=(3, 17, 20, 238))
        overlay_draw.text((banner_margin, banner_margin // 2), "QuaNTecH-ViD / SecuredMe",
                          font=banner_font, fill="#68f5d1")
        notice_y = banner_margin // 2 + line_height
        for line in notice_lines:
            overlay_draw.text((banner_margin, notice_y), line, font=banner_font, fill="#fff2b2")
            notice_y += line_height
    y = panel_top + int(height * 0.035)
    overlay_draw.text((margin, y), f"QuaNTecH-ViD / SecuredMe / {locale.upper()}",
                      font=label_font, fill=accent)
    y += int(label_font.size * 1.9)
    title, body = scene.copy_for(locale)
    for line in _wrap(overlay_draw, title, title_font, width - 2 * margin):
        overlay_draw.text((margin, y), line, font=title_font, fill="white")
        y += int(title_font.size * 1.2)
    y += int(body_font.size * 0.35)
    for line in _wrap(overlay_draw, body, body_font, width - 2 * margin)[:3]:
        overlay_draw.text((margin, y), line, font=body_font, fill="#d7e8e5")
        y += int(body_font.size * 1.35)
    if disclosure:
        disclosure_font = _font(max(10, min(16, int(width * 0.011))))
        disclosure_text = _wrap(overlay_draw, disclosure, disclosure_font,
                                width - 2 * margin)[0]
        disclosure_y = height - disclosure_font.size - 4
        overlay_draw.rectangle((0, disclosure_y - 2, width, height), fill=(3, 17, 20, 238))
        overlay_draw.text((margin, disclosure_y), disclosure_text,
                          font=disclosure_font, fill="#b9cbc8")
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
        # Stay two frames inside the tail; seeking on the container duration can land past
        # the final decodable frame even when the timeline duration is correct.
        tail_guard = max(0.05, 2 / fps)
        sample_times = [0.05, max(0.05, duration / 2), max(0.05, duration - tail_guard)]
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


def _render_sequence_mp4(timeline: Path, narration: Path, target: Path, fps: int,
                         total_duration: float, cancelled: Callable[[], bool]) -> None:
    run_command(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(fps), "-start_number", "0", "-i", timeline / "frame-%06d.png",
            "-i", narration, "-t", f"{total_duration:.6f}", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-shortest", target,
        ],
        timeout=_render_timeout(total_duration), cancelled=cancelled,
    )


def _link_or_copy(source: Path, target: Path) -> int:
    try:
        os.link(source, target)
        return 0
    except OSError:
        shutil.copyfile(source, target)
        return target.stat().st_size


def _remove_work_dirs(output_dir: Path, work_dirs: list[Path]) -> None:
    root = output_dir.resolve()
    for directory in work_dirs:
        resolved = directory.resolve()
        if resolved.parent != root:
            raise Scene3DError("THREED_CLEANUP_BOUNDARY_FAILED")
        if resolved.is_dir():
            shutil.rmtree(resolved)


def _prepare_narration(
    settings: Settings,
    manifest: ProjectManifest,
    track: object,
    locale: str,
    output_dir: Path,
    narration_mode: str,
    *,
    local_voice: LocalVoicePilot | None = None,
    local_voice_binding: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[Path, bool, str, str, str | None]:
    if narration_mode == "silent":
        narration = silent_wav(output_dir / "narration.wav", manifest.duration)
        return narration, False, sha256_file(narration), "local-silence-generator", None
    if narration_mode == "openai":
        narration, cache_hit, narration_hash = synthesize(
            settings, track.narration, locale, manifest.duration, voice=track.voice
        )
        return narration, cache_hit, narration_hash, settings.tts_model, track.voice
    if narration_mode != "local_kokoro_cpu":
        raise ValueError("NARRATION_MODE_UNSUPPORTED")
    if local_voice is None or local_voice_binding is None:
        raise LocalVoiceError("LOCAL_VOICE_UNAVAILABLE")
    if locale != "en" or track.locale != "en":
        raise LocalVoiceError("LOCAL_VOICE_LOCALE_UNSUPPORTED")
    if track.voice not in {None, PILOT_VOICE}:
        raise LocalVoiceError("LOCAL_VOICE_VOICE_NOT_ALLOWED")
    if not track.narration.strip() or len(track.narration) > MAX_TEXT_CHARS:
        raise LocalVoiceError("LOCAL_VOICE_TEXT_INVALID")
    _, current_binding = local_voice.binding()
    if not hmac.compare_digest(current_binding, local_voice_binding):
        raise LocalVoiceError("LOCAL_VOICE_BINDING_MISMATCH")
    result = local_voice.synthesize(
        track.narration, output_dir, timeout=180, cancelled=cancelled
    )
    if result.binding_sha256 != local_voice_binding:
        raise LocalVoiceError("LOCAL_VOICE_BINDING_MISMATCH")
    try:
        audio = result.receipt["audio"]
        runtime = result.receipt["runtime"]
        frames = audio["frames"]
        sample_rate = audio["sample_rate"]
        providers = runtime["providers"]
    except (KeyError, TypeError) as exc:
        raise LocalVoiceError("LOCAL_VOICE_AUDIO_INVALID") from exc
    if providers != ["CPUExecutionProvider"]:
        raise LocalVoiceError("LOCAL_VOICE_CPU_REQUIRED")
    if (isinstance(frames, bool) or not isinstance(frames, int) or frames < 1
            or isinstance(sample_rate, bool) or not isinstance(sample_rate, int)
            or sample_rate != SAMPLE_RATE
            or frames > SAMPLE_RATE * MAX_AUDIO_SECONDS):
        raise LocalVoiceError("LOCAL_VOICE_AUDIO_INVALID")
    maximum_frames = math.floor(manifest.duration * sample_rate + 1e-9)
    if frames > maximum_frames:
        raise LocalVoiceError("LOCAL_VOICE_AUDIO_EXCEEDS_TIMELINE")
    narration = normalize_audio(
        result.wav_path, output_dir / "narration.wav", manifest.duration
    )
    return narration, False, sha256_file(narration), MODEL_FILENAME, PILOT_VOICE


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
    local_voice: LocalVoicePilot | None = None,
    local_voice_binding: str | None = None,
) -> list[Path]:
    if narration_mode not in {"silent", "openai", "local_kokoro_cpu"}:
        raise ValueError("NARRATION_MODE_UNSUPPORTED")
    notify = progress or (lambda _: None)
    is_cancelled = cancelled or (lambda: False)
    profile = next(item for item in manifest.profiles if item.name == profile_name)
    track = next(item for item in manifest.locales if item.locale == locale)
    output_dir.mkdir(parents=True, exist_ok=True)
    notify(5)

    narration, cache_hit, narration_hash, narration_model, narration_voice = _prepare_narration(
        settings, manifest, track, locale, output_dir, narration_mode,
        local_voice=local_voice, local_voice_binding=local_voice_binding,
        cancelled=is_cancelled,
    )
    notify(20)

    frame_paths: list[Path] = []
    animated = any(scene.visual_3d is not None for scene in manifest.scenes)
    timeline = output_dir / "timeline"
    work_dirs: list[Path] = []
    frame_bytes = 0
    if animated:
        if not manifest.scene3d_binding or manifest.scene3d_binding != bundle_identity().binding:
            raise Scene3DError("THREED_BUNDLE_MISMATCH")
        if manifest.scene3d_frame_byte_limit is None:
            raise Scene3DError("THREED_FRAME_BYTES_LIMIT_EXCEEDED")
        validate_project_bounds(manifest.scenes, profile.width, profile.height, profile.fps,
                                manifest.scene3d_frame_byte_limit)
        timeline.mkdir(parents=True, exist_ok=True)
        work_dirs.append(timeline)
    scene3d_started = time.monotonic()
    frame_number = 0
    for index, scene in enumerate(manifest.scenes):
        if is_cancelled():
            raise InterruptedError("Render cancelled")
        asset = settings.require_allowed_path(manifest_path.parent / scene.asset)
        if animated and scene.visual_3d is not None:
            raw_dir = output_dir / f"scene3d-{index + 1:02}"
            work_dirs.append(raw_dir)
            remaining = manifest.scene3d_frame_byte_limit - frame_bytes
            try:
                deadline_remaining = SCENE3D_DEADLINE_SECONDS - (time.monotonic() - scene3d_started)
                if deadline_remaining <= 0:
                    raise Scene3DError("THREED_RENDER_DEADLINE_EXCEEDED")
                raw_paths, _ = capture_scene_frames(
                    visual=scene.visual_3d, title=scene.copy_for(locale)[0], duration=scene.duration,
                    width=profile.width, height=profile.height, fps=profile.fps,
                    output_dir=raw_dir, frame_byte_limit=max(1, remaining),
                    cancelled=is_cancelled, deadline_seconds=deadline_remaining,
                )
                raw_remaining = sum(path.stat().st_size for path in raw_paths)
                for raw in raw_paths:
                    target = timeline / f"frame-{frame_number:06d}.png"
                    with compose_frame(raw, scene, profile.width, profile.height, locale,
                                       manifest.disclosure) as composed:
                        with io.BytesIO() as encoded:
                            composed.save(encoded, format="PNG", optimize=True)
                            payload = encoded.getvalue()
                    raw_size = raw.stat().st_size
                    projected = frame_bytes + (raw_remaining - raw_size) + len(payload)
                    if projected > manifest.scene3d_frame_byte_limit:
                        raise Scene3DError("THREED_FRAME_BYTES_LIMIT_EXCEEDED")
                    target.write_bytes(payload)
                    frame_bytes += len(payload)
                    raw_remaining -= raw_size
                    raw.unlink(missing_ok=True)
                    frame_number += 1
            except BaseException:
                _remove_work_dirs(output_dir, work_dirs)
                raise
            raw_dir.rmdir()
            work_dirs.remove(raw_dir)
        else:
            frame = compose_frame(asset, scene, profile.width, profile.height, locale,
                                  manifest.disclosure)
            frame_path = output_dir / f"scene-{index + 1:02}.png"
            frame.save(frame_path, quality=95)
            frame.close()
            frame_paths.append(frame_path)
            if animated:
                frame_bytes += frame_path.stat().st_size
                if frame_bytes > manifest.scene3d_frame_byte_limit:
                    _remove_work_dirs(output_dir, work_dirs)
                    raise Scene3DError("THREED_FRAME_BYTES_LIMIT_EXCEEDED")
                try:
                    for _ in range(max(1, int(scene.duration * profile.fps + 0.999999))):
                        frame_bytes += _link_or_copy(frame_path, timeline / f"frame-{frame_number:06d}.png")
                        if frame_bytes > manifest.scene3d_frame_byte_limit:
                            raise Scene3DError("THREED_FRAME_BYTES_LIMIT_EXCEEDED")
                        frame_number += 1
                except BaseException:
                    _remove_work_dirs(output_dir, work_dirs)
                    raise
        notify(20 + int(35 * (index + 1) / len(manifest.scenes)))

    basename = f"{manifest.slug}-{locale}-{profile.name}"
    mp4 = output_dir / f"{basename}.mp4"
    webm = output_dir / f"{basename}.webm"
    try:
        if animated:
            _render_sequence_mp4(timeline, narration, mp4, profile.fps, manifest.duration,
                                 is_cancelled)
        else:
            _render_mp4(
                frame_paths, [scene.duration for scene in manifest.scenes], narration, mp4,
                profile.fps, manifest.duration, is_cancelled,
            )
        notify(75)
        _render_webm(mp4, webm, manifest.duration, is_cancelled)
    except BaseException:
        mp4.unlink(missing_ok=True)
        webm.unlink(missing_ok=True)
        if animated:
            _remove_work_dirs(output_dir, work_dirs)
        raise
    notify(88)

    srt = output_dir / f"{basename}.srt"
    vtt = output_dir / f"{basename}.vtt"
    write_subtitles(track.narration, manifest.duration, srt, vtt, track.segments)
    poster = output_dir / f"{basename}-poster.png"
    poster_source = timeline / "frame-000000.png" if animated else frame_paths[0]
    with Image.open(poster_source) as first_frame:
        first_frame.save(poster)
    if animated:
        _remove_work_dirs(output_dir, work_dirs)
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
        "narration": {"mode": narration_mode, "model": narration_model, "voice": narration_voice, "hash": narration_hash, "cache_hit": cache_hit},
        "disclosure": manifest.disclosure,
    }
    if narration_mode == "local_kokoro_cpu":
        provenance["narration"]["resource_binding"] = local_voice_binding
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    notify(100)
    return [mp4, webm, srt, vtt, poster, provenance_path, qa_path]
