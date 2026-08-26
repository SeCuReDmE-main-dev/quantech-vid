from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .schemas import CaptureRequest


async def capture_site(settings: Settings, request: CaptureRequest) -> dict:
    parsed = urlparse(str(request.url))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS capture targets are supported")

    from playwright.async_api import async_playwright

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_dir = settings.data_dir / "captures" / f"{parsed.hostname}-{stamp}"
    video_dir = target_dir / "video"
    target_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context_args = {"viewport": {"width": request.width, "height": request.height}}
        if request.record_seconds:
            video_dir.mkdir(parents=True, exist_ok=True)
            context_args["record_video_dir"] = str(video_dir)
            context_args["record_video_size"] = {"width": request.width, "height": request.height}
        context = await browser.new_context(**context_args)
        page = await context.new_page()
        await page.goto(str(request.url), wait_until="networkidle", timeout=60_000)
        screenshot = target_dir / "page.png"
        await page.screenshot(path=str(screenshot), full_page=request.full_page)
        if request.record_seconds:
            await asyncio.sleep(request.record_seconds)
        video = page.video
        await context.close()
        video_path = await video.path() if video else None
        await browser.close()
    return {"screenshot": str(screenshot), "video": str(video_path) if video_path else None}
