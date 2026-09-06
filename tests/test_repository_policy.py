import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("repository_policy", Path(__file__).parents[1] / "tools/check_repository_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def test_current_manifests_agree():
    assert policy.manifest_errors(Path(__file__).parents[1]) == []


def test_dependency_drift_is_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies=["Pillow==12.3.0"]\n[project.optional-dependencies]\ndev=["pytest==9.0.3"]\n')
    (tmp_path / "requirements-video.txt").write_text("Pillow==11.3.0\npytest==9.0.3\n")
    assert "Python manifests disagree" in policy.manifest_errors(tmp_path)
