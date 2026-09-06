"""Offline repository guardrails; diagnostics never print detected secret values."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tomllib


def manifest_errors(root: Path) -> list[str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = project["dependencies"] + project["optional-dependencies"]["dev"]
    requirements = [line.strip() for line in (root / "requirements-video.txt").read_text().splitlines()
                    if line.strip() and not line.startswith("#")]
    errors = []
    if sorted(map(str.lower, declared)) != sorted(map(str.lower, requirements)):
        errors.append("Python manifests disagree")
    if any("==" not in item for item in declared):
        errors.append("Direct Python dependencies must be exact pins")
    if any(item.lower().startswith("moviepy") for item in declared):
        errors.append("MoviePy must not return to the direct FFmpeg pipeline")
    return errors


def scan_secrets(root: Path) -> tuple[int, list[str]]:
    # Git-tracked files only. No --all-files, no baseline suppressions, no network validation.
    result = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "scan", "--no-verify", str(root)],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=120, check=True,
    )
    findings = json.loads(result.stdout)["results"]
    return sum(len(items) for items in findings.values()), sorted(findings)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = manifest_errors(root)
    if errors:
        for error in errors:
            print(error)
        return 1
    count, paths = scan_secrets(root)
    if count:
        print(f"Secret scan blocked: {count} potential findings in {len(paths)} tracked files.")
        for path in paths:
            print(path)
        return 1
    print("Python manifests agree; offline tracked-file secret scan: no findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
