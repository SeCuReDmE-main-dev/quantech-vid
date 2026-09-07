from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from threading import Lock
from urllib.parse import unquote
from uuid import uuid4

import imageio_ffmpeg
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .config import Settings
from .engines import CodexEngineAdapter, CopilotEngineAdapter, AntigravityEngineAdapter
from .engines.models import EngineConnection
from .production_schemas import (AdmitSourceRequest, AuthorizePlanRequest, CreateProjectRequest,
    MigrateV1Request, OutputProfileV2, PrepareRenderPlanRequest, RegisterAgentRequest, ReviseProjectRequest,
    RunApprovedRenderRequest, SceneProjectV2, SceneV2, SourceAsset, SourceProvenance,
    OriginalSourceDescriptor,
    SourceRights, TrackV2)
from .production_service import ProductionService
from .production_store import ContractError, ProductionStore, now_iso
from .renderer import render_project
from .schemas import load_manifest
from .tool_catalog import public_catalog
from .tool_service import ToolService


@dataclass(frozen=True)
class APIConfig:
    expected_host: str
    allowed_origin: str
    pairing_code: str | None = None
    signing_key: bytes | None = None
    max_body_bytes: int = 1_000_000
    max_source_bytes: int = 50_000_000
    max_source_pixels: int = 40_000_000
    selections: dict[str, Path] = field(default_factory=dict)
    background_jobs: bool = True


class LoopbackBoundary:
    def __init__(self, app: Callable, config: APIConfig) -> None:
        self.app, self.config = app, config

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/v2"):
            await self.app(scope, receive, send); return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        if headers.get("host") != self.config.expected_host:
            await self._reject(scope, receive, send, 400, "HOST_REJECTED"); return
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"} and headers.get("origin") != self.config.allowed_origin:
            await self._reject(scope, receive, send, 403, "ORIGIN_REJECTED"); return
        body_limit = self.config.max_source_bytes if scope.get("path") == "/api/v2/sources/upload" else self.config.max_body_bytes
        try: declared = int(headers.get("content-length", "0"))
        except ValueError: declared = body_limit + 1
        if declared > body_limit:
            await self._reject(scope, receive, send, 413, "BODY_TOO_LARGE"); return
        seen = 0
        async def bounded_receive() -> dict:
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > body_limit: raise ContractError("BODY_TOO_LARGE", 413)
            return message
        try: await self.app(scope, bounded_receive, send)
        except ContractError as exc: await self._reject(scope, receive, send, exc.status, exc.code)

    @staticmethod
    async def _reject(scope: dict, receive: Callable, send: Callable, status: int, code: str) -> None:
        await JSONResponse({"error": {"code": code, "message": "Request rejected"}}, status_code=status)(scope, receive, send)


def _signing_key(data_dir: Path) -> bytes:
    configured = os.getenv("QUANTECH_VID_SIGNING_KEY")
    if configured: return configured.encode()
    path = data_dir / ".production-signing-key"
    if path.is_file(): return path.read_bytes()
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(key)
    try: path.chmod(0o600)
    except OSError: pass
    return key


