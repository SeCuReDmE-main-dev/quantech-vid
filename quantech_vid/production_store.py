from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .production_schemas import (
    ArtifactReceipt,
    ProductionJob,
    ProjectRevision,
    RenderPlan,
    SceneProjectV2,
    SourceAsset,
    OriginalSourceDescriptor,
)
from .local_voice import (LocalVoiceError, LocalVoicePilot, MAX_TEXT_CHARS,
                          PILOT_LANGUAGE, PILOT_VOICE)
from .store_migrations import allow_supported_originals
from .scene3d import (MAX_SCENE3D_FRAME_BYTES, MAX_SCENE3D_FRAMES, Scene3DUnavailable,
                      runtime_binding)


MAX_SOURCE_PREVIEW_BYTES = 10 * 1024 * 1024

_SOURCE_DERIVATIVE_ERRORS = {
    "render": {
        "unsupported": ("SOURCE_PREVIEW_UNSUPPORTED_MEDIA", 415),
        "too_large": ("SOURCE_PREVIEW_TOO_LARGE", 413),
        "integrity": ("SOURCE_PREVIEW_INTEGRITY_FAILED", 409),
    },
    "analyze": {
        "unsupported": ("SOURCE_ANALYSIS_UNSUPPORTED_MEDIA", 415),
        "too_large": ("SOURCE_ANALYSIS_TOO_LARGE", 413),
        "integrity": ("SOURCE_ANALYSIS_INTEGRITY_FAILED", 409),
    },
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utc_now().isoformat()


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class ContractError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class ProductionStore:
    def __init__(self, path: Path, signing_key: bytes, pairing_code: str | None = None,
                 local_voice: LocalVoicePilot | None = None) -> None:
        self.path = path
        self.signing_key = signing_key
        self.local_voice = local_voice
        self.lock = Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize(pairing_code)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self, pairing_code: str | None) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS security_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1), pairing_hash TEXT, pairing_consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS actors (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('human','agent')),
                    owner_id TEXT, label TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES actors(id)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, actor_id TEXT NOT NULL, csrf_hash TEXT,
                    expires_at TEXT NOT NULL, revoked_at TEXT, FOREIGN KEY(actor_id) REFERENCES actors(id)
                );
                CREATE TABLE IF NOT EXISTS agent_scopes (
                    actor_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(actor_id) REFERENCES actors(id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id,revision) REFERENCES project_revisions(project_id,revision) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS agent_scope_sources (
                    actor_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    PRIMARY KEY(actor_id,source_id),
                    FOREIGN KEY(actor_id) REFERENCES agent_scopes(actor_id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES source_assets(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS source_assets (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, sha256 TEXT NOT NULL,
                    internal_path TEXT NOT NULL, media_type TEXT NOT NULL, size INTEGER NOT NULL,
                    provenance_json TEXT NOT NULL, rights_json TEXT NOT NULL,
                    operations_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES actors(id)
                );
                CREATE TABLE IF NOT EXISTS source_originals (
                    source_id TEXT PRIMARY KEY,
                    internal_path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    name TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    transformation TEXT NOT NULL CHECK(
                        transformation IN ('literal-text-preview-v1','rgb-png-v1','glb-four-view-png-v1','pcm16-waveform-png-v1')
                    ),
                    FOREIGN KEY(source_id) REFERENCES source_assets(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES actors(id)
                );
                CREATE TABLE IF NOT EXISTS project_revisions (
                    project_id TEXT NOT NULL, revision INTEGER NOT NULL, document_hash TEXT NOT NULL,
                    document_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, revision), FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE TABLE IF NOT EXISTS render_plans (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    plan_hash TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, FOREIGN KEY(project_id, revision)
                    REFERENCES project_revisions(project_id, revision)
                );
                CREATE TABLE IF NOT EXISTS production_grants (
                    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                    scope TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT,
                    consumed_at TEXT, signature TEXT NOT NULL, created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, FOREIGN KEY(plan_id) REFERENCES render_plans(id),
                    FOREIGN KEY(agent_id) REFERENCES actors(id)
                );
                CREATE TABLE IF NOT EXISTS production_jobs (
                    id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL, plan_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, error_code TEXT,
                    internal_artifacts_json TEXT NOT NULL DEFAULT '[]', receipts_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(actor_id, idempotency_key), FOREIGN KEY(plan_id) REFERENCES render_plans(id)
                );
                """
            )
            allow_supported_originals(db, self.path)
            columns = {item[1] for item in db.execute("PRAGMA table_info(security_state)").fetchall()}
            for name, declaration in (("pairing_expires_at", "TEXT"), ("pairing_failed_attempts", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE security_state ADD COLUMN {name} {declaration}")
            row = db.execute("SELECT * FROM security_state WHERE id=1").fetchone()
            expiry = (utc_now() + timedelta(minutes=10)).isoformat()
            if row is None:
                db.execute(
                    "INSERT INTO security_state(id,pairing_hash,pairing_consumed_at,pairing_expires_at) VALUES(1,?,NULL,?)",
                    (token_hash(pairing_code) if pairing_code else None, expiry if pairing_code else None),
                )
            elif pairing_code and token_hash(pairing_code) != row["pairing_hash"]:
                db.execute("UPDATE security_state SET pairing_hash=?,pairing_consumed_at=NULL,pairing_expires_at=?,pairing_failed_attempts=0 WHERE id=1",
                           (token_hash(pairing_code), expiry))

    def issue_pairing_code(self) -> str:
        """Local operator command only. Never expose this function as an HTTP or agent tool."""
        code = secrets.token_urlsafe(24)
        expiry = (utc_now() + timedelta(minutes=10)).isoformat()
        with self.lock, self._connect() as db:
            db.execute("UPDATE security_state SET pairing_hash=?,pairing_consumed_at=NULL,pairing_expires_at=?,pairing_failed_attempts=0 WHERE id=1",
                       (token_hash(code), expiry))
        return code

    def consume_pairing(self, code: str, actor_id: str) -> tuple[str, str]:
        raw_token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        stamp, expiry = now_iso(), (utc_now() + timedelta(hours=8)).isoformat()
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM security_state WHERE id=1").fetchone()
            if not row or not row["pairing_hash"]:
                raise ContractError("PAIRING_DISABLED", 503)
            if (row["pairing_consumed_at"] or not row["pairing_expires_at"]
                    or datetime.fromisoformat(row["pairing_expires_at"]) <= utc_now()
                    or row["pairing_failed_attempts"] >= 10):
                raise ContractError("PAIRING_REJECTED", 403)
            if not hmac.compare_digest(row["pairing_hash"], token_hash(code)):
                db.execute("UPDATE security_state SET pairing_failed_attempts=pairing_failed_attempts+1 WHERE id=1")
                db.commit()  # Failed-attempt budget must survive the rejected transaction.
                raise ContractError("PAIRING_REJECTED", 403)
            db.execute("UPDATE security_state SET pairing_consumed_at=? WHERE id=1", (stamp,))
            existing = db.execute("SELECT kind FROM actors WHERE id=?", (actor_id,)).fetchone()
            if existing and existing["kind"] != "human":
                raise ContractError("PAIRING_REJECTED", 403)
            db.execute("INSERT OR IGNORE INTO actors VALUES(?,?,?,?,?)", (actor_id, "human", None, actor_id, stamp))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,NULL)", (token_hash(raw_token), actor_id, token_hash(csrf), expiry))
        return raw_token, csrf

    def authenticate(self, raw_token: str, csrf: str | None = None) -> dict:
        with self._connect() as db:
            row = db.execute(
                """SELECT s.*,a.kind,a.owner_id,sc.project_id AS scope_project_id,
                          sc.revision AS scope_revision
                   FROM sessions s JOIN actors a ON a.id=s.actor_id
                   LEFT JOIN agent_scopes sc ON sc.actor_id=a.id WHERE token_hash=?""",
                (token_hash(raw_token),),
            ).fetchone()
        if not row or row["revoked_at"] or datetime.fromisoformat(row["expires_at"]) <= utc_now():
            raise ContractError("AUTHENTICATION_REQUIRED", 401)
        if csrf is not None and (not row["csrf_hash"] or not hmac.compare_digest(row["csrf_hash"], token_hash(csrf))):
            raise ContractError("CSRF_REJECTED", 403)
        principal = {"id": row["actor_id"], "kind": row["kind"], "owner_id": row["owner_id"]}
        if row["kind"] == "agent":
            if row["scope_project_id"] is None or row["scope_revision"] is None:
                raise ContractError("AGENT_SCOPE_REQUIRED", 403)
            with self._connect() as db:
                sources = db.execute(
                    "SELECT source_id FROM agent_scope_sources WHERE actor_id=? ORDER BY source_id",
                    (row["actor_id"],),
                ).fetchall()
            principal.update({"project_id": row["scope_project_id"], "revision": row["scope_revision"],
                              "source_ids": [item["source_id"] for item in sources]})
        return principal

    def create_agent(self, human_id: str, agent_id: str, label: str, project_id: str,
                     revision: int, source_ids: list[str] | None = None) -> str:
        human = {"id": human_id, "kind": "human", "owner_id": None}
        project_revision = self.get_revision(human, project_id, revision)
        authorized_sources = list(dict.fromkeys([*project_revision.document.sources, *(source_ids or [])]))
        if len(authorized_sources) > 128:
            raise ContractError("AGENT_SCOPE_TOO_LARGE", 422)
        self.source_rows(human, authorized_sources)
        raw_token, stamp = secrets.token_urlsafe(32), now_iso()
        expiry = (utc_now() + timedelta(days=30)).isoformat()
        with self.lock, self._connect() as db:
            try:
                db.execute("INSERT INTO actors VALUES(?,?,?,?,?)", (agent_id, "agent", human_id, label, stamp))
                db.execute("INSERT INTO agent_scopes VALUES(?,?,?,?)", (agent_id, project_id, revision, stamp))
                db.executemany("INSERT INTO agent_scope_sources VALUES(?,?)",
                               [(agent_id, source_id) for source_id in authorized_sources])
                db.execute("INSERT INTO sessions VALUES(?,?,?,?,NULL)", (token_hash(raw_token), agent_id, None, expiry))
            except sqlite3.IntegrityError as exc:
                raise ContractError("AGENT_ALREADY_EXISTS") from exc
        return raw_token

    def revoke_actor_sessions(self, human_id: str, actor_id: str) -> None:
        with self.lock, self._connect() as db:
            target = db.execute("SELECT kind,owner_id FROM actors WHERE id=?", (actor_id,)).fetchone()
            if not target or (actor_id != human_id and target["owner_id"] != human_id):
                raise ContractError("ACTOR_NOT_FOUND", 404)
            db.execute("UPDATE sessions SET revoked_at=? WHERE actor_id=? AND revoked_at IS NULL", (now_iso(), actor_id))
            db.execute("UPDATE production_grants SET revoked_at=? WHERE agent_id=? AND consumed_at IS NULL AND revoked_at IS NULL",
                       (now_iso(), actor_id))

    def revoke_session(self, raw_token: str) -> None:
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT s.actor_id,a.kind FROM sessions s JOIN actors a ON a.id=s.actor_id WHERE s.token_hash=? AND s.revoked_at IS NULL",
                             (token_hash(raw_token),)).fetchone()
            cursor = db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                                (now_iso(), token_hash(raw_token)))
            if cursor.rowcount != 1:
                raise ContractError("AUTHENTICATION_REQUIRED", 401)
            if row["kind"] == "human":
                agent_ids = [item[0] for item in db.execute("SELECT id FROM actors WHERE owner_id=?", (row["actor_id"],)).fetchall()]
                db.execute("UPDATE sessions SET revoked_at=? WHERE actor_id IN (SELECT id FROM actors WHERE owner_id=?) AND revoked_at IS NULL",
                           (now_iso(), row["actor_id"]))
                for agent_id in agent_ids:
                    db.execute("UPDATE production_grants SET revoked_at=? WHERE agent_id=? AND consumed_at IS NULL AND revoked_at IS NULL",
                               (now_iso(), agent_id))

    def _owner_for(self, actor: dict) -> str:
        return actor["id"] if actor["kind"] == "human" else actor["owner_id"]

    @staticmethod
    def _agent_scope_matches(actor: dict, project_id: str, revision: int) -> bool:
        return (actor["kind"] != "agent"
                or (actor.get("project_id") == project_id and actor.get("revision") == revision))

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _original_path(self, path: Path) -> Path:
        resolved = path.resolve()
        root = (self.path.parent / "source-originals").resolve()
        if root not in resolved.parents:
            raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409)
        return resolved

    def _verify_original_file(self, path: Path, descriptor: OriginalSourceDescriptor) -> Path:
        try:
            # Check the selected path before resolving it: resolution would hide
            # a substituted symlink. Inaccessible/disappearing files are also a
            # closed integrity failure, not an unfiltered filesystem exception.
            if path.is_symlink():
                raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409)
            resolved = self._original_path(path)
            if (not resolved.is_file() or resolved.stat().st_size != descriptor.size
                    or not hmac.compare_digest(self._file_sha256(resolved), descriptor.sha256)):
                raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409)
            return resolved
        except (OSError, RuntimeError) as exc:
            raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409) from exc

    def add_source(self, actor: dict, asset: SourceAsset, internal_path: Path,
                   original_path: Path | None = None) -> SourceAsset:
        owner = self._owner_for(actor)
        descriptor = asset.provenance.original
        if (descriptor is None) != (original_path is None):
            raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409)
        verified_original = (self._verify_original_file(original_path, descriptor)
                             if original_path is not None and descriptor is not None else None)
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT INTO source_assets VALUES(?,?,?,?,?,?,?,?,?,?)",
                (asset.id, owner, asset.sha256, str(internal_path), asset.media_type, asset.size,
                 asset.provenance.model_dump_json(), asset.rights.model_dump_json(),
                 json.dumps(asset.allowed_operations), asset.created_at),
            )
            if verified_original is not None and descriptor is not None:
                db.execute(
                    "INSERT INTO source_originals VALUES(?,?,?,?,?,?,?)",
                    (asset.id, str(verified_original), descriptor.sha256, descriptor.name,
                     descriptor.size, descriptor.media_type, descriptor.transformation),
                )
        return asset

    def _verified_originals(self, sources: list[sqlite3.Row],
                            expected_hashes: dict[str, str] | None = None) -> list[sqlite3.Row]:
        source_ids = [row["id"] for row in sources]
        with self._connect() as db:
            rows = [db.execute("SELECT * FROM source_originals WHERE source_id=?", (source_id,)).fetchone()
                    for source_id in source_ids]
        linked = {row["source_id"]: row for row in rows if row is not None}
        for source in sources:
            try:
                provenance = json.loads(source["provenance_json"])
                if not isinstance(provenance, dict):
                    raise ValueError("Invalid provenance object")
                descriptor_payload = provenance.get("original")
            except (ValueError, TypeError) as exc:
                raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409) from exc
            lineage = linked.get(source["id"])
            if (descriptor_payload is None) != (lineage is None):
                raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409)
            if lineage is None:
                continue
            try:
                descriptor = OriginalSourceDescriptor.model_validate(descriptor_payload)
            except ValueError as exc:
                raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409) from exc
            stored = {key: lineage[key] for key in
                      ("name", "sha256", "size", "media_type", "transformation")}
            if descriptor.model_dump(mode="json") != stored:
                raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409)
            try:
                self._verify_original_file(Path(lineage["internal_path"]), descriptor)
            except TypeError as exc:
                raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409) from exc
        actual_hashes = {source_id: row["sha256"] for source_id, row in linked.items()}
        if expected_hashes is not None and actual_hashes != expected_hashes:
            raise ContractError("PLAN_INTEGRITY_FAILED", 409)
        return [linked[source_id] for source_id in source_ids if source_id in linked]

    def source_rows(self, actor: dict, source_ids: list[str], operation: str = "render") -> list[sqlite3.Row]:
        owner = self._owner_for(actor)
        if actor["kind"] == "agent" and any(source_id not in actor.get("source_ids", []) for source_id in source_ids):
            raise ContractError("SOURCE_NOT_FOUND", 404)
        with self._connect() as db:
            rows = [db.execute("SELECT * FROM source_assets WHERE id=? AND owner_id=?", (sid, owner)).fetchone() for sid in source_ids]
        if any(row is None for row in rows):
            raise ContractError("SOURCE_NOT_FOUND", 404)
        if any(operation not in json.loads(row["operations_json"]) for row in rows if row):
            raise ContractError("SOURCE_OPERATION_FORBIDDEN", 403)
        return rows  # type: ignore[return-value]

    def _verified_source_derivative(self, actor: dict, source_id: str,
                                    operation: str) -> tuple[bytes, str, str]:
        """Read one admitted derivative under a fixed internal operation boundary."""
        if operation not in _SOURCE_DERIVATIVE_ERRORS:
            raise ValueError("unsupported internal source operation")
        errors = _SOURCE_DERIVATIVE_ERRORS[operation]
        row = self.source_rows(actor, [source_id], operation=operation)[0]
        media_type = row["media_type"]
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ContractError(*errors["unsupported"])
        try:
            path = Path(row["internal_path"])
            if path.is_symlink():
                raise ContractError(*errors["integrity"])
            resolved = path.resolve(strict=True)
            admitted_root = (self.path.parent / "admitted-assets").resolve(strict=True)
            if admitted_root not in resolved.parents or not resolved.is_file():
                raise ContractError(*errors["integrity"])
            expected_size = int(row["size"])
            actual_size = resolved.stat().st_size
            if expected_size > MAX_SOURCE_PREVIEW_BYTES or actual_size > MAX_SOURCE_PREVIEW_BYTES:
                raise ContractError(*errors["too_large"])
            if expected_size < 0 or actual_size != expected_size:
                raise ContractError(*errors["integrity"])
            with resolved.open("rb") as stream:
                payload = stream.read(MAX_SOURCE_PREVIEW_BYTES + 1)
            if len(payload) > MAX_SOURCE_PREVIEW_BYTES:
                raise ContractError(*errors["too_large"])
            if (len(payload) != expected_size
                    or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), row["sha256"])):
                raise ContractError(*errors["integrity"])
            return payload, media_type, row["sha256"]
        except ContractError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ContractError(*errors["integrity"]) from exc

    def source_preview(self, actor: dict, source_id: str) -> tuple[bytes, str]:
        """Return a verified admitted derivative, never an uploaded original."""
        payload, media_type, _ = self._verified_source_derivative(
            actor, source_id, operation="render")
        return payload, media_type

    def source_analysis(self, actor: dict, source_id: str) -> tuple[bytes, str, str]:
        """Return a verified admitted derivative under analyze rights."""
        return self._verified_source_derivative(actor, source_id, operation="analyze")

    def audio_original_for_analysis(
        self, actor: dict, source_id: str
    ) -> tuple[Path, OriginalSourceDescriptor, str]:
        """Return one verified server-held WAV path under analyze rights."""
        row = self.source_rows(actor, [source_id], operation="analyze")[0]
        _, _, derivative_sha256 = self._verified_source_derivative(actor, source_id, "analyze")
        if not hmac.compare_digest(derivative_sha256, str(row["sha256"])):
            raise ContractError("LOCAL_ASR_SOURCE_INTEGRITY_FAILED", 409)
        originals = self._verified_originals([row])
        if len(originals) != 1:
            raise ContractError("LOCAL_ASR_SOURCE_NOT_AUDIO", 415)
        try:
            provenance = json.loads(row["provenance_json"])
            descriptor = OriginalSourceDescriptor.model_validate(provenance["original"])
            original = originals[0]
            if (descriptor.media_type != "audio/wav"
                    or descriptor.transformation != "pcm16-waveform-png-v1"
                    or original["media_type"] != descriptor.media_type
                    or original["transformation"] != descriptor.transformation):
                raise ContractError("LOCAL_ASR_SOURCE_NOT_AUDIO", 415)
            return Path(original["internal_path"]), descriptor, str(row["sha256"])
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError("SOURCE_ORIGINAL_INTEGRITY_FAILED", 409) from exc

    def list_sources(self, actor: dict) -> list[SourceAsset]:
        with self._connect() as db:
            if actor["kind"] == "agent":
                rows = db.execute(
                    """SELECT s.* FROM source_assets s JOIN agent_scope_sources a ON a.source_id=s.id
                       WHERE a.actor_id=? AND s.owner_id=? ORDER BY s.created_at DESC""",
                    (actor["id"], self._owner_for(actor)),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM source_assets WHERE owner_id=? ORDER BY created_at DESC",
                                  (self._owner_for(actor),)).fetchall()
        return [SourceAsset(id=row["id"], sha256=row["sha256"], media_type=row["media_type"], size=row["size"],
                            provenance=json.loads(row["provenance_json"]), rights=json.loads(row["rights_json"]),
                            allowed_operations=json.loads(row["operations_json"]), created_at=row["created_at"])
                for row in rows]

    def create_project(self, actor: dict, document: SceneProjectV2) -> ProjectRevision:
        if actor["kind"] != "human":
            raise ContractError("HUMAN_SESSION_REQUIRED", 403)
        self.source_rows(actor, document.sources)
        project_id, stamp = "prj_" + uuid4().hex, now_iso()
        payload, digest = document.model_dump(mode="json"), canonical_hash(document.model_dump(mode="json"))
        with self.lock, self._connect() as db:
            db.execute("INSERT INTO projects VALUES(?,?,?)", (project_id, self._owner_for(actor), stamp))
            db.execute("INSERT INTO project_revisions VALUES(?,?,?,?,?,?)",
                       (project_id, 1, digest, json.dumps(payload), actor["id"], stamp))
        return ProjectRevision(project_id=project_id, revision=1, document_hash=digest, document=document, created_at=stamp)

    def get_revision(self, actor: dict, project_id: str, revision: int | None = None) -> ProjectRevision:
        if actor["kind"] == "agent":
            if project_id != actor.get("project_id"):
                raise ContractError("PROJECT_REVISION_NOT_FOUND", 404)
            if revision is None:
                revision = actor.get("revision")
            elif revision != actor.get("revision"):
                raise ContractError("PROJECT_REVISION_NOT_FOUND", 404)
        owner = self._owner_for(actor)
        clause, args = ("AND r.revision=?", (project_id, owner, revision)) if revision else ("ORDER BY r.revision DESC LIMIT 1", (project_id, owner))
        with self._connect() as db:
            row = db.execute(
                f"SELECT r.* FROM project_revisions r JOIN projects p ON p.id=r.project_id WHERE r.project_id=? AND p.owner_id=? {clause}", args
            ).fetchone()
        if not row:
            raise ContractError("PROJECT_REVISION_NOT_FOUND", 404)
        return ProjectRevision(project_id=row["project_id"], revision=row["revision"], document_hash=row["document_hash"],
                               document=SceneProjectV2.model_validate_json(row["document_json"]), created_at=row["created_at"])

    def revise_project(self, actor: dict, project_id: str, base_revision: int, document: SceneProjectV2) -> ProjectRevision:
        if actor["kind"] != "human":
            raise ContractError("HUMAN_SESSION_REQUIRED", 403)
        self.source_rows(actor, document.sources)
        payload, stamp = document.model_dump(mode="json"), now_iso()
        digest = canonical_hash(payload)
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute("SELECT owner_id FROM projects WHERE id=?", (project_id,)).fetchone()
            latest = db.execute("SELECT MAX(revision) FROM project_revisions WHERE project_id=?", (project_id,)).fetchone()[0]
            if not project or project["owner_id"] != self._owner_for(actor):
                raise ContractError("PROJECT_NOT_FOUND", 404)
            if latest != base_revision:
                raise ContractError("REVISION_CONFLICT")
            revision = base_revision + 1
            db.execute("INSERT INTO project_revisions VALUES(?,?,?,?,?,?)",
                       (project_id, revision, digest, json.dumps(payload), actor["id"], stamp))
        return ProjectRevision(project_id=project_id, revision=revision, document_hash=digest, document=document, created_at=stamp)

    def list_projects(self, actor: dict) -> list[dict]:
        owner = self._owner_for(actor)
        with self._connect() as db:
            if actor["kind"] == "agent":
                rows = db.execute(
                    """SELECT r.* FROM project_revisions r JOIN projects p ON p.id=r.project_id
                       WHERE p.owner_id=? AND r.project_id=? AND r.revision=?""",
                    (owner, actor["project_id"], actor["revision"]),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT r.* FROM project_revisions r JOIN projects p ON p.id=r.project_id
                       WHERE p.owner_id=? AND r.revision=(SELECT MAX(r2.revision) FROM project_revisions r2 WHERE r2.project_id=r.project_id)
                       ORDER BY r.created_at DESC""", (owner,)).fetchall()
        result = []
        for row in rows:
            document = json.loads(row["document_json"])
            result.append({"project_id": row["project_id"], "revision": row["revision"],
                           "document_hash": row["document_hash"], "slug": document["slug"],
                           "title": document["title"], "created_at": row["created_at"]})
        return result

    def create_plan(self, actor: dict, data: dict) -> RenderPlan:
        revision = self.get_revision(actor, data["project_id"], data["revision"])
        with self._connect() as db:
            latest = db.execute("SELECT MAX(revision) FROM project_revisions WHERE project_id=?",
                                (data["project_id"],)).fetchone()[0]
        if latest != data["revision"]:
            raise ContractError("STALE_PROJECT_REVISION")
        document = revision.document
        if data["locale"] not in {item.locale for item in document.tracks} or data["profile"] not in {item.name for item in document.output_profiles}:
            raise ContractError("PLAN_TARGET_NOT_DECLARED", 422)
        track = next(item for item in document.tracks if item.locale == data["locale"])
        if data["narration_mode"] == "local_kokoro_cpu":
            self._validate_local_voice_target(data["locale"], track.narration, track.voice)
        rows = self.source_rows(actor, document.sources)
        asset_hashes = {row["id"]: row["sha256"] for row in rows}
        originals = self._verified_originals(rows)
        original_hashes = {row["source_id"]: row["sha256"] for row in originals}
        max_duration = data.pop("max_duration_seconds")
        max_output = data.pop("max_output_bytes")
        resource_modes = {"narration": "local-silent", "render": "local-ffmpeg"}
        if data["narration_mode"] == "local_kokoro_cpu":
            resource_modes = {
                "narration": "local-kokoro-cpu",
                "render": "local-ffmpeg",
                "local_voice": self._local_voice_binding(),
                "local_voice_voice": PILOT_VOICE,
                "local_voice_language": PILOT_LANGUAGE,
            }
        limits = {"max_duration_seconds": max_duration, "max_output_bytes": max_output}
        if any(scene.visual_3d is not None for scene in document.scenes):
            try:
                resource_modes["scene3d"] = runtime_binding()
            except Scene3DUnavailable as exc:
                raise ContractError("THREED_RENDERER_UNAVAILABLE", 503) from exc
            limits["max_scene3d_frames"] = MAX_SCENE3D_FRAMES
            limits["max_scene3d_frame_bytes"] = min(max_output, MAX_SCENE3D_FRAME_BYTES)
        base = {**data, "project_hash": revision.document_hash, "asset_hashes": asset_hashes,
                "provider_resource_modes": resource_modes, "limits": limits}
        if original_hashes:
            base["original_hashes"] = original_hashes
        plan_hash, stamp = canonical_hash(base), now_iso()
        plan = RenderPlan(id="plan_" + uuid4().hex, plan_hash=plan_hash, created_at=stamp, **base)
        with self.lock, self._connect() as db:
            try:
                db.execute("INSERT INTO render_plans VALUES(?,?,?,?,?,?,?)",
                           (plan.id, plan.project_id, plan.revision, plan.plan_hash, plan.model_dump_json(), actor["id"], stamp))
            except sqlite3.IntegrityError:
                row = db.execute("SELECT payload_json FROM render_plans WHERE plan_hash=?", (plan_hash,)).fetchone()
                return RenderPlan.model_validate_json(row[0])
        return plan

    @staticmethod
    def _validate_local_voice_target(locale: str, narration: str,
                                     voice: str | None) -> None:
        if locale != "en":
            raise ContractError("LOCAL_VOICE_LOCALE_UNSUPPORTED", 422)
        if voice not in {None, PILOT_VOICE}:
            raise ContractError("LOCAL_VOICE_VOICE_NOT_ALLOWED", 422)
        if not narration.strip() or len(narration) > MAX_TEXT_CHARS:
            raise ContractError("LOCAL_VOICE_TEXT_INVALID", 422)

    def _local_voice_binding(self) -> str:
        if self.local_voice is None:
            raise ContractError("LOCAL_VOICE_UNAVAILABLE", 503)
        try:
            _, binding = self.local_voice.binding()
        except LocalVoiceError as exc:
            integrity_codes = {
                "LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED",
                "LOCAL_VOICE_RESOURCE_LINK_REJECTED",
                "LOCAL_VOICE_RESOURCE_OUTSIDE_ROOT",
                "LOCAL_VOICE_BINDING_MISMATCH",
            }
            code = ("LOCAL_VOICE_INTEGRITY_FAILED"
                    if exc.code in integrity_codes else "LOCAL_VOICE_UNAVAILABLE")
            raise ContractError(code, 409 if code.endswith("INTEGRITY_FAILED") else 503) from exc
        if not isinstance(binding, str) or re.fullmatch(r"[0-9a-f]{64}", binding) is None:
            raise ContractError("LOCAL_VOICE_INTEGRITY_FAILED", 409)
        return binding

    def get_plan(self, actor: dict, plan_id: str) -> RenderPlan:
        owner = self._owner_for(actor)
        with self._connect() as db:
            row = db.execute("SELECT rp.payload_json,p.owner_id FROM render_plans rp JOIN projects p ON p.id=rp.project_id WHERE rp.id=?", (plan_id,)).fetchone()
        if not row or row["owner_id"] != owner:
            raise ContractError("PLAN_NOT_FOUND", 404)
        plan = RenderPlan.model_validate_json(row["payload_json"])
        self._verify_plan(plan)
        if not self._agent_scope_matches(actor, plan.project_id, plan.revision):
            raise ContractError("PLAN_NOT_FOUND", 404)
        return plan

    @staticmethod
    def _verify_plan(plan: RenderPlan) -> None:
        payload = plan.model_dump(mode="json", exclude={"id", "plan_hash", "created_at"})
        if not hmac.compare_digest(plan.plan_hash, canonical_hash(payload)):
            raise ContractError("PLAN_INTEGRITY_FAILED", 409)

    def _grant_signature(self, grant_id: str, plan_id: str, agent_id: str, expires_at: str) -> str:
        return hmac.new(self.signing_key, f"{grant_id}|{plan_id}|{agent_id}|render|{expires_at}".encode(), hashlib.sha256).hexdigest()

    def authorize(self, actor: dict, plan_id: str, agent_id: str, ttl: int) -> str:
        if actor["kind"] != "human":
            raise ContractError("HUMAN_AUTHORIZATION_REQUIRED", 403)
        plan = self.get_plan(actor, plan_id)
        with self._connect() as db:
            target = db.execute(
                """SELECT a.owner_id,a.kind,s.project_id,s.revision,
                          EXISTS(SELECT 1 FROM sessions x WHERE x.actor_id=a.id
                                 AND x.revoked_at IS NULL AND x.expires_at>?) AS active_session
                   FROM actors a LEFT JOIN agent_scopes s ON s.actor_id=a.id WHERE a.id=?""",
                (now_iso(), agent_id),
            ).fetchone()
        if (not target or target["kind"] != "agent" or target["owner_id"] != actor["id"]
                or not target["active_session"]):
            raise ContractError("AGENT_NOT_FOUND", 404)
        if target["project_id"] != plan.project_id or target["revision"] != plan.revision:
            raise ContractError("AGENT_SCOPE_MISMATCH", 403)
        grant_id, stamp = "grant_" + uuid4().hex, now_iso()
        expiry = (utc_now() + timedelta(seconds=ttl)).isoformat()
        signature = self._grant_signature(grant_id, plan_id, agent_id, expiry)
        with self.lock, self._connect() as db:
            db.execute("INSERT INTO production_grants VALUES(?,?,?,?,?,NULL,NULL,?,?,?)",
                       (grant_id, plan_id, agent_id, "render", expiry, signature, actor["id"], stamp))
        return grant_id

    def revoke_grant(self, actor: dict, grant_id: str) -> None:
        if actor["kind"] != "human":
            raise ContractError("HUMAN_AUTHORIZATION_REQUIRED", 403)
        with self.lock, self._connect() as db:
            row = db.execute("SELECT g.id,p.owner_id FROM production_grants g JOIN render_plans r ON r.id=g.plan_id JOIN projects p ON p.id=r.project_id WHERE g.id=?", (grant_id,)).fetchone()
            if not row or row["owner_id"] != actor["id"]:
                raise ContractError("GRANT_NOT_FOUND", 404)
            db.execute("UPDATE production_grants SET revoked_at=? WHERE id=? AND consumed_at IS NULL", (now_iso(), grant_id))

    def enqueue_approved(self, actor: dict, plan_id: str, idempotency_key: str) -> tuple[ProductionJob, bool]:
        if actor["kind"] != "agent":
            raise ContractError("AGENT_CLIENT_REQUIRED", 403)
        request_hash = canonical_hash({"actor_id": actor["id"], "plan_id": plan_id})
        stamp = now_iso()
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM production_jobs WHERE actor_id=? AND idempotency_key=?", (actor["id"], idempotency_key)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ContractError("IDEMPOTENCY_CONFLICT")
                return self._job_from_row(existing), False
            plan_row = db.execute("SELECT payload_json FROM render_plans WHERE id=?", (plan_id,)).fetchone()
            if not plan_row:
                raise ContractError("PLAN_NOT_FOUND", 404)
            plan = RenderPlan.model_validate_json(plan_row[0])
            self._verify_plan(plan)
            if not self._agent_scope_matches(actor, plan.project_id, plan.revision):
                raise ContractError("PLAN_NOT_FOUND", 404)
            project = db.execute("SELECT owner_id FROM projects WHERE id=?", (plan.project_id,)).fetchone()
            if not project or project["owner_id"] != actor["owner_id"]:
                raise ContractError("PLAN_NOT_FOUND", 404)
            latest = db.execute("SELECT MAX(revision) FROM project_revisions WHERE project_id=?", (plan.project_id,)).fetchone()[0]
            if latest != plan.revision:
                raise ContractError("STALE_PROJECT_REVISION")
            grant = db.execute(
                "SELECT * FROM production_grants WHERE plan_id=? AND agent_id=? AND consumed_at IS NULL AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (plan_id, actor["id"]),
            ).fetchone()
            if not grant or datetime.fromisoformat(grant["expires_at"]) <= utc_now():
                raise ContractError("APPROVAL_REQUIRED", 403)
            expected = self._grant_signature(grant["id"], grant["plan_id"], grant["agent_id"], grant["expires_at"])
            if not hmac.compare_digest(expected, grant["signature"]):
                raise ContractError("APPROVAL_INTEGRITY_FAILED", 403)
            updated = db.execute("UPDATE production_grants SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (stamp, grant["id"]))
            if updated.rowcount != 1:
                raise ContractError("APPROVAL_ALREADY_CONSUMED")
            job_id = "job_" + uuid4().hex
            db.execute(
                "INSERT INTO production_jobs VALUES(?,?,?,?,?,?,?,?,?,0,NULL,'[]','[]',?,?)",
                (job_id, actor["id"], idempotency_key, request_hash, plan_id, plan.project_id, plan.revision, "queued", 0, stamp, stamp),
            )
            row = db.execute("SELECT * FROM production_jobs WHERE id=?", (job_id,)).fetchone()
            return self._job_from_row(row), True

    def claim_next(self) -> str | None:
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM production_jobs WHERE status='running'").fetchone():
                return None
            row = db.execute("SELECT id FROM production_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE production_jobs SET status='running',progress=1,updated_at=? WHERE id=? AND status='queued'", (now_iso(), row["id"]))
            return row["id"]

    def job_internal(self, job_id: str) -> tuple[ProductionJob, list[str]]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM production_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ContractError("JOB_NOT_FOUND", 404)
        return self._job_from_row(row), json.loads(row["internal_artifacts_json"])

    def job_for(self, actor: dict, job_id: str) -> ProductionJob:
        with self._connect() as db:
            row = db.execute("SELECT j.*,p.owner_id FROM production_jobs j JOIN projects p ON p.id=j.project_id WHERE j.id=?", (job_id,)).fetchone()
        if (not row or row["owner_id"] != self._owner_for(actor)
                or (actor["kind"] == "agent" and (row["actor_id"] != actor["id"]
                    or not self._agent_scope_matches(actor, row["project_id"], row["revision"])))):
            raise ContractError("JOB_NOT_FOUND", 404)
        return self._job_from_row(row)

    def artifact_for(self, actor: dict, job_id: str, name: str) -> tuple[Path, ArtifactReceipt]:
        job = self.job_for(actor, job_id)
        if job.status != "complete" or Path(name).name != name:
            raise ContractError("ARTIFACT_NOT_FOUND", 404)
        _, internal = self.job_internal(job_id)
        matches = [(Path(path), receipt) for path, receipt in zip(internal, job.receipts, strict=True)
                   if receipt.name == name and Path(path).name == name]
        if len(matches) != 1 or not matches[0][0].is_file():
            raise ContractError("ARTIFACT_NOT_FOUND", 404)
        return matches[0]

    def _job_from_row(self, row: sqlite3.Row) -> ProductionJob:
        return ProductionJob(id=row["id"], project_id=row["project_id"], revision=row["revision"], plan_id=row["plan_id"],
                             status=row["status"], progress=row["progress"], error_code=row["error_code"],
                             receipts=[ArtifactReceipt.model_validate(v) for v in json.loads(row["receipts_json"])],
                             created_at=row["created_at"], updated_at=row["updated_at"])

    def update_job(self, job_id: str, *, status: str | None = None, progress: int | None = None,
                   error_code: str | None = None, artifacts: list[str] | None = None,
                   receipts: list[dict] | None = None) -> None:
        fields: dict[str, object] = {"updated_at": now_iso()}
        if status is not None: fields["status"] = status
        if progress is not None: fields["progress"] = progress
        if error_code is not None: fields["error_code"] = error_code
        if artifacts is not None: fields["internal_artifacts_json"] = json.dumps(artifacts)
        if receipts is not None: fields["receipts_json"] = json.dumps(receipts)
        with self.lock, self._connect() as db:
            db.execute(f"UPDATE production_jobs SET {','.join(k+'=?' for k in fields)} WHERE id=?", (*fields.values(), job_id))

    def complete_job(self, job_id: str, artifacts: list[str], receipts: list[dict]) -> None:
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,cancel_requested FROM production_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ContractError("JOB_NOT_FOUND", 404)
            if row["cancel_requested"] or row["status"] == "cancelled":
                db.execute("UPDATE production_jobs SET status='cancelled',error_code='CANCELLED',updated_at=? WHERE id=?",
                           (now_iso(), job_id))
                return
            if row["status"] != "running":
                raise ContractError("JOB_STATE_CONFLICT")
            db.execute("""UPDATE production_jobs SET status='complete',progress=100,
                       internal_artifacts_json=?,receipts_json=?,updated_at=? WHERE id=?""",
                       (json.dumps(artifacts), json.dumps(receipts), now_iso(), job_id))

    def cancellation_requested(self, job_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT cancel_requested,status FROM production_jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))

    def cancel(self, actor: dict, job_id: str) -> ProductionJob:
        self.job_for(actor, job_id)
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM production_jobs WHERE id=?", (job_id,)).fetchone()
            if row["status"] == "queued":
                db.execute("UPDATE production_jobs SET status='cancelled',cancel_requested=1,updated_at=? WHERE id=?", (now_iso(), job_id))
            elif row["status"] == "running":
                db.execute("UPDATE production_jobs SET cancel_requested=1,updated_at=? WHERE id=?", (now_iso(), job_id))
        return self.job_for(actor, job_id)

    def recover_interrupted(self) -> int:
        with self.lock, self._connect() as db:
            cursor = db.execute("UPDATE production_jobs SET status='failed',error_code='RESTART_INTERRUPTED',updated_at=? WHERE status='running'", (now_iso(),))
            return cursor.rowcount

    def plan_and_revision_internal(
        self, plan_id: str
    ) -> tuple[RenderPlan, ProjectRevision, list[sqlite3.Row], list[sqlite3.Row]]:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM render_plans WHERE id=?", (plan_id,)).fetchone()
            if not row: raise ContractError("PLAN_NOT_FOUND", 404)
            plan = RenderPlan.model_validate_json(row[0])
            self._verify_plan(plan)
            rev = db.execute("SELECT * FROM project_revisions WHERE project_id=? AND revision=?", (plan.project_id, plan.revision)).fetchone()
            sources = ([] if rev is None else
                       [db.execute("SELECT * FROM source_assets WHERE id=?", (sid,)).fetchone()
                        for sid in json.loads(rev["document_json"])["sources"]])
        if rev is None or any(source is None for source in sources):
            raise ContractError("PLAN_INTEGRITY_FAILED", 409)
        document_payload = json.loads(rev["document_json"])
        if (canonical_hash(document_payload) != rev["document_hash"]
                or rev["document_hash"] != plan.project_hash
                or any(plan.asset_hashes.get(source["id"]) != source["sha256"] for source in sources)):
            raise ContractError("PLAN_INTEGRITY_FAILED", 409)
        originals = self._verified_originals(sources, plan.original_hashes)
        revision = ProjectRevision(project_id=rev["project_id"], revision=rev["revision"], document_hash=rev["document_hash"],
                                   document=SceneProjectV2.model_validate_json(rev["document_json"]), created_at=rev["created_at"])
        expected_scene3d = plan.provider_resource_modes.get("scene3d")
        if any(scene.visual_3d is not None for scene in revision.document.scenes):
            try:
                current_scene3d = runtime_binding()
            except Scene3DUnavailable as exc:
                raise ContractError("THREED_RENDERER_UNAVAILABLE", 503) from exc
            if not expected_scene3d or not hmac.compare_digest(expected_scene3d, current_scene3d):
                raise ContractError("THREED_BUNDLE_MISMATCH", 409)
        if plan.narration_mode == "local_kokoro_cpu":
            track = next((item for item in revision.document.tracks
                          if item.locale == plan.locale), None)
            if track is None:
                raise ContractError("PLAN_INTEGRITY_FAILED", 409)
            self._validate_local_voice_target(plan.locale, track.narration, track.voice)
            modes = plan.provider_resource_modes
            if (modes.get("narration") != "local-kokoro-cpu"
                    or modes.get("render") != "local-ffmpeg"
                    or modes.get("local_voice_voice") != PILOT_VOICE
                    or modes.get("local_voice_language") != PILOT_LANGUAGE):
                raise ContractError("PLAN_INTEGRITY_FAILED", 409)
            expected_voice = modes.get("local_voice")
            current_voice = self._local_voice_binding()
            if (not expected_voice
                    or not hmac.compare_digest(expected_voice, current_voice)):
                raise ContractError("LOCAL_VOICE_BINDING_MISMATCH", 409)
        return plan, revision, sources, originals
