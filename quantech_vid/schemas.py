from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

from .scene3d import Visual3DConfig
from .transcripts import TranscriptSegment


class Profile(BaseModel):
    name: str
    width: int = Field(ge=320, le=4096)
    height: int = Field(ge=320, le=4096)
    fps: int = Field(default=30, ge=12, le=60)


class Scene(BaseModel):
    id: str
    duration: float = Field(gt=0, le=60)
    title_fr: str
    body_fr: str = ""
    title_en: str
    body_en: str = ""
    notice: str = Field(default="", max_length=500, exclude_if=lambda value: not value)
    visual_3d: Visual3DConfig | None = Field(default=None, exclude_if=lambda value: value is None)
    asset: str
    fit: Literal["cover", "contain"] = "cover"

    def copy_for(self, locale: str) -> tuple[str, str]:
        if locale == "fr":
            return self.title_fr, self.body_fr
        return self.title_en, self.body_en


class LocaleTrack(BaseModel):
    locale: Literal["fr", "en"]
    title: str
    narration: str
    voice: str | None = None
    segments: list[TranscriptSegment] = Field(
        default_factory=list, max_length=128, exclude_if=lambda value: not value
    )


class ProjectManifest(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    slug: str
    title: str
    source_url: HttpUrl | None = None
    disclosure: str = "Narration generated with AI and reviewed by a human."
    locales: list[LocaleTrack]
    profiles: list[Profile]
    scenes: list[Scene]
    scene3d_binding: str | None = Field(default=None, exclude_if=lambda value: value is None)
    scene3d_frame_byte_limit: int | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def unique_contract(self) -> "ProjectManifest":
        if len({item.locale for item in self.locales}) != len(self.locales):
            raise ValueError("Locales must be unique")
        if len({item.name for item in self.profiles}) != len(self.profiles):
            raise ValueError("Profiles must be unique")
        if len({item.id for item in self.scenes}) != len(self.scenes):
            raise ValueError("Scene ids must be unique")
        return self

    @property
    def duration(self) -> float:
        return sum(scene.duration for scene in self.scenes)


class ValidateProjectRequest(BaseModel):
    manifest_path: str


class CaptureRequest(BaseModel):
    url: HttpUrl
    width: int = Field(default=1440, ge=320, le=4096)
    height: int = Field(default=900, ge=320, le=4096)
    full_page: bool = True
    record_seconds: float = Field(default=0, ge=0, le=30)


class NarrationRequest(BaseModel):
    locale: Literal["fr", "en"]
    text: str = Field(min_length=1, max_length=8000)
    voice: str | None = None
    model: str | None = None
    force: bool = False


class RenderRequest(BaseModel):
    manifest_path: str
    locale: Literal["fr", "en"]
    profile: str
    narration_mode: Literal["openai", "silent"] = "openai"


class JobRecord(BaseModel):
    id: str
    status: Literal["queued", "running", "complete", "failed", "cancelled"]
    progress: int = Field(ge=0, le=100)
    request: dict
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: str
    updated_at: str


def load_manifest(path: Path) -> ProjectManifest:
    return ProjectManifest.model_validate_json(path.read_text(encoding="utf-8"))
