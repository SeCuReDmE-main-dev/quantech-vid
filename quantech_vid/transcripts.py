from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TranscriptSegment(BaseModel):
    """A human-authored, source-linked caption interval.

    Text is preserved verbatim in the canonical project. Subtitle serialization uses a
    separately escaped presentation value so caption syntax cannot create extra cues.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    text: str = Field(min_length=1, max_length=1000)
    source_asset_id: str = Field(pattern=r"^src_[a-f0-9]{32}$")
    source_locator: str = Field(min_length=1, max_length=160)

    @field_validator("text", "source_locator")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not any(
            not character.isspace()
            and not unicodedata.category(character).startswith("C")
            for character in value
        ):
            raise ValueError("transcript text and source locator require visible content")
        return value

    @model_validator(mode="after")
    def finite_interval(self) -> "TranscriptSegment":
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError("transcript times must be finite")
        if self.start >= self.end:
            raise ValueError("transcript segment start must precede end")
        if timestamp_milliseconds(self.end) <= timestamp_milliseconds(self.start):
            raise ValueError("transcript interval must span at least one exported millisecond")
        return self


def timestamp_milliseconds(seconds: float) -> int:
    """Match caption half-up millisecond rounding without binary tie ambiguity."""

    return int((Decimal(str(seconds)) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validate_track_segments(
    segments: list[TranscriptSegment], *, duration: float, source_ids: set[str]
) -> None:
    if len({segment.id for segment in segments}) != len(segments):
        raise ValueError("transcript segment ids must be unique within each track")
    previous_end = 0.0
    for index, segment in enumerate(segments):
        if segment.source_asset_id not in source_ids:
            raise ValueError("transcript segment sources must be declared")
        if segment.end > duration:
            raise ValueError("transcript segments must end within project duration")
        if index and segment.start < previous_end:
            raise ValueError("transcript segments must be ordered and non-overlapping")
        previous_end = segment.end


_SPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def subtitle_text(text: str) -> str:
    """Return one inert cue line while leaving the canonical text untouched."""

    single_line = _SPACE.sub(" ", _CONTROL.sub("\ufffd", text)).strip()
    return (single_line.replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;"))


def build_transcript_sidecar(
    *, project_id: str, revision: int, project_hash: str, locale: str,
    segments: Iterable[TranscriptSegment], source_hashes: dict[str, str]
) -> dict:
    entries = []
    for segment in segments:
        entries.append({
            "id": segment.id,
            "start": segment.start,
            "end": segment.end,
            "text_sha256": hashlib.sha256(segment.text.encode("utf-8")).hexdigest(),
            "source_asset_id": segment.source_asset_id,
            "source_sha256": source_hashes[segment.source_asset_id],
            "source_locator": segment.source_locator,
        })
    return {
        "schema_version": "quantech.transcript-provenance.v1",
        "project": {"id": project_id, "revision": revision, "sha256": project_hash},
        "locale": locale,
        "method": "manual",
        "segments": entries,
        "limitations": {
            "automatic_speech_recognition_performed": False,
            "independent_verification": False,
        },
    }
