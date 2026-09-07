from pathlib import Path

import pytest

from quantech_vid.renderer import render_project


@pytest.mark.parametrize("mode", ["", "open_ai", "local_kokoro_gpu", "auto", None])
def test_unknown_narration_never_falls_through_to_paid_synthesis(tmp_path: Path, monkeypatch, mode):
    def forbidden(*args, **kwargs):
        raise AssertionError("No voice provider may run for an unsupported mode")
    monkeypatch.setattr("quantech_vid.renderer.synthesize", forbidden)
    monkeypatch.setattr("quantech_vid.renderer.silent_wav", forbidden)
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="NARRATION_MODE_UNSUPPORTED"):
        render_project(None, tmp_path / "absent.json", None, "en", "square", output, mode)
    assert not output.exists()
