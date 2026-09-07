from __future__ import annotations

import io
import math

import pytest
from PIL import Image

from quantech_vid.vision import (VisionAdapter, VisionBoundaryError,
                                 decode_raster)


def png_bytes(size: tuple[int, int] = (100, 50)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "#456789").save(output, format="PNG")
    return output.getvalue()


class SyntheticTransport:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[bytes, str]] = []

    def detect(self, image: bytes, media_type: str) -> object:
        self.calls.append((image, media_type))
        return self.response


def response(**changes: object) -> dict:
    value = {
        "success": True,
        "predictions": [{
            "x_min": 10.0,
            "y_min": 5.0,
            "x_max": 60.0,
            "y_max": 30.0,
            "confidence": 95.0,
            "label": "microphone",
        }],
        "count": 1,
        "moduleId": "ObjectDetectionPilot",
    }
    value.update(changes)
    return value


def test_absent_pilot_is_unavailable_and_makes_no_transport_claim() -> None:
    payload = png_bytes()
    representation = decode_raster(payload, "image/png")
    result = VisionAdapter().analyze(payload, "image/png", representation)
    assert result == {
        "status": "unavailable",
        "reason_code": "VISION_PROVIDER_NOT_CONFIGURED",
        "effect": "proposal_only",
        "observations": [],
    }
    assert "local" not in result and "provider" not in result and "connected" not in result


def test_invalid_confidence_scale_is_not_silently_interpreted() -> None:
    with pytest.raises(ValueError, match="confidence_scale"):
        VisionAdapter(confidence_scale="unknown")


def test_synthetic_transport_is_closed_and_normalizes_2d_observations() -> None:
    payload = png_bytes()
    representation = decode_raster(payload, "image/png")
    transport = SyntheticTransport(response())
    result = VisionAdapter(transport, expected_module_id="ObjectDetectionPilot").analyze(
        payload, "image/png", representation)
    assert transport.calls == [(payload, "image/png")]
    assert result == {
        "status": "observed",
        "module_id": "ObjectDetectionPilot",
        "effect": "proposal_only",
        "observations": [{
            "label": "microphone",
            "confidence": 0.95,
            "box_2d": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.6, "y_max": 0.6},
        }],
    }
    assert "identity" not in str(result).lower()
    assert "face" not in str(result).lower()
    assert "3d" not in str(result).lower()


@pytest.mark.parametrize("bad_response,code", [
    (response(extra="not allowed"), "VISION_RESPONSE_INVALID"),
    (response(count=0), "VISION_RESPONSE_INVALID"),
    (response(predictions=[{**response()["predictions"][0], "x_max": 101.0}]),
     "VISION_RESPONSE_INVALID"),
    (response(predictions=[{**response()["predictions"][0], "confidence": math.nan}]),
     "VISION_RESPONSE_INVALID"),
    (response(moduleId="UnexpectedModule"), "VISION_MODULE_MISMATCH"),
])
def test_adapter_rejects_malformed_bounds_nan_and_module_mismatch(
        bad_response: dict, code: str) -> None:
    payload = png_bytes()
    representation = decode_raster(payload, "image/png")
    adapter = VisionAdapter(SyntheticTransport(bad_response),
                            expected_module_id="ObjectDetectionPilot")
    with pytest.raises(VisionBoundaryError, match=code) as caught:
        adapter.analyze(payload, "image/png", representation)
    assert caught.value.code == code


def test_adapter_bounds_observation_count() -> None:
    prediction = response()["predictions"][0]
    oversized = response(predictions=[prediction] * 129, count=129)
    payload = png_bytes()
    with pytest.raises(VisionBoundaryError, match="VISION_RESPONSE_INVALID"):
        VisionAdapter(SyntheticTransport(oversized),
                      expected_module_id="ObjectDetectionPilot").analyze(
                          payload, "image/png", decode_raster(payload, "image/png"))


def test_raster_contract_rejects_wrong_media_corruption_and_dimensions() -> None:
    payload = png_bytes()
    with pytest.raises(VisionBoundaryError, match="SOURCE_ANALYSIS_UNSUPPORTED_MEDIA"):
        decode_raster(payload, "image/jpeg")
    with pytest.raises(VisionBoundaryError, match="SOURCE_ANALYSIS_INTEGRITY_FAILED"):
        decode_raster(b"not an image", "image/png")
    oversized = png_bytes((4001, 4000))
    with pytest.raises(VisionBoundaryError, match="SOURCE_ANALYSIS_TOO_LARGE"):
        decode_raster(oversized, "image/png")
