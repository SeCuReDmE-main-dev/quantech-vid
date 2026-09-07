from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


MAX_SCENE3D_FRAMES = 1_800
MAX_SCENE3D_FRAME_BYTES = 1_000_000_000
SCENE3D_DEADLINE_SECONDS = 180.0


class Scene3DError(RuntimeError):
    pass


class Scene3DUnavailable(Scene3DError):
    pass


class Visual3DConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["title", "diagram", "annotated-object", "comparison", "code", "presentation", "synthetic-avatar"]
    lines: list[str] = Field(default_factory=list, max_length=8)
    accent: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    animation: Literal["none", "spin", "pulse"]

    def model_post_init(self, __context: object) -> None:
        if any(not line.strip() or len(line) > 200 for line in self.lines):
            raise ValueError("3D lines must contain 1 to 200 characters")


@dataclass(frozen=True)
class Scene3DBundle:
    path: Path
    version: str
    sha256: str

    @property
    def binding(self) -> str:
        return f"three@{self.version}:sha256:{self.sha256}"


def bundle_path() -> Path:
    return Path(__file__).resolve().parent.parent / "packages" / "scene3d" / "dist" / "browser.js"


def bundle_identity() -> Scene3DBundle:
    path = bundle_path()
    package = path.parent.parent / "package.json"
    if not path.is_file() or not package.is_file():
        raise Scene3DUnavailable("THREED_RENDERER_UNAVAILABLE")
    try:
        package_data = json.loads(package.read_text(encoding="utf-8"))
        version = str(package_data["dependencies"]["three"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise Scene3DUnavailable("THREED_RENDERER_UNAVAILABLE") from exc
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return Scene3DBundle(path=path, version=version, sha256=digest)


def configured_browser_path(default_path: str) -> Path:
    configured = os.environ.get("QUANTECH_SCENE3D_CHROMIUM", default_path)
    path = Path(configured)
    if not path.is_absolute() or not path.is_file():
        raise Scene3DUnavailable("THREED_RENDERER_UNAVAILABLE")
    return path


def runtime_binding() -> str:
    identity = bundle_identity()
    try:
        from playwright.sync_api import sync_playwright
        playwright = sync_playwright().start()
        try:
            executable = configured_browser_path(playwright.chromium.executable_path)
            browser = playwright.chromium.launch(
                headless=True, executable_path=str(executable),
                args=["--disable-background-networking", "--disable-component-update",
                      "--disable-default-apps", "--no-first-run", "--no-default-browser-check"],
            )
            browser.close()
        finally:
            playwright.stop()
    except Scene3DUnavailable:
        raise
    except Exception as exc:
        raise Scene3DUnavailable("THREED_RENDERER_UNAVAILABLE") from exc
    return identity.binding


def validate_project_bounds(scenes: list[object], width: int, height: int, fps: int,
                            frame_byte_limit: int) -> int:
    if not (320 <= width <= 1920 and 320 <= height <= 1920 and width * height <= 2_073_600):
        raise Scene3DError("THREED_PROFILE_LIMIT_EXCEEDED")
    frame_count = sum(math.ceil(scene.duration * fps) for scene in scenes
                      if getattr(scene, "visual_3d", None) is not None)
    if frame_count > MAX_SCENE3D_FRAMES:
        raise Scene3DError("THREED_FRAME_LIMIT_EXCEEDED")
    if not (1 <= frame_byte_limit <= MAX_SCENE3D_FRAME_BYTES):
        raise Scene3DError("THREED_FRAME_BYTES_LIMIT_EXCEEDED")
    return frame_count


def capture_scene_frames(*, visual: Visual3DConfig, title: str, duration: float, width: int,
                         height: int, fps: int, output_dir: Path, frame_byte_limit: int,
                         cancelled: Callable[[], bool], browser_executable: Path | None = None,
                         deadline_seconds: float = SCENE3D_DEADLINE_SECONDS,
                         _frame_times: list[float] | None = None) -> tuple[list[Path], list[str]]:
    if cancelled():
        raise InterruptedError("Render cancelled")
    identity = bundle_identity()
    frame_times = (_frame_times if _frame_times is not None
                   else [index / fps for index in range(math.ceil(duration * fps))])
    if any(not isinstance(value, (int, float)) or not math.isfinite(value)
           or value < 0 or value > duration for value in frame_times):
        raise Scene3DError("THREED_FRAME_TIME_INVALID")
    frame_count = len(frame_times)
    if frame_count < 1 or frame_count > MAX_SCENE3D_FRAMES:
        raise Scene3DError("THREED_FRAME_LIMIT_EXCEEDED")
    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    network_attempts: list[str] = []
    started = time.monotonic()
    playwright = browser = context = page = None
    succeeded = False
    try:
        from playwright.sync_api import sync_playwright
        playwright = sync_playwright().start()
        executable = browser_executable or configured_browser_path(playwright.chromium.executable_path)
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(executable),
            args=["--disable-background-networking", "--disable-component-update",
                  "--disable-default-apps", "--no-first-run", "--no-default-browser-check"],
        )
        context = browser.new_context(viewport={"width": width, "height": height},
                                      service_workers="block", java_script_enabled=True)
        page = context.new_page()
        page.set_default_timeout(5_000)

        def block_network(route) -> None:
            network_attempts.append(route.request.url)
            route.abort()

        page.route("**/*", block_network)
        page.set_content("<canvas id='scene' aria-label='Synthetic representation'></canvas>")
        page.add_script_tag(path=str(identity.path))
        config = {**visual.model_dump(mode="json"), "title": title, "duration": duration}
        page.evaluate(
            """({config,width,height}) => {
              const canvas = document.getElementById('scene');
              window.__quantechScene = QuaNTechScene3D.createSceneRenderer(
                canvas, config, {width, height});
            }""",
            {"config": config, "width": width, "height": height},
        )
        canvas = page.locator("#scene")
        total_bytes = 0
        for index in range(frame_count):
            if cancelled():
                raise InterruptedError("Render cancelled")
            if time.monotonic() - started > deadline_seconds:
                raise Scene3DError("THREED_RENDER_DEADLINE_EXCEEDED")
            seconds = frame_times[index]
            page.evaluate("seconds => window.__quantechScene.renderAt(seconds)", seconds)
            target = output_dir / f"raw-{index:06d}.png"
            canvas.screenshot(path=str(target), type="png")
            created.append(target)
            total_bytes += target.stat().st_size
            if total_bytes > frame_byte_limit:
                raise Scene3DError("THREED_FRAME_BYTES_LIMIT_EXCEEDED")
        page.evaluate("() => window.__quantechScene.dispose()")
        succeeded = True
        return created, network_attempts
    except Scene3DUnavailable:
        raise
    except InterruptedError:
        raise
    except Scene3DError:
        raise
    except Exception as exc:
        raise Scene3DUnavailable("THREED_RENDERER_UNAVAILABLE") from exc
    finally:
        for resource in (page, context, browser):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        if not succeeded:
            for path in created:
                path.unlink(missing_ok=True)
