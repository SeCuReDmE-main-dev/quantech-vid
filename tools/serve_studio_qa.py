"""Run the real studio against disposable local data, never the user's .env/runtime.

Build apps/studio first. The generated operator code is a local QA credential only.
No provider calls, upload to remote services, or production identities are involved.
"""
from __future__ import annotations

import argparse
import secrets
import tempfile
from pathlib import Path

import uvicorn

from quantech_vid.api import APIConfig, create_app
from quantech_vid.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--local-voice-root", type=Path, default=None,
                        help="Explicit isolated Kokoro runtime for synthetic CPU QA; never downloads resources")
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error("port must be 1024-65535")
    root = Path(__file__).resolve().parents[1]
    if not (root / "apps/studio/dist/index.html").is_file():
        parser.error("Build the React studio first: cd apps/studio && npm ci && npm run build")
    data = Path(tempfile.mkdtemp(prefix="quantech-studio-qa-"))
    settings = Settings(root=root, data_dir=data, host="127.0.0.1", port=arguments.port,
                        allowed_asset_roots=(data,), tts_model="disabled", tts_voice_fr="disabled",
                        tts_voice_en="disabled", max_workers=1,
                        local_voice_runtime_root=arguments.local_voice_root)
    settings.ensure_directories()
    code = secrets.token_urlsafe(24)
    origin = f"http://127.0.0.1:{arguments.port}"
    config = APIConfig(expected_host=f"127.0.0.1:{arguments.port}", allowed_origin=origin,
                       pairing_code=code, signing_key=secrets.token_bytes(32))
    app = create_app(settings, config)
    print(f"QA Studio: {origin}", flush=True)
    print(f"Disposable data: {data}", flush=True)
    print(f"One-time QA operator code: {code}", flush=True)
    print("Only synthetic or explicitly selected QA sources. Stop with Ctrl+C; files remain for inspection.", flush=True)
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
