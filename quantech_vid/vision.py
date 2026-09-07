from __future__ import annotations

import io
import math
from typing import Literal, Protocol

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


MAX_RASTER_PIXELS = 16_000_000
MAX_OBSERVATIONS = 128
RASTER_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}


class VisionBoundaryError(ValueError):
    def __init__(self, code: str, status: int = 502) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class RasterRepresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["verified_raster_2d"] = "verified_raster_2d"
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    format: Literal["PNG", "JPEG", "WEBP"]
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    color_mode: str = Field(min_length=1, max_length=32)


def decode_raster(payload: bytes, media_type: str) -> RasterRepresentation:
    """Fully decode a bounded in-memory raster and bind its format to its media type."""
    expected_format = RASTER_FORMATS.get(media_type)
    if expected_format is None:
        raise VisionBoundaryError("SOURCE_ANALYSIS_UNSUPPORTED_MEDIA", 415)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.width, image.height
            if width < 1 or height < 1 or width * height > MAX_RASTER_PIXELS:
                raise VisionBoundaryError("SOURCE_ANALYSIS_TOO_LARGE", 413)
            detected_format = image.format
            color_mode = image.mode
            image.verify()
        with Image.open(io.BytesIO(payload)) as decoded:
            decoded.load()
    except VisionBoundaryError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise VisionBoundaryError("SOURCE_ANALYSIS_INTEGRITY_FAILED", 409) from exc
    if detected_format != expected_format:
        raise VisionBoundaryError("SOURCE_ANALYSIS_UNSUPPORTED_MEDIA", 415)
    return RasterRepresentation(media_type=media_type, format=detected_format,
                                width=width, height=height, color_mode=color_mode)


class _RawPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    x_min: float
    y_min: float
    x_max: float
    y_max: float
    confidence: float
    label: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def finite_numbers(self) -> "_RawPrediction":
        values = (self.x_min, self.y_min, self.x_max, self.y_max, self.confidence)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("prediction numbers must be finite")
        return self


class _DetectionResponse(BaseModel):
    """Closed subset of the documented CodeProject.AI detection response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    success: Literal[True]
    predictions: list[_RawPrediction] = Field(max_length=MAX_OBSERVATIONS)
    count: int = Field(ge=0, le=MAX_OBSERVATIONS)
    moduleId: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    message: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=500)
    moduleName: str | None = Field(default=None, max_length=120)
    command: str | None = Field(default=None, max_length=80)
    executionProvider: str | None = Field(default=None, max_length=120)
    canUseGPU: bool | None = None
    inferenceMs: int | None = Field(default=None, ge=0)
    processMs: int | None = Field(default=None, ge=0)
    analysisRoundTripMs: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def count_matches(self) -> "_DetectionResponse":
        if self.count != len(self.predictions):
            raise ValueError("prediction count mismatch")
        return self


class TrustedVisionTransport(Protocol):
    """Operator-supplied transport after independent isolation/license verification."""

    def detect(self, image: bytes, media_type: str) -> object: ...


class VisionAdapter:
    """Validated object-observation adapter with no built-in network transport."""

    def __init__(self, transport: TrustedVisionTransport | None = None, *,
                 expected_module_id: str | None = None,
                 confidence_scale: Literal["unit", "percent"] = "percent") -> None:
        if (transport is None) != (expected_module_id is None):
            raise ValueError("transport and expected_module_id must be configured together")
        if expected_module_id is not None and not expected_module_id:
            raise ValueError("expected_module_id must not be empty")
        if confidence_scale not in {"unit", "percent"}:
            raise ValueError("unsupported confidence_scale")
        self._transport = transport
        self._expected_module_id = expected_module_id
        self._confidence_scale = confidence_scale

    @staticmethod
    def _unavailable() -> dict:
        return {
            "status": "unavailable",
            "reason_code": "VISION_PROVIDER_NOT_CONFIGURED",
            "effect": "proposal_only",
            "observations": [],
        }

    def analyze(self, payload: bytes, media_type: str,
                representation: RasterRepresentation) -> dict:
        if self._transport is None or self._expected_module_id is None:
            return self._unavailable()
        try:
            response = _DetectionResponse.model_validate(
                self._transport.detect(payload, media_type))
        except (ValidationError, TypeError, ValueError) as exc:
            raise VisionBoundaryError("VISION_RESPONSE_INVALID") from exc
        if response.moduleId != self._expected_module_id:
            raise VisionBoundaryError("VISION_MODULE_MISMATCH")

        observations = []
        for prediction in response.predictions:
            if not (0 <= prediction.x_min < prediction.x_max <= representation.width
                    and 0 <= prediction.y_min < prediction.y_max <= representation.height):
                raise VisionBoundaryError("VISION_RESPONSE_INVALID")
            maximum = 1.0 if self._confidence_scale == "unit" else 100.0
            if not 0 <= prediction.confidence <= maximum:
                raise VisionBoundaryError("VISION_RESPONSE_INVALID")
            confidence = (prediction.confidence if self._confidence_scale == "unit"
                          else prediction.confidence / 100.0)
            observations.append({
                "label": prediction.label,
                "confidence": confidence,
                "box_2d": {
                    "x_min": prediction.x_min / representation.width,
                    "y_min": prediction.y_min / representation.height,
                    "x_max": prediction.x_max / representation.width,
                    "y_max": prediction.y_max / representation.height,
                },
            })
        return {
            "status": "observed",
            "module_id": response.moduleId,
            "effect": "proposal_only",
            "observations": observations,
        }
