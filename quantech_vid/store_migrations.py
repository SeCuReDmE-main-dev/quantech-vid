from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from uuid import uuid4


ORIGINALS_COLUMNS = ("source_id", "internal_path", "sha256", "name", "size", "media_type", "transformation")
LEGACY_ORIGINALS_SQL = """CREATE TABLE source_originals (
    source_id TEXT PRIMARY KEY, internal_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    transformation TEXT NOT NULL CHECK(transformation IN ('literal-text-preview-v1','rgb-png-v1')),
    FOREIGN KEY(source_id) REFERENCES source_assets(id) ON DELETE RESTRICT
)"""
GLB_ORIGINALS_SQL = """CREATE TABLE source_originals (
    source_id TEXT PRIMARY KEY, internal_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    transformation TEXT NOT NULL CHECK(transformation IN
        ('literal-text-preview-v1','rgb-png-v1','glb-four-view-png-v1')),
    FOREIGN KEY(source_id) REFERENCES source_assets(id) ON DELETE RESTRICT
)"""


def allow_supported_originals(db: sqlite3.Connection, database_path: Path) -> None:
    """Upgrade only known legacy/GLB source tables and retain a private backup."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='source_originals'").fetchone()
    if row is None:
        raise RuntimeError("SOURCE_SCHEMA_MIGRATION_REQUIRED")
    sql = row[0]
    if "'pcm16-waveform-png-v1'" in sql:
        return
    compact = re.sub(r"\s+", "", sql).lower()
    expected = {
        re.sub(r"\s+", "", LEGACY_ORIGINALS_SQL).lower(),
        re.sub(r"\s+", "", GLB_ORIGINALS_SQL).lower(),
    }
    columns = tuple(item[1] for item in db.execute("PRAGMA table_info(source_originals)"))
    custom_objects = db.execute("SELECT count(*) FROM sqlite_master WHERE tbl_name='source_originals' AND type IN ('trigger','index') AND sql IS NOT NULL").fetchone()[0]
    if compact not in expected or columns != ORIGINALS_COLUMNS or custom_objects or db.in_transaction:
        raise RuntimeError("SOURCE_SCHEMA_MIGRATION_REQUIRED")
    # No inbound relationships may be silently rewritten by a table rename.
    for table in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        name = table[0].replace('"', '""')
        if any(item[2] == "source_originals" for item in db.execute(f'PRAGMA foreign_key_list("{name}")')):
            raise RuntimeError("SOURCE_SCHEMA_MIGRATION_REQUIRED")
    backup_path = database_path.with_name(f"{database_path.name}.before-audio-v1-{uuid4().hex}.bak")
    with backup_path.open("xb"):
        pass
    os.chmod(backup_path, 0o600)
    backup = sqlite3.connect(backup_path)
    try:
        db.backup(backup)
    finally:
        backup.close()
    try:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT count(*) FROM source_originals").fetchone()[0]
        db.execute("""CREATE TABLE source_originals_audio_migration (
            source_id TEXT PRIMARY KEY, internal_path TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            transformation TEXT NOT NULL CHECK(transformation IN
                ('literal-text-preview-v1','rgb-png-v1','glb-four-view-png-v1','pcm16-waveform-png-v1')),
            FOREIGN KEY(source_id) REFERENCES source_assets(id) ON DELETE RESTRICT
        )""")
        db.execute("INSERT INTO source_originals_audio_migration SELECT * FROM source_originals")
        if db.execute("SELECT count(*) FROM source_originals_audio_migration").fetchone()[0] != count:
            raise RuntimeError("SOURCE_SCHEMA_MIGRATION_FAILED")
        db.execute("DROP TABLE source_originals")
        db.execute("ALTER TABLE source_originals_audio_migration RENAME TO source_originals")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("SOURCE_SCHEMA_MIGRATION_FAILED")
        db.commit()
    except Exception:
        db.rollback()
        raise RuntimeError("SOURCE_SCHEMA_MIGRATION_FAILED") from None


def allow_glb_originals(db: sqlite3.Connection, database_path: Path) -> None:
    """Compatibility name retained for callers; now admits the audio profile too."""
    allow_supported_originals(db, database_path)
