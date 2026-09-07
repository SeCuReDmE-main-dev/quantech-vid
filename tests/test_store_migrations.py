import sqlite3

import pytest

from quantech_vid.store_migrations import (
    GLB_ORIGINALS_SQL, LEGACY_ORIGINALS_SQL, allow_supported_originals,
)


def legacy(tmp_path):
    path = tmp_path / "production.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE source_assets(id TEXT PRIMARY KEY)")
    db.execute(LEGACY_ORIGINALS_SQL)
    db.execute("INSERT INTO source_assets VALUES('fixture')")
    original = ('fixture', 'private-test-original', 'a' * 64, 'fixture.md', 5, 'text/markdown', 'literal-text-preview-v1')
    db.execute("INSERT INTO source_originals VALUES(?,?,?,?,?,?,?)", original)
    db.commit()
    return path, db, original


def test_original_migration_preserves_rows_and_recoverable_backup(tmp_path):
    path, db, original = legacy(tmp_path)
    try:
        allow_supported_originals(db, path)
        assert db.execute("SELECT * FROM source_originals").fetchone() == original
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        backups = list(tmp_path.glob("*.bak"))
        assert len(backups) == 1
        backup = sqlite3.connect(backups[0])
        try:
            assert backup.execute("SELECT * FROM source_originals").fetchone() == original
            assert 'glb-four-view' not in backup.execute("SELECT sql FROM sqlite_master WHERE name='source_originals'").fetchone()[0]
        finally:
            backup.close()
        db.execute("UPDATE source_originals SET transformation='glb-four-view-png-v1'")
        db.commit()
        db.execute("UPDATE source_originals SET transformation='pcm16-waveform-png-v1'")
        db.commit()
        allow_supported_originals(db, path)
        assert len(list(tmp_path.glob("*.bak"))) == 1
    finally:
        db.close()


def test_custom_schema_is_not_rewritten(tmp_path):
    path, db, original = legacy(tmp_path)
    try:
        db.execute("CREATE INDEX custom_original_name ON source_originals(name)")
        with pytest.raises(RuntimeError, match='SOURCE_SCHEMA_MIGRATION_REQUIRED'):
            allow_supported_originals(db, path)
        assert db.execute("SELECT * FROM source_originals").fetchone() == original
        assert not list(tmp_path.glob("*.bak"))
    finally:
        db.close()


def test_failed_migration_rolls_back_without_losing_originals(tmp_path):
    path, db, original = legacy(tmp_path)
    try:
        db.execute("CREATE TABLE source_originals_audio_migration(occupied TEXT)")
        with pytest.raises(RuntimeError, match='SOURCE_SCHEMA_MIGRATION_FAILED'):
            allow_supported_originals(db, path)
        assert db.execute("SELECT * FROM source_originals").fetchone() == original
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert len(list(tmp_path.glob("*.bak"))) == 1
    finally:
        db.close()


def test_known_glb_schema_is_the_only_other_accepted_starting_shape(tmp_path):
    path = tmp_path / "production.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE source_assets(id TEXT PRIMARY KEY)")
    db.execute(GLB_ORIGINALS_SQL)
    db.commit()
    try:
        allow_supported_originals(db, path)
        sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='source_originals'"
        ).fetchone()[0]
        assert "pcm16-waveform-png-v1" in sql
        assert len(list(tmp_path.glob("*.bak"))) == 1
    finally:
        db.close()
