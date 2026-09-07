from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_ENV = Path(__file__).parents[1] / ".env"


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    host: str
    port: int
    allowed_asset_roots: tuple[Path, ...]
    tts_model: str
    tts_voice_fr: str
    tts_voice_en: str
    max_workers: int
    local_voice_runtime_root: Path | None = None

    @classmethod
    def load(cls) -> "Settings":
        env_path = Path(os.getenv("QUANTECH_VID_ENV_FILE", DEFAULT_ENV))
        load_dotenv(env_path, override=False)
        root = _resolved(os.getenv("QUANTECH_VID_ROOT", Path(__file__).parents[1]))
        data_dir = _resolved(os.getenv("QUANTECH_VID_DATA_DIR", root / "runtime"))
        raw_roots = os.getenv("QUANTECH_VID_ALLOWED_ASSET_ROOTS", str(root))
        roots = tuple(_resolved(item) for item in raw_roots.split(";") if item.strip())
        local_voice_root = os.getenv("QUANTECH_VID_LOCAL_VOICE_ROOT")
        settings = cls(
            root=root,
            data_dir=data_dir,
            host=os.getenv("QUANTECH_VID_HOST", "127.0.0.1"),
            port=int(os.getenv("QUANTECH_VID_PORT", "7476")),
            allowed_asset_roots=roots or (root,),
            tts_model=os.getenv("QUANTECH_VID_OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            tts_voice_fr=os.getenv("QUANTECH_VID_OPENAI_TTS_VOICE_FR", "alloy"),
            tts_voice_en=os.getenv("QUANTECH_VID_OPENAI_TTS_VOICE_EN", "alloy"),
            max_workers=max(1, int(os.getenv("QUANTECH_VID_MAX_CONCURRENT_RENDERS", "1"))),
            local_voice_runtime_root=(_resolved(local_voice_root) if local_voice_root else None),
        )
        settings.ensure_directories()
        return settings

    def ensure_directories(self) -> None:
        for name in ("captures", "tts-cache", "renders", "projects", "tmp"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

    def require_loopback(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("QUANTECH_VID_HOST must remain a loopback address")

    def require_allowed_path(self, candidate: str | Path) -> Path:
        path = _resolved(candidate)
        if not any(path == root or root in path.parents for root in self.allowed_asset_roots):
            raise ValueError(f"Asset path is outside the allowed roots: {path}")
        return path
