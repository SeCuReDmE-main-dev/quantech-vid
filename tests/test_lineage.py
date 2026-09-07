from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from quantech_vid.production_schemas import OriginalSourceDescriptor
from quantech_vid.production_store import ContractError, ProductionStore


def fixture_original(tmp_path: Path) -> tuple[ProductionStore, Path, OriginalSourceDescriptor]:
    content = b"# Synthetic original\n"
    path = tmp_path / "source-originals" / "fixture" / "original.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    store = ProductionStore(tmp_path / "production.sqlite3", b"synthetic-test-key-not-a-credential")
    descriptor = OriginalSourceDescriptor(name="fixture.md", size=len(content),
        sha256=hashlib.sha256(content).hexdigest(), media_type="text/markdown",
        transformation="literal-text-preview-v1")
    return store, path, descriptor


def test_original_read_failure_is_a_filtered_integrity_error(tmp_path, monkeypatch) -> None:
    store, path, descriptor = fixture_original(tmp_path)
    assert store._verify_original_file(path, descriptor) == path.resolve()

    def unavailable(_: Path) -> str:
        raise PermissionError("PRIVATE_RUNTIME_PATH_MUST_NOT_ESCAPE")

    monkeypatch.setattr(store, "_file_sha256", unavailable)
    with pytest.raises(ContractError, match="^SOURCE_ORIGINAL_INTEGRITY_FAILED$") as failure:
        store._verify_original_file(path, descriptor)
    assert failure.value.status == 409
    assert "PRIVATE_RUNTIME" not in str(failure.value)


def test_original_symlink_is_rejected_before_resolving(tmp_path, monkeypatch) -> None:
    store, path, descriptor = fixture_original(tmp_path)
    # The branch is portable to Windows hosts without symlink creation rights.
    # This is a simulated filesystem signal, not an OS-level sandbox proof.
    monkeypatch.setattr(type(path), "is_symlink", lambda candidate: candidate == path)
    monkeypatch.setattr(store, "_file_sha256", lambda _: pytest.fail("Must reject before reading"))
    with pytest.raises(ContractError, match="^SOURCE_ORIGINAL_INTEGRITY_FAILED$"):
        store._verify_original_file(path, descriptor)
