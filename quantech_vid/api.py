from __future__ import annotations

import mimetypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .capture import capture_site
from .config import Settings
from .db import JobStore
from .renderer import render_project
from .schemas import (
    CaptureRequest,
    NarrationRequest,
    RenderRequest,
    ValidateProjectRequest,
    load_manifest,
)
from .tts import synthesize


settings = Settings.load()
settings.require_loopback()
store = JobStore(settings.data_dir / "quantech-vid.sqlite3")
store.recover_interrupted()
executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="render")

app = FastAPI(title="QuaNTecH-ViD Studio", version="2.0.0")


def _manifest(path_value: str):
    try:
        path = settings.require_allowed_path(path_value)
        if not path.is_file():
            raise ValueError("Manifest does not exist")
        manifest = load_manifest(path)
        for scene in manifest.scenes:
            asset = settings.require_allowed_path(path.parent / scene.asset)
            if not asset.is_file():
                raise ValueError(f"Missing scene asset: {scene.asset}")
        return path, manifest
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _run_render(job_id: str) -> None:
    job = store.get(job_id)
    request = RenderRequest.model_validate(job.request)
    try:
        path, manifest = _manifest(request.manifest_path)
        output = settings.data_dir / "renders" / job_id
        store.update(job_id, status="running", progress=1)
        artifacts = render_project(
            settings, path, manifest, request.locale, request.profile, output,
            request.narration_mode,
            progress=lambda value: store.update(job_id, progress=value),
            cancelled=lambda: store.cancellation_requested(job_id),
        )
        store.update(job_id, status="complete", progress=100, artifacts=[str(item) for item in artifacts])
    except InterruptedError:
        store.update(job_id, status="cancelled", error="Render cancelled")
    except Exception as exc:
        store.update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")


@app.get("/api/v1/health")
def health() -> dict:
    return {
        "status": "ok", "version": app.version, "loopback": True,
        "data_dir": str(settings.data_dir), "allowed_asset_roots": [str(path) for path in settings.allowed_asset_roots],
    }


@app.get("/api/v1/projects")
def projects() -> dict:
    found = []
    for path in (settings.root / "projects").glob("*/project.json"):
        try:
            manifest = load_manifest(path)
            found.append({"path": str(path), "slug": manifest.slug, "title": manifest.title, "duration": manifest.duration})
        except Exception:
            continue
    return {"projects": found}


@app.post("/api/v1/projects/validate")
def validate_project(request: ValidateProjectRequest) -> dict:
    path, manifest = _manifest(request.manifest_path)
    return {
        "valid": True, "path": str(path), "slug": manifest.slug,
        "duration": manifest.duration, "locales": [item.locale for item in manifest.locales],
        "profiles": [item.model_dump() for item in manifest.profiles], "scenes": len(manifest.scenes),
    }


@app.post("/api/v1/captures")
async def captures(request: CaptureRequest) -> dict:
    try:
        return await capture_site(settings, request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Capture failed: {exc}") from exc


@app.post("/api/v1/narrations")
def narrations(request: NarrationRequest) -> dict:
    try:
        path, cache_hit, key = synthesize(
            settings, request.text, request.locale, 60, request.voice, request.model, request.force
        )
        return {"path": str(path), "cache_hit": cache_hit, "key": key}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Narration failed: {exc}") from exc


@app.post("/api/v1/renders", status_code=202)
def create_render(request: RenderRequest) -> dict:
    _, manifest = _manifest(request.manifest_path)
    if request.locale not in {item.locale for item in manifest.locales}:
        raise HTTPException(status_code=422, detail="Locale is not declared by the project")
    if request.profile not in {item.name for item in manifest.profiles}:
        raise HTTPException(status_code=422, detail="Profile is not declared by the project")
    job = store.create(request.model_dump())
    executor.submit(_run_render, job.id)
    return job.model_dump()


@app.get("/api/v1/renders/{job_id}")
def render_status(job_id: str) -> dict:
    try:
        return store.get(job_id).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Render job not found") from exc


@app.post("/api/v1/renders/{job_id}/cancel")
def cancel_render(job_id: str) -> dict:
    try:
        return store.cancel(job_id).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Render job not found") from exc


@app.get("/api/v1/renders/{job_id}/artifacts")
def artifacts(job_id: str) -> dict:
    try:
        job = store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Render job not found") from exc
    return {
        "job_id": job_id,
        "artifacts": [
            {"name": Path(path).name, "size": Path(path).stat().st_size, "download": f"/api/v1/renders/{job_id}/artifacts/{Path(path).name}"}
            for path in job.artifacts if Path(path).is_file()
        ],
    }


@app.get("/api/v1/renders/{job_id}/artifacts/{filename}")
def download_artifact(job_id: str, filename: str):
    try:
        job = store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Render job not found") from exc
    matches = [Path(path) for path in job.artifacts if Path(path).name == filename]
    if not matches or not matches[0].is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(matches[0], media_type=media_type, filename=filename)


@app.post("/api/plugin/generate-token")
def compatibility_token() -> dict:
    return {"token": "loopback-local-operator", "subscriptionLevel": "local"}


@app.post("/api/plugin/analyze-image")
def compatibility_analyze(payload: dict) -> dict:
    from PIL import Image
    try:
        path = settings.require_allowed_path(payload.get("path", ""))
        with Image.open(path) as image:
            return {"path": str(path), "width": image.width, "height": image.height, "format": image.format, "mode": image.mode}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


studio = settings.root / "studio"
app.mount("/", StaticFiles(directory=studio, html=True), name="studio")
