from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantech_vid.api import APIConfig, create_app
from quantech_vid.config import Settings
from quantech_vid.local_voice import LocalVoiceError


@pytest.mark.parametrize('configured,valid', [(False, False), (True, False), (True, True)])
def test_optional_voice_binding_never_synthesizes_or_disables_manual_studio(tmp_path: Path, monkeypatch, configured, valid):
    calls = []

    def discover(path):
        calls.append('discover')
        assert path == tmp_path / 'isolated'
        if not valid:
            raise LocalVoiceError('LOCAL_VOICE_RESOURCE_MISSING')
        return object()

    class Pilot:
        def __init__(self, resources):
            calls.append('construct')

        def binding(self):
            calls.append('binding')
            return {}, 'a' * 64

        def synthesize(self, *args, **kwargs):
            raise AssertionError('Startup and health must never synthesize')

    monkeypatch.setattr('quantech_vid.api.LocalVoiceResources.discover', discover)
    monkeypatch.setattr('quantech_vid.api.LocalVoicePilot', Pilot)
    settings = Settings(root=tmp_path, data_dir=tmp_path / 'data', host='127.0.0.1', port=7476,
        allowed_asset_roots=(tmp_path,), tts_model='disabled', tts_voice_fr='disabled',
        tts_voice_en='disabled', max_workers=1,
        local_voice_runtime_root=tmp_path / 'isolated' if configured else None)
    settings.ensure_directories()
    app = create_app(settings, APIConfig(expected_host='testserver', allowed_origin='http://testserver',
        signing_key=b'synthetic-activation-test-signing', background_jobs=False))
    try:
        with TestClient(app) as client:
            health = client.get('/api/v2/health').json()
        assert health['capabilities']['experimental_local_voice_configured'] is (configured and valid)
        assert health['capabilities']['approved_silent_render'] is True
        assert health['capabilities']['paid_narration'] is False
        assert 'isolated' not in str(health)
        assert calls == (['discover', 'construct', 'binding'] if configured and valid else ['discover'] if configured else [])
    finally:
        app.state.production_service.executor.shutdown(wait=True)
