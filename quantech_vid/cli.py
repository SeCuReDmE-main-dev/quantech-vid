from __future__ import annotations

import argparse
import asyncio
import json
import sys
import zipfile
from pathlib import Path

import httpx
import imageio_ffmpeg

from .capture import capture_site
from .config import Settings
from .renderer import render_project, verify_media
from .schemas import CaptureRequest, load_manifest


def doctor(settings: Settings) -> int:
    checks = {
        "loopback": settings.host in {"127.0.0.1", "localhost", "::1"},
        "ffmpeg": Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file(),
        "data_dir": settings.data_dir.is_dir(),
        "allowed_roots": all(path.is_dir() for path in settings.allowed_asset_roots),
    }
    try:
        response = httpx.get(f"http://{settings.host}:{settings.port}/api/v1/health", timeout=2)
        checks["studio"] = response.status_code == 200
    except httpx.HTTPError:
        checks["studio"] = False
    print(json.dumps(checks, indent=2))
    return 0 if all(value for key, value in checks.items() if key != "studio") else 1


def validate(settings: Settings, manifest_path: Path) -> int:
    try:
        path = settings.require_allowed_path(manifest_path)
        manifest = load_manifest(path)
        for scene in manifest.scenes:
            settings.require_allowed_path(path.parent / scene.asset)
            if not (path.parent / scene.asset).is_file():
                raise FileNotFoundError(scene.asset)
        print(json.dumps({"valid": True, "slug": manifest.slug, "duration": manifest.duration}, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1


def local_render(settings: Settings, args: argparse.Namespace) -> int:
    path = settings.require_allowed_path(args.manifest)
    manifest = load_manifest(path)
    output = Path(args.output).resolve()
    artifacts = render_project(
        settings, path, manifest, args.locale, args.profile, output,
        "silent" if args.silent else "openai", progress=lambda value: print(f"progress={value}"),
    )
    print(json.dumps({"artifacts": [str(item) for item in artifacts]}, indent=2))
    return 0


def package_render(directory: Path, output: Path) -> int:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.glob("*")):
            if path.is_file():
                archive.write(path, path.name)
    print(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantech-vid")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("pairing-code", help="Issue a one-time local studio code valid for ten minutes; never share it with agents")
    validate_parser = sub.add_parser("validate-project")
    validate_parser.add_argument("manifest", type=Path)
    capture_parser = sub.add_parser("capture-site")
    capture_parser.add_argument("url")
    capture_parser.add_argument("--width", type=int, default=1440)
    capture_parser.add_argument("--height", type=int, default=900)
    render_parser = sub.add_parser("render-promo")
    render_parser.add_argument("manifest", type=Path)
    render_parser.add_argument("--locale", choices=["fr", "en"], required=True)
    render_parser.add_argument("--profile", required=True)
    render_parser.add_argument("--output", required=True)
    render_parser.add_argument("--silent", action="store_true")
    status_parser = sub.add_parser("job-status")
    status_parser.add_argument("job_id")
    verify_parser = sub.add_parser("verify-render")
    verify_parser.add_argument("path", type=Path)
    verify_parser.add_argument("--width", type=int, required=True)
    verify_parser.add_argument("--height", type=int, required=True)
    verify_parser.add_argument("--fps", type=int, default=30)
    package_parser = sub.add_parser("package-render")
    package_parser.add_argument("directory", type=Path)
    package_parser.add_argument("output", type=Path)
    sub.add_parser("status")
    analyze_parser = sub.add_parser("analyze-assets")
    analyze_parser.add_argument("--root", type=Path, required=True)
    analyze_parser.add_argument("--limit", type=int, default=10)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.load()
    settings.require_loopback()
    if args.command == "pairing-code":
        from .api import _signing_key
        from .production_store import ProductionStore
        store = ProductionStore(settings.data_dir / "production.sqlite3", _signing_key(settings.data_dir))
        print("One-time local studio code (10 minutes; never commit, publish or share with an agent):")
        print(store.issue_pairing_code())
        return
    if args.command in {"doctor", "status"}:
        raise SystemExit(doctor(settings))
    if args.command == "validate-project":
        raise SystemExit(validate(settings, args.manifest))
    if args.command == "capture-site":
        request = CaptureRequest(url=args.url, width=args.width, height=args.height)
        print(json.dumps(asyncio.run(capture_site(settings, request)), indent=2))
        return
    if args.command == "render-promo":
        raise SystemExit(local_render(settings, args))
    if args.command == "job-status":
        response = httpx.get(f"http://{settings.host}:{settings.port}/api/v1/renders/{args.job_id}")
        print(json.dumps(response.json(), indent=2))
        return
    if args.command == "verify-render":
        result = verify_media(args.path, args.width, args.height, args.fps)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["passed"] else 1)
    if args.command == "package-render":
        raise SystemExit(package_render(args.directory, args.output))
    if args.command == "analyze-assets":
        images = [path for path in args.root.rglob("*") if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
        print(json.dumps({"root": str(args.root), "assets": [str(path) for path in images[: args.limit]]}, indent=2))


if __name__ == "__main__":
    main()
