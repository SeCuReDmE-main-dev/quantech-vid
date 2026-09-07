from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from quantech_vid.config import Settings
from quantech_vid.production_schemas import (
    OutputProfileV2,
    ProductionJob,
    ProjectRevision,
    SceneProjectV2,
    SceneV2,
    SourceAsset,
    SourceProvenance,
    SourceRights,
    TrackV2,
)
from quantech_vid.production_service import ProductionService
from quantech_vid.production_store import ContractError, ProductionStore, canonical_hash, now_iso
from quantech_vid.subtitles import write_subtitles
from quantech_vid.transcripts import TranscriptSegment


SOURCE_ID = "src_" + "a" * 32


def segment(
    segment_id: str = "segment-1", *, start: float = 0.25, end: float = 1.75,
    text: str = "Human-authored caption.", source_id: str = SOURCE_ID,
) -> dict:
    return {
        "id": segment_id,
        "start": start,
        "end": end,
        "text": text,
        "source_asset_id": source_id,
        "source_locator": "page 2, paragraph 3",
    }


def document(*, segments: list[dict] | None = None, duration: float = 4.0) -> SceneProjectV2:
    return SceneProjectV2(
        slug="manual-transcript",
        title="Manual transcript",
        sources=[SOURCE_ID],
        scenes=[SceneV2(
            id="scene-1", duration=duration, source_asset_id=SOURCE_ID,
            title={"en": "Timed captions"}, body={"en": "Original scene body."},
        )],
        tracks=[TrackV2(
            locale="en", title="Manual transcript", narration="Legacy narration remains canonical.",
            segments=segments or [],
        )],
        output_profiles=[OutputProfileV2(name="square", width=320, height=320, fps=12)],
    )