def create_app(settings: Settings | None = None, api_config: APIConfig | None = None,
               render_fn: Callable = render_project) -> FastAPI:
    runtime = settings or Settings.load(); runtime.require_loopback()
    config = api_config or APIConfig(expected_host=f"{runtime.host}:{runtime.port}",
        allowed_origin=f"http://{runtime.host}:{runtime.port}", pairing_code=os.getenv("QUANTECH_VID_PAIRING_CODE"),
        signing_key=_signing_key(runtime.data_dir))
    if config.pairing_code is not None and len(config.pairing_code) < 16:
        raise ValueError("The operator pairing code must contain at least 16 characters")
    store = ProductionStore(runtime.data_dir / "production.sqlite3", config.signing_key or _signing_key(runtime.data_dir), config.pairing_code)
    service = ProductionService(runtime, store, render_fn=render_fn, background=config.background_jobs)
    application = FastAPI(title="QuaNTecH-ViD Studio", version="2.1.0")
    application.add_middleware(LoopbackBoundary, config=config)
    application.state.production_store, application.state.production_service = store, service

    @application.exception_handler(ContractError)
    async def contract_error(_: Request, exc: ContractError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": "Request could not be completed"}}, status_code=exc.status)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "Request validation failed"}}, status_code=422)

    def actor(request: Request) -> dict:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "): raise ContractError("AUTHENTICATION_REQUIRED", 401)
        csrf = request.headers.get("x-csrf-token") if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None
        principal = store.authenticate(authorization[7:], csrf if csrf else None)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and principal["kind"] == "human" and not csrf:
            raise ContractError("CSRF_REJECTED", 403)
        return principal

    def human(principal: dict = Depends(actor)) -> dict:
        if principal["kind"] != "human": raise ContractError("HUMAN_SESSION_REQUIRED", 403)
        return principal

    def agent(principal: dict = Depends(actor)) -> dict:
        if principal["kind"] != "agent": raise ContractError("AGENT_CLIENT_REQUIRED", 403)
        return principal

    application.state.engine_inspectors = {
        "openai_codex": CodexEngineAdapter(), "github_copilot": CopilotEngineAdapter(),
        "google_antigravity": AntigravityEngineAdapter(),
    }
    engine_probe_lock = Lock()

    @application.post("/api/v2/engines/{provider}/inspect")
    def inspect_engine(provider: str, principal: dict = Depends(human)) -> dict:
        del principal
        adapter = application.state.engine_inspectors.get(provider)
        if adapter is None:
            raise ContractError("ENGINE_NOT_FOUND", 404)
        if not engine_probe_lock.acquire(blocking=False):
            raise ContractError("ENGINE_INSPECTION_BUSY", 429)
        try:
            # Explicit human request only: inspect the installed client; never
            # begin login, start a model turn or enable a paid fallback.
            connection = EngineConnection.model_validate(adapter.inspect(timeout=2.0))
            if connection.provider != provider:
                raise ValueError("provider mismatch")
            return {"checked_at": now_iso(), "connection": connection.model_dump(mode="json")}
        except Exception as exc:
            raise ContractError("ENGINE_INSPECTION_FAILED", 503) from exc
        finally:
            engine_probe_lock.release()

    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
        return digest.hexdigest()

    def decoded_header(raw: str, maximum: int) -> str:
        if not raw or len(raw) > maximum * 3 or re.search(r"%(?![0-9A-Fa-f]{2})", raw):
            raise ContractError("INVALID_UPLOAD_METADATA", 422)
        try: decoded = unquote(raw, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc: raise ContractError("INVALID_UPLOAD_METADATA", 422) from exc
        if not (1 <= len(decoded) <= maximum): raise ContractError("INVALID_UPLOAD_METADATA", 422)
        return decoded

    def validate_image(path: Path) -> str:
        try:
            with Image.open(path) as image:
                if image.width * image.height > config.max_source_pixels:
                    raise ContractError("SOURCE_TOO_LARGE", 413)
                image.verify(); detected = image.format
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ContractError("UNSUPPORTED_SOURCE_MEDIA", 422) from exc
        if detected not in {"PNG", "JPEG", "WEBP"}:
            raise ContractError("UNSUPPORTED_SOURCE_MEDIA", 422)
        return detected

    def admit_derived_image(source: Path, principal: dict, provenance: SourceProvenance,
                            rights: SourceRights, operations: list[str],
                            original_path: Path | None = None) -> SourceAsset:
        detected = validate_image(source)
        asset_id = "src_" + uuid4().hex
        target = runtime.data_dir / "admitted-assets" / f"{asset_id}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as opened:
            opened.convert("RGB").save(target, format="PNG", optimize=True)
        asset = SourceAsset(id=asset_id, sha256=file_sha256(target), media_type="image/png", size=target.stat().st_size,
            provenance=provenance, rights=rights, allowed_operations=sorted(set(operations)), created_at=now_iso())
        return store.add_source(principal, asset, target, original_path=original_path)

    @application.get("/api/v1/health")
    @application.get("/api/v2/health")
    def health() -> dict:
        render_ready = (Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file() and runtime.data_dir.is_dir()
                        and os.access(runtime.data_dir, os.W_OK))
        return {"status": "ok", "version": application.version, "loopback": True,
            "capabilities": {"approved_silent_render": render_ready, "network_import": False,
                             "legacy_render": False, "paid_narration": False}}

    @application.post("/api/v2/pair", status_code=201)
    def pair(payload: dict) -> dict:
        code, actor_id = payload.get("operator_code"), payload.get("actor_id")
        if (not isinstance(code, str) or not 16 <= len(code) <= 200 or not isinstance(actor_id, str)
                or not (3 <= len(actor_id) <= 80)):
            raise ContractError("PAIRING_REJECTED", 403)
        token, csrf = store.consume_pairing(code, actor_id)
        return {"actor_id": actor_id, "actor_type": "human", "session_token": token,
                "csrf_token": csrf, "expires_in_seconds": 28800}

    @application.post("/api/v2/agents", status_code=201)
    def register_agent(payload: RegisterAgentRequest, principal: dict = Depends(human)) -> dict:
        return {"agent_id": payload.agent_id, "actor_type": "agent",
                "client_token": store.create_agent(
                    principal["id"], payload.agent_id, payload.label, payload.project_id,
                    payload.revision, payload.source_ids,
                )}

    @application.delete("/api/v2/session", status_code=204)
    def revoke_session(request: Request, principal: dict = Depends(actor)) -> None:
        del principal
        store.revoke_session(request.headers["authorization"][7:])

    @application.delete("/api/v2/agents/{agent_id}", status_code=204)
    def revoke_agent(agent_id: str, principal: dict = Depends(human)) -> None:
        store.revoke_actor_sessions(principal["id"], agent_id)

    @application.post("/api/v2/sources/admit", response_model=SourceAsset, status_code=201)
    def admit_source(payload: AdmitSourceRequest, principal: dict = Depends(human)) -> SourceAsset:
        selected = config.selections.get(payload.selection_token)
        if selected is None: raise ContractError("SELECTION_NOT_FOUND", 404)
        source = selected.resolve()
        if selected.is_symlink() or not source.is_file() or not any(source == root or root in source.parents for root in runtime.allowed_asset_roots):
            raise ContractError("SELECTION_REJECTED", 403)
        if source.stat().st_size > config.max_source_bytes: raise ContractError("SOURCE_TOO_LARGE", 413)
        provenance = SourceProvenance.model_validate(payload.provenance.model_dump(mode="json"))
        return admit_derived_image(source, principal, provenance, payload.rights, payload.allowed_operations)

    @application.post("/api/v2/sources/upload", status_code=201)
    async def upload_source(request: Request, principal: dict = Depends(human)) -> dict:
        filename = decoded_header(request.headers.get("x-file-name", ""), 180)
        basis = request.headers.get("x-rights-basis", "")
        reference = decoded_header(request.headers.get("x-rights-reference", ""), 500)
        origin = decoded_header(request.headers.get("x-source-origin", "browser%20file%20selection"), 240)
        if (Path(filename).name != filename
                or "/" in filename or "\\" in filename or basis not in {"owned", "licensed", "public-domain", "permission"}
                ):
            raise ContractError("INVALID_UPLOAD_METADATA", 422)
        suffix = Path(filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".txt", ".md", ".markdown"}:
            raise ContractError("UNSUPPORTED_SOURCE_MEDIA", 422)
        upload_id = uuid4().hex
        staging = runtime.data_dir / "tmp" / f"upload-{upload_id}.part"
        original = runtime.data_dir / "source-originals" / upload_id / f"original{suffix}"
        staging.parent.mkdir(parents=True, exist_ok=True); original.parent.mkdir(parents=True, exist_ok=True)
        size, digest, admitted = 0, hashlib.sha256(), False
        derived: Path | None = None
        try:
            with staging.open("wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > config.max_source_bytes: raise ContractError("SOURCE_TOO_LARGE", 413)
                    digest.update(chunk); stream.write(chunk)
            if size == 0: raise ContractError("EMPTY_SOURCE", 422)
            shutil.move(staging, original)
            rights = SourceRights(basis=basis, reference=reference)
            if suffix in {".txt", ".md", ".markdown"}:
                try: text = original.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc: raise ContractError("INVALID_UTF8_SOURCE", 422) from exc
                if len(text) > 200_000: raise ContractError("SOURCE_TOO_LARGE", 413)
                derived = runtime.data_dir / "tmp" / f"derived-{upload_id}.png"
                canvas = Image.new("RGB", (1280, 720), "#0b3438"); draw = ImageDraw.Draw(canvas)
                font = ImageFont.load_default(size=30)
                lines = textwrap.wrap(text.replace("\x00", ""), width=68)[:16]
                draw.multiline_text((70, 70), "\n".join(lines) or "(empty text)", fill="white", font=font, spacing=12)
                canvas.save(derived, format="PNG")
                original_media_type = "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
                transformation = "literal-text-preview-v1"
            else:
                detected = validate_image(original); derived = original
                original_media_type = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[detected]
                transformation = "rgb-png-v1"
            original_descriptor = OriginalSourceDescriptor(
                name=filename, media_type=original_media_type, size=size,
                sha256=digest.hexdigest(), transformation=transformation,
            )
            provenance = SourceProvenance(
                origin=origin, collected_by=principal["id"], original=original_descriptor,
            )
            asset = admit_derived_image(
                derived, principal, provenance, rights, ["render", "analyze"], original_path=original,
            )
            admitted = True
            return {"asset": asset.model_dump(mode="json"), "original": {"name": filename,
                "media_type": original_media_type,
                "size": size, "sha256": digest.hexdigest()}, "derived": True}
        finally:
            staging.unlink(missing_ok=True)
            if derived is not None and derived != original: derived.unlink(missing_ok=True)
            if not admitted: shutil.rmtree(original.parent, ignore_errors=True)

    @application.post("/api/v2/sources/sample", response_model=SourceAsset, status_code=201)
    def create_sample_source(principal: dict = Depends(human)) -> SourceAsset:
        asset_id = "src_" + uuid4().hex
        target = runtime.data_dir / "admitted-assets" / f"{asset_id}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (640, 360), "#0b3438")
        image.save(target, format="PNG")
        digest_builder = hashlib.sha256()
        with target.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""): digest_builder.update(block)
        asset = SourceAsset(id=asset_id, sha256=digest_builder.hexdigest(), media_type="image/png",
            size=target.stat().st_size,
            provenance=SourceProvenance(origin="QuaNTecH-ViD deterministic sample generator",
                                        collected_by=principal["id"], note="Synthetic onboarding fixture"),
            rights=SourceRights(basis="owned", reference="Built-in synthetic fixture; no personal or third-party media"),
            allowed_operations=["render", "analyze"], created_at=now_iso())
        return store.add_source(principal, asset, target)

    @application.post("/api/v2/sources/import-url")
    def import_url(_: dict, principal: dict = Depends(actor)) -> None:
        del principal; raise ContractError("NETWORK_IMPORT_DISABLED", 501)

    @application.get("/api/v2/sources")
    def list_sources(principal: dict = Depends(actor)) -> dict:
        return {"sources": [item.model_dump(mode="json") for item in store.list_sources(principal)]}

    @application.get("/api/v2/sources/{source_id}/preview")
    def source_preview(source_id: str, principal: dict = Depends(actor)) -> Response:
        if re.fullmatch(r"src_[0-9a-f]{32}", source_id) is None:
            raise ContractError("SOURCE_NOT_FOUND", 404)
        payload, media_type = store.source_preview(principal, source_id)
        expected_format = {
            "image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP",
        }[media_type]
        try:
            with Image.open(io.BytesIO(payload)) as image:
                detected_format = image.format
                if image.width * image.height > 16_000_000:
                    raise ContractError("SOURCE_PREVIEW_TOO_LARGE", 413)
                image.verify()
            # Header verification alone does not decode JPEG pixel data.
            with Image.open(io.BytesIO(payload)) as decoded:
                decoded.load()
        except ContractError:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ContractError("SOURCE_PREVIEW_INTEGRITY_FAILED", 409) from exc
        if detected_format != expected_format:
            raise ContractError("SOURCE_PREVIEW_UNSUPPORTED_MEDIA", 415)
        return Response(content=payload, media_type=media_type, headers={
            "Cache-Control": "no-store",
            "Content-Length": str(len(payload)),
            "X-Content-Type-Options": "nosniff",
        })

    @application.get("/api/v2/tools/catalog")
    def tools_catalog(principal: dict = Depends(agent)) -> dict:
        del principal
        return {"tools": public_catalog()}

    @application.post("/api/v2/tools/{tool_name}")
    def call_tool(tool_name: str, payload: dict, principal: dict = Depends(agent)) -> dict:
        return ToolService(store, service, principal).dispatch(tool_name, payload)

    @application.get("/api/v2/legacy-projects")
    def list_legacy_projects(principal: dict = Depends(human)) -> dict:
        del principal
        found = []
        project_root = (runtime.root / "projects").resolve()
        for manifest_path in sorted(project_root.glob("*/project.json")):
            try:
                manifest = load_manifest(manifest_path)
                found.append({"slug": manifest_path.parent.name, "title": manifest.title,
                              "duration": manifest.duration, "schema_version": manifest.schema_version})
            except (OSError, ValueError):
                continue
        return {"projects": found}

    @application.post("/api/v2/legacy-projects/{slug}/import", status_code=201)
    def migrate_legacy_project(slug: str, payload: MigrateV1Request,
                               principal: dict = Depends(human)) -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug):
            raise ContractError("LEGACY_PROJECT_NOT_FOUND", 404)
        project_root = (runtime.root / "projects").resolve()
        manifest_path = (project_root / slug / "project.json").resolve()
        if project_root not in manifest_path.parents or not manifest_path.is_file() or manifest_path.is_symlink():
            raise ContractError("LEGACY_PROJECT_NOT_FOUND", 404)
        if manifest_path.stat().st_size > config.max_body_bytes:
            raise ContractError("LEGACY_PROJECT_TOO_LARGE", 413)
        try: legacy = load_manifest(manifest_path)
        except (OSError, ValueError) as exc: raise ContractError("INVALID_LEGACY_PROJECT", 422) from exc
        source_paths: list[Path] = []
        for scene in legacy.scenes:
            source = (manifest_path.parent / scene.asset).resolve()
            if (manifest_path.parent.resolve() not in source.parents or not source.is_file()
                    or source.is_symlink() or source.stat().st_size > config.max_source_bytes):
                raise ContractError("INVALID_LEGACY_ASSET", 422)
            validate_image(source)
            if source not in source_paths: source_paths.append(source)
        archive_id = uuid4().hex
        archive = runtime.data_dir / "v1-imports" / archive_id
        archive.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(manifest_path, archive / "project.v1.json")
        source_map: dict[Path, SourceAsset] = {}
        for source in source_paths:
            shutil.copyfile(source, archive / f"source-{len(source_map) + 1:03}{source.suffix.lower()}")
            provenance = SourceProvenance.model_validate(payload.provenance.model_dump(mode="json"))
            source_map[source] = admit_derived_image(source, principal, provenance, payload.rights,
                                                     ["render", "analyze"])
        document = SceneProjectV2(slug=legacy.slug, title=legacy.title, disclosure=legacy.disclosure,
            sources=[item.id for item in source_map.values()],
            scenes=[SceneV2(id=scene.id, duration=scene.duration,
                source_asset_id=source_map[(manifest_path.parent / scene.asset).resolve()].id,
                title={"fr": scene.title_fr, "en": scene.title_en},
                body={"fr": scene.body_fr, "en": scene.body_en}, fit=scene.fit) for scene in legacy.scenes],
            tracks=[TrackV2(locale=track.locale, title=track.title, narration=track.narration, voice=track.voice)
                    for track in legacy.locales],
            output_profiles=[OutputProfileV2(**profile.model_dump()) for profile in legacy.profiles])
        revision = store.create_project(principal, document)
        return {"migration": "copied", "source_schema_version": "1.0",
                "original_manifest_sha256": file_sha256(manifest_path),
                "project": revision.model_dump(mode="json")}

    @application.post("/api/v2/projects", status_code=201)
    def create_project_route(payload: CreateProjectRequest, principal: dict = Depends(human)) -> dict:
        return store.create_project(principal, payload.document).model_dump(mode="json")

    @application.get("/api/v2/projects")
    def list_projects(principal: dict = Depends(actor)) -> dict:
        return {"projects": store.list_projects(principal)}

    @application.get("/api/v2/projects/{project_id}")
    def read_project(project_id: str, revision: int | None = None, principal: dict = Depends(actor)) -> dict:
        return store.get_revision(principal, project_id, revision).model_dump(mode="json")

    @application.put("/api/v2/projects/{project_id}")
    def revise_project_route(project_id: str, payload: ReviseProjectRequest, principal: dict = Depends(human)) -> dict:
        return store.revise_project(principal, project_id, payload.base_revision, payload.document).model_dump(mode="json")

    @application.post("/api/v2/render-plans", status_code=201)
    def prepare_plan(payload: PrepareRenderPlanRequest, principal: dict = Depends(actor)) -> dict:
        return store.create_plan(principal, payload.model_dump()).model_dump(mode="json")

    @application.post("/api/v2/render-plans/{plan_id}/authorize", status_code=201)
    def authorize_plan(plan_id: str, payload: AuthorizePlanRequest, principal: dict = Depends(human)) -> dict:
        plan = store.get_plan(principal, plan_id)
        if plan.project_id != payload.project_id or plan.revision != payload.revision:
            raise ContractError("PLAN_BINDING_MISMATCH", 409)
        grant_id = store.authorize(principal, plan_id, payload.agent_id, payload.expires_in_seconds)
        return {"grant_id": grant_id, "plan_id": plan_id, "scope": "render", "status": "approved"}

    @application.delete("/api/v2/production-grants/{grant_id}", status_code=204)
    def revoke_grant(grant_id: str, principal: dict = Depends(human)) -> None:
        store.revoke_grant(principal, grant_id)

    @application.post("/api/v2/renders/run-approved", status_code=202)
    def run_approved(payload: RunApprovedRenderRequest, idempotency_key: str = Header(min_length=16, max_length=200),
                     principal: dict = Depends(actor)) -> dict:
        job, created = store.enqueue_approved(principal, payload.plan_id, idempotency_key)
        if created: service.schedule()
        return job.model_dump(mode="json")

    @application.get("/api/v2/renders/{job_id}")
    def render_status(job_id: str, principal: dict = Depends(actor)) -> dict:
        return store.job_for(principal, job_id).model_dump(mode="json")

    @application.get("/api/v2/renders/{job_id}/artifacts/{name}")
    def read_artifact(job_id: str, name: str, request: Request,
                      principal: dict = Depends(actor)) -> StreamingResponse:
        path, receipt = store.artifact_for(principal, job_id, name)
        if not secrets.compare_digest(file_sha256(path), receipt.sha256):
            raise ContractError("ARTIFACT_INTEGRITY_FAILED", 409)
        if receipt.role in {"video-mp4", "video-webm"}:
            job = store.job_for(principal, job_id)
            qa_names = {item.name for item in job.receipts if item.role == "quality-report"}
            if len(qa_names) != 1: raise ContractError("ARTIFACT_QA_FAILED", 409)
            qa_path, qa_receipt = store.artifact_for(principal, job_id, next(iter(qa_names)))
            if not secrets.compare_digest(file_sha256(qa_path), qa_receipt.sha256):
                raise ContractError("ARTIFACT_QA_FAILED", 409)
            try: qa = json.loads(qa_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc: raise ContractError("ARTIFACT_QA_FAILED", 409) from exc
            if not qa.get("passed") or not qa.get("webm", {}).get("passed"):
                raise ContractError("ARTIFACT_QA_FAILED", 409)
        size = path.stat().st_size; start, end, status = 0, size - 1, 200
        range_value = request.headers.get("range")
        headers = {"Accept-Ranges": "bytes", "Content-Disposition": f'inline; filename="{receipt.name}"',
                   "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        if range_value:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_value)
            if not match: raise ContractError("RANGE_REJECTED", 416)
            start = int(match.group(1)); requested_end = int(match.group(2)) if match.group(2) else min(size - 1, start + 8_388_607)
            end = min(requested_end, size - 1)
            if start >= size or end < start or end - start + 1 > 8_388_608:
                raise ContractError("RANGE_REJECTED", 416)
            status = 206; headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = end - start + 1; headers["Content-Length"] = str(length)
        def chunks():
            remaining = length
            with path.open("rb") as stream:
                stream.seek(start)
                while remaining:
                    block = stream.read(min(65_536, remaining))
                    if not block: break
                    remaining -= len(block); yield block
        return StreamingResponse(chunks(), status_code=status, media_type=receipt.media_type, headers=headers)

    @application.post("/api/v2/renders/{job_id}/cancel")
    def cancel(job_id: str, principal: dict = Depends(actor)) -> dict:
        return store.cancel(principal, job_id).model_dump(mode="json")

    @application.api_route("/api/v1/{legacy_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def disabled_legacy(legacy_path: str) -> JSONResponse:
        del legacy_path
        return JSONResponse({"error": {"code": "LEGACY_API_DISABLED", "message": "Use the paired approval API"}}, status_code=410)

    @application.api_route("/api/plugin/{legacy_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def disabled_plugin(legacy_path: str) -> JSONResponse:
        del legacy_path
        return JSONResponse({"error": {"code": "LEGACY_PLUGIN_API_DISABLED", "message": "Compatibility endpoint is disabled"}}, status_code=410)

    @application.api_route("/api/{unknown_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_api(unknown_path: str) -> JSONResponse:
        del unknown_path
        return JSONResponse({"error": {"code": "API_ROUTE_NOT_FOUND", "message": "Request could not be completed"}}, status_code=404)
    @application.middleware("http")
    async def browser_security_headers(request: Request, call_next: Callable):
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            )
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    built_studio = runtime.root / "apps" / "studio" / "dist"
    studio = built_studio if built_studio.is_dir() else runtime.root / "studio"
    if studio.is_dir():
        application.mount("/", StaticFiles(directory=studio, html=True), name="studio")
    return application

def default_app() -> FastAPI:
    """Uvicorn factory; importing this module never loads environment state or starts workers."""
    return create_app()
