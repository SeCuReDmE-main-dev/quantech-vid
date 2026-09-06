from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Callable

from .claims import build_claim_sidecar, claim_notice
from .config import Settings
from .production_schemas import ProductionJob
from .production_store import ProductionStore
from .renderer import render_project
from .scene3d import Scene3DError, validate_project_bounds
from .schemas import LocaleTrack, Profile, ProjectManifest, Scene


class ProductionService:
    def __init__(self, settings: Settings, store: ProductionStore,
                 render_fn: Callable = render_project, background: bool = True) -> None:
        self.settings = settings
        self.store = store
        self.render_fn = render_fn
        self.background = background
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="approved-render")
        self._drain_lock = Lock()
        self.store.recover_interrupted()
        if self.background:
            self.executor.submit(self.drain)

    def schedule(self) -> None:
        if self.background:
            self.executor.submit(self.drain)

    def drain(self) -> None:
        if not self._drain_lock.acquire(blocking=False):
            return
        try:
            while job_id := self.store.claim_next():
                self.run_job(job_id)
        finally:
            self._drain_lock.release()

    def run_job(self, job_id: str) -> ProductionJob:
        job, _ = self.store.job_internal(job_id)
        if job.status == "queued":
            claimed = self.store.claim_next()
            if claimed != job_id:
                return self.store.job_internal(job_id)[0]
        try:
            plan, revision, sources = self.store.plan_and_revision_internal(job.plan_id)
            document = revision.document
            if sum(scene.duration for scene in document.scenes) > float(plan.limits["max_duration_seconds"]):
                raise ValueError("RENDER_LIMIT_EXCEEDED")
            job_dir = self.settings.data_dir / "production" / job.id
            input_dir, output_dir = job_dir / "input", job_dir / "output"
            input_dir.mkdir(parents=True, exist_ok=True)
            source_names: dict[str, str] = {}
            for row in sources:
                source = Path(row["internal_path"])
                suffix = source.suffix.lower() or ".bin"
                name = f"source-{row['id']}{suffix}"
                target = input_dir / name
                shutil.copyfile(source, target)
                if self._sha256(target) != row["sha256"]:
                    raise ValueError("SOURCE_INTEGRITY_FAILED")
                source_names[row["id"]] = name
            manifest = ProjectManifest(
                slug=document.slug,
                title=document.title,
                disclosure=document.disclosure,
                locales=[LocaleTrack(locale=t.locale, title=t.title, narration=t.narration, voice=t.voice) for t in document.tracks],
                profiles=[Profile(name=p.name, width=p.width, height=p.height, fps=p.fps) for p in document.output_profiles],
                scenes=[Scene(id=s.id, duration=s.duration, title_fr=s.title.get("fr", ""), body_fr=s.body.get("fr", ""),
                              title_en=s.title.get("en", ""), body_en=s.body.get("en", ""),
                              notice=claim_notice(s.claims),
                              visual_3d=s.visual_3d,
                              asset=source_names[s.source_asset_id], fit=s.fit) for s in document.scenes],
                scene3d_binding=getattr(plan, "provider_resource_modes", {}).get("scene3d"),
                scene3d_frame_byte_limit=(int(plan.limits["max_scene3d_frame_bytes"])
                                          if "max_scene3d_frame_bytes" in plan.limits else None),
            )
            profile = next(item for item in document.output_profiles if item.name == plan.profile)
            if any(scene.visual_3d is not None for scene in document.scenes):
                validate_project_bounds(document.scenes, profile.width, profile.height, profile.fps,
                                        int(plan.limits["max_scene3d_frame_bytes"]))
            manifest_path = input_dir / "project.json"
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            artifacts = list(self.render_fn(
                self.settings, manifest_path, manifest, plan.locale, plan.profile, output_dir, "silent",
                progress=lambda value: self.store.update_job(job.id, progress=value),
                cancelled=lambda: self.store.cancellation_requested(job.id),
            ))
            if self.store.cancellation_requested(job.id):
                raise InterruptedError
            qa_paths = [path for path in artifacts if path.name.endswith("-qa.json")]
            if len(qa_paths) != 1:
                raise ValueError("RENDER_QA_FAILED")
            try:
                qa = json.loads(qa_paths[0].read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError("RENDER_QA_FAILED") from exc
            if not qa.get("passed") or not qa.get("webm", {}).get("passed"):
                raise ValueError("RENDER_QA_FAILED")
            sidecar = build_claim_sidecar(
                project_id=revision.project_id,
                revision=revision.revision,
                project_hash=revision.document_hash,
                scenes=document.scenes,
                source_hashes={row["id"]: row["sha256"] for row in sources},
            )
            claims_path = output_dir / f"{document.slug}-{plan.locale}-{plan.profile}-claims.json"
            claims_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True, ensure_ascii=False),
                                   encoding="utf-8")
            artifacts.append(claims_path)
            total = sum(path.stat().st_size for path in artifacts)
            if total > int(plan.limits["max_output_bytes"]):
                raise ValueError("RENDER_LIMIT_EXCEEDED")
            receipts = [self._receipt(path) for path in artifacts]
            self.store.complete_job(job.id, [str(path) for path in artifacts], receipts)
        except InterruptedError:
            self.store.update_job(job.id, status="cancelled", error_code="CANCELLED")
        except Exception as exc:
            known = {"RENDER_LIMIT_EXCEEDED", "SOURCE_INTEGRITY_FAILED", "RENDER_QA_FAILED",
                     "THREED_RENDERER_UNAVAILABLE", "THREED_BUNDLE_MISMATCH",
                     "THREED_PROFILE_LIMIT_EXCEEDED", "THREED_FRAME_LIMIT_EXCEEDED",
                     "THREED_FRAME_BYTES_LIMIT_EXCEEDED", "THREED_RENDER_DEADLINE_EXCEEDED"}
            code = str(exc) if str(exc) in known else "RENDER_FAILED"
            self.store.update_job(job.id, status="failed", error_code=code)
        return self.store.job_internal(job_id)[0]

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _receipt(self, path: Path) -> dict:
        suffix = path.suffix.lower()
        roles = {".mp4": "video-mp4", ".webm": "video-webm", ".srt": "captions-srt", ".vtt": "captions-vtt",
                 ".png": "poster", ".json": "metadata"}
        if path.name.endswith("-qa.json"): role = "quality-report"
        elif path.name.endswith("-claims.json"): role = "claim-provenance"
        elif path.name.endswith("-provenance.json"): role = "provenance"
        else: role = roles.get(suffix, "artifact")
        return {"name": path.name, "role": role, "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "size": path.stat().st_size, "sha256": self._sha256(path)}