def test_segment_contract_is_closed_bounded_and_finite() -> None:
    assert TranscriptSegment.model_validate(segment()).text == "Human-authored caption."
    invalid = [
        {**segment(), "unexpected": True},
        segment(start=1, end=1),
        segment(start=0.0001, end=0.0002),
        segment(start=-1),
        segment(start=float("nan")),
        segment(end=float("inf")),
        segment(text=""),
        segment(text=" \t\n\u00a0"),
        segment(text="\x00\x1f\u200b"),
        segment(text="x" * 1001),
        {**segment(), "source_locator": "x" * 161},
        {**segment(), "source_locator": " \t\n\u00a0"},
        {**segment(), "source_locator": "\x00\x1f\u200b"},
        segment(source_id="../../private"),
        segment(start="0.25"),
        segment(end="0.75"),
        segment(start=True),
        segment(end=True),
    ]
    for payload in invalid:
        with pytest.raises(ValidationError):
            TranscriptSegment.model_validate(payload)
    preserved = TranscriptSegment.model_validate(
        {**segment(text=" \tHuman text\x00"), "source_locator": " \tpage 1\x00"}
    )
    assert preserved.text == " \tHuman text\x00"
    assert preserved.source_locator == " \tpage 1\x00"
    assert TranscriptSegment.model_validate(segment(start=0, end=1)).end == 1.0
    with pytest.raises(ValidationError):
        document(segments=[segment(str(index)) for index in range(129)])


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        ([segment(), segment()], "ids must be unique"),
        ([segment(source_id="src_" + "b" * 32)], "sources must be declared"),
        ([segment(end=4.001)], "within project duration"),
        ([segment("later", start=2, end=3), segment("earlier", start=1, end=1.5)],
         "ordered and non-overlapping"),
        ([segment("first", start=0, end=2), segment("overlap", start=1.99, end=3)],
         "ordered and non-overlapping"),
    ],
)
def test_project_rejects_invalid_timeline(segments: list[dict], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        document(segments=segments)


def test_empty_segments_are_omitted_and_preserve_historical_hash() -> None:
    payload = document().model_dump(mode="json")
    assert "segments" not in payload["tracks"][0]
    restored = SceneProjectV2.model_validate(payload)
    assert restored.tracks[0].segments == []
    assert restored.model_dump(mode="json") == payload
    assert canonical_hash(restored.model_dump(mode="json")) == canonical_hash(payload)


def test_revision_changes_hash_and_old_grant_cannot_authorize_new_revision(tmp_path: Path) -> None:
    store = ProductionStore(
        tmp_path / "production.sqlite3", b"transcript-signing-key-32-bytes!", "pairing-code",
    )
    token, csrf = store.consume_pairing("pairing-code", "teacher")
    human = store.authenticate(token, csrf)
    source_path = tmp_path / "source.png"
    source_path.write_bytes(b"fixture")
    source = SourceAsset(
        id=SOURCE_ID, sha256=hashlib.sha256(b"fixture").hexdigest(), media_type="image/png", size=7,
        provenance=SourceProvenance(origin="fixture", collected_by="teacher"),
        rights=SourceRights(basis="owned", reference="fixture"),
        allowed_operations=["render"], created_at=now_iso(),
    )
    store.add_source(human, source, source_path)
    first = store.create_project(human, document())
    old_token = store.create_agent("teacher", "transcript-old", "Old revision", first.project_id, 1)
    old_agent = store.authenticate(old_token)
    old_plan = store.create_plan(old_agent, {
        "project_id": first.project_id, "revision": 1, "locale": "en", "profile": "square",
        "narration_mode": "silent", "max_duration_seconds": 10, "max_output_bytes": 2_000_000,
    })
    store.authorize(human, old_plan.id, old_agent["id"], 300)
    revised = store.revise_project(human, first.project_id, 1, document(segments=[segment()]))

    assert first.document_hash != revised.document_hash
    assert store.get_revision(human, first.project_id, 1).document.tracks[0].segments == []
    assert store.get_revision(human, first.project_id, 2).document.tracks[0].segments[0].text == segment()["text"]
    with pytest.raises(ContractError) as stale:
        store.enqueue_approved(old_agent, old_plan.id, "transcript-stale-0001")
    assert stale.value.code == "STALE_PROJECT_REVISION"

    new_token = store.create_agent(
        "teacher", "transcript-new", "New revision", first.project_id, revised.revision
    )
    new_agent = store.authenticate(new_token)
    new_plan = store.create_plan(new_agent, {
        "project_id": first.project_id, "revision": revised.revision, "locale": "en",
        "profile": "square", "narration_mode": "silent", "max_duration_seconds": 10,
        "max_output_bytes": 2_000_000,
    })
    with pytest.raises(ContractError) as not_approved:
        store.enqueue_approved(new_agent, new_plan.id, "transcript-new-0001")
    assert not_approved.value.code == "APPROVAL_REQUIRED"
    store.authorize(human, new_plan.id, new_agent["id"], 300)
    queued, created = store.enqueue_approved(
        new_agent, new_plan.id, "transcript-new-0001"
    )
    assert created is True and queued.revision == revised.revision


def test_manual_subtitles_escape_blocks_and_markup_without_mutating_text(tmp_path: Path) -> None:
    original = "Café <b>visible</b>\n\n2\n00:00:09,000 --> 00:00:10,000\nInjected & literal.\x00"
    item = TranscriptSegment.model_validate(segment(text=original))
    srt, vtt = tmp_path / "captions.srt", tmp_path / "captions.vtt"
    write_subtitles("unused", 4, srt, vtt, [item])
    srt_text, vtt_text = srt.read_text(encoding="utf-8"), vtt.read_text(encoding="utf-8")

    assert item.text == original
    assert srt_text.count(" --> ") == 1
    assert "00:00:09,000 --&gt; 00:00:10,000" in srt_text
    assert "\n\n2\n" not in srt_text
    assert "<b>" not in vtt_text and "&lt;b&gt;visible&lt;/b&gt;" in vtt_text
    assert "00:00:00,250 --> 00:00:01,750" in srt_text
    assert "00:00:00.250 --> 00:00:01.750" in vtt_text
    assert "Café" in srt_text and "&amp; literal" in srt_text
    assert "\x00" not in srt_text and "\ufffd" in srt_text


def test_caption_timestamp_rounding_is_half_up_and_never_collapses(tmp_path: Path) -> None:
    item = TranscriptSegment.model_validate(segment(start=0.0005, end=0.0015))
    srt, vtt = tmp_path / "rounding.srt", tmp_path / "rounding.vtt"
    write_subtitles("unused", 1, srt, vtt, [item])
    assert "00:00:00,001 --> 00:00:00,002" in srt.read_text(encoding="utf-8")
    assert "00:00:00.001 --> 00:00:00.002" in vtt.read_text(encoding="utf-8")


class RenderStore:
    def __init__(self, job: ProductionJob, plan: object, revision: ProjectRevision,
                 sources: list[dict]) -> None:
        self.job, self.plan, self.revision, self.sources = job, plan, revision, sources
        self.receipts: list[dict] = []

    def recover_interrupted(self) -> int:
        return 0

    def job_internal(self, job_id: str):
        return self.job, []

    def claim_next(self) -> str:
        return self.job.id

    def plan_and_revision_internal(self, plan_id: str):
        return self.plan, self.revision, self.sources, []

    def update_job(self, job_id: str, **changes: object) -> None:
        self.job = self.job.model_copy(update=changes)

    def cancellation_requested(self, job_id: str) -> bool:
        return False

    def complete_job(self, job_id: str, paths: list[str], receipts: list[dict]) -> None:
        self.receipts = receipts
        self.job = self.job.model_copy(update={"status": "complete", "progress": 100,
                                               "receipts": receipts})


def test_real_four_second_render_uses_exact_manual_cues_and_receipts_sidecar(tmp_path: Path) -> None:
    settings = Settings(
        root=tmp_path, data_dir=tmp_path / "runtime", host="127.0.0.1", port=7476,
        allowed_asset_roots=(tmp_path,), tts_model="disabled", tts_voice_fr="disabled",
        tts_voice_en="disabled", max_workers=1,
    )
    settings.ensure_directories()
    source_path = tmp_path / "source.png"
    Image.new("RGB", (320, 320), "#236b69").save(source_path)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    transcript = [
        segment("opening", start=0.25, end=1.75, text="Première ligne accentuée."),
        segment("closing", start=2.0, end=3.8, text="Second <tag> & conclusion."),
    ]
    project = document(segments=transcript)
    revision = ProjectRevision(
        project_id="prj_" + "b" * 32, revision=2,
        document_hash=canonical_hash(project.model_dump(mode="json")), document=project,
        created_at=now_iso(),
    )
    job = ProductionJob(
        id="job_" + "d" * 32, project_id=revision.project_id, revision=2,
        plan_id="plan_" + "e" * 32, status="queued", progress=0,
        created_at=now_iso(), updated_at=now_iso(),
    )
    plan = SimpleNamespace(
        id=job.plan_id, locale="en", profile="square",
        limits={"max_duration_seconds": 10, "max_output_bytes": 20_000_000},
        provider_resource_modes={},
    )
    store = RenderStore(job, plan, revision, [{
        "id": SOURCE_ID, "sha256": source_hash, "internal_path": str(source_path),
    }])
    result = ProductionService(settings, store, background=False).run_job(job.id)

    assert result.status == "complete"
    output_dir = settings.data_dir / "production" / job.id / "output"
    srt = (output_dir / "manual-transcript-en-square.srt").read_text(encoding="utf-8")
    vtt = (output_dir / "manual-transcript-en-square.vtt").read_text(encoding="utf-8")
    assert "00:00:00,250 --> 00:00:01,750" in srt
    assert "00:00:02,000 --> 00:00:03,800" in srt
    assert "00:00:00.250 --> 00:00:01.750" in vtt
    assert "00:00:02.000 --> 00:00:03.800" in vtt
    assert project.tracks[0].segments[1].text == "Second <tag> & conclusion."

    transcript_receipt = next(item for item in store.receipts
                              if item["role"] == "transcript-provenance")
    sidecar_path = output_dir / transcript_receipt["name"]
    assert transcript_receipt["sha256"] == hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["project"] == {
        "id": revision.project_id, "revision": 2, "sha256": revision.document_hash,
    }
    assert sidecar["locale"] == "en" and sidecar["method"] == "manual"
    assert sidecar["limitations"] == {
        "automatic_speech_recognition_performed": False,
        "independent_verification": False,
    }
    assert [entry["source_sha256"] for entry in sidecar["segments"]] == [source_hash, source_hash]
    assert "internal_path" not in json.dumps(sidecar)
    assert next(item for item in store.receipts if item["role"] == "video-mp4")["size"] > 1024
    assert next(item for item in store.receipts if item["role"] == "video-webm")["size"] > 1024
    for role, name in (
        ("captions-srt", "manual-transcript-en-square.srt"),
        ("captions-vtt", "manual-transcript-en-square.vtt"),
    ):
        receipt = next(item for item in store.receipts if item["role"] == role)
        path = output_dir / name
        assert receipt["name"] == name
        assert receipt["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    qa = json.loads((output_dir / "manual-transcript-en-square-qa.json").read_text(encoding="utf-8"))
    assert qa["passed"] is True and qa["webm"]["passed"] is True
    assert qa["duration_expected"] == 4.0 and qa["webm"]["duration_expected"] == 4.0


def test_locale_without_segments_has_no_transcript_provenance_receipt(tmp_path: Path) -> None:
    settings = Settings(
        root=tmp_path, data_dir=tmp_path / "runtime", host="127.0.0.1", port=7476,
        allowed_asset_roots=(tmp_path,), tts_model="disabled", tts_voice_fr="disabled",
        tts_voice_en="disabled", max_workers=1,
    )
    settings.ensure_directories()
    source_path = tmp_path / "source.png"
    source_path.write_bytes(b"source")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    project = document()
    revision = ProjectRevision(
        project_id="prj_" + "b" * 32, revision=1,
        document_hash=canonical_hash(project.model_dump(mode="json")), document=project,
        created_at=now_iso(),
    )
    job = ProductionJob(
        id="job_" + "f" * 32, project_id=revision.project_id, revision=1,
        plan_id="plan_" + "e" * 32, status="queued", progress=0,
        created_at=now_iso(), updated_at=now_iso(),
    )
    plan = SimpleNamespace(
        id=job.plan_id, locale="en", profile="square",
        limits={"max_duration_seconds": 10, "max_output_bytes": 2_000_000},
        provider_resource_modes={},
    )
    store = RenderStore(job, plan, revision, [{
        "id": SOURCE_ID, "sha256": source_hash, "internal_path": str(source_path),
    }])

    def fake_render(_settings, _manifest_path, _manifest, _locale, _profile, output_dir,
                    _narration_mode, progress, cancelled):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = [output_dir / name for name in (
            "video.mp4", "video.webm", "video.srt", "video.vtt", "poster.png",
            "video-provenance.json", "video-qa.json",
        )]
        for path in paths:
            path.write_bytes(b"artifact")
        paths[-1].write_text(
            json.dumps({"passed": True, "webm": {"passed": True}}), encoding="utf-8"
        )
        progress(100)
        assert cancelled() is False
        return paths

    result = ProductionService(
        settings, store, render_fn=fake_render, background=False
    ).run_job(job.id)
    assert result.status == "complete"
    assert "transcript-provenance" not in {item["role"] for item in store.receipts}
    output_dir = settings.data_dir / "production" / job.id / "output"
    assert not list(output_dir.glob("*-transcript-provenance.json"))
