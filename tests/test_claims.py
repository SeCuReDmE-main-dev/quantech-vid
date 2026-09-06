from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from quantech_vid.claims import Claim, ClaimEvidence, claim_notice
from quantech_vid.config import Settings
from quantech_vid.production_schemas import (OutputProfileV2, ProductionJob, ProjectRevision,
    SceneProjectV2, SceneV2, SourceAsset, SourceProvenance, SourceRights, TrackV2)
from quantech_vid.production_service import ProductionService
from quantech_vid.production_store import ContractError, ProductionStore, canonical_hash, now_iso
from quantech_vid.renderer import compose_frame
from quantech_vid.schemas import Scene
from quantech_vid.tool_service import ToolService


SOURCE_ID = "src_" + "a" * 32


def claim(status: str = "suspended", *, claim_id: str = "claim-1",
          evidence: bool = False) -> dict:
    return {
        "id": claim_id,
        "text": "The source reports a bounded result.",
        "status": status,
        "rationale": "Classification declared by the author for review.",
        "evidence": ([{"source_asset_id": SOURCE_ID, "locator": "section 2, lines 10-14"}]
                     if evidence else []),
    }


def document(*, claims: list[dict] | None = None) -> SceneProjectV2:
    return SceneProjectV2(
        slug="claim-project",
        title="Claim project",
        sources=[SOURCE_ID],
        scenes=[SceneV2(id="scene-1", duration=0.5, source_asset_id=SOURCE_ID,
                        title={"en": "Claim scene"}, body={"en": "Original body."},
                        claims=claims or [])],
        tracks=[TrackV2(locale="en", title="Claim project", narration="Narration")],
        output_profiles=[OutputProfileV2(name="square", width=320, height=320, fps=24)],
    )


def test_claim_contract_is_closed_bounded_and_does_not_invent_truth() -> None:
    with pytest.raises(ValidationError):
        Claim.model_validate({**claim(), "truth": True})
    with pytest.raises(ValidationError, match="at least one evidence"):
        Claim.model_validate(claim("observed"))
    observed = Claim.model_validate(claim("observed", evidence=True))
    assert observed.status == "observed"
    assert observed.evidence[0].source_asset_id == SOURCE_ID
    for status in ("disputed", "suspended"):
        assert Claim.model_validate(claim(status)).evidence == []
    with pytest.raises(ValidationError):
        ClaimEvidence(source_asset_id="../../private", locator="line 1")
    with pytest.raises(ValidationError):
        document(claims=[claim(claim_id=f"c-{index}") for index in range(17)])


def test_project_rejects_forged_evidence_and_duplicate_claim_ids() -> None:
    forged = claim("observed", evidence=True)
    forged["evidence"][0]["source_asset_id"] = "src_" + "b" * 32
    with pytest.raises(ValidationError, match="evidence sources must be declared"):
        document(claims=[forged])
    with pytest.raises(ValidationError, match="claim ids must be unique"):
        document(claims=[claim(), claim("hypothesis")])


def test_empty_claims_are_omitted_to_preserve_historical_hashes() -> None:
    payload = document().model_dump(mode="json")
    assert "claims" not in payload["scenes"][0]
    restored = SceneProjectV2.model_validate(payload)
    assert restored.scenes[0].claims == []
    assert restored.model_dump(mode="json") == payload
    assert canonical_hash(restored.model_dump(mode="json")) == canonical_hash(payload)
    legacy = Scene(id="scene-1", duration=1, title_fr="Titre", title_en="Title",
                   asset="source.png")
    assert "notice" not in legacy.model_dump(mode="json")
    with pytest.raises(ValidationError):
        Scene(id="scene-1", duration=1, title_fr="Titre", title_en="Title",
              asset="source.png", notice="x" * 501)


def test_suspension_is_preserved_across_cas_reevaluation(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.sqlite3", b"claim-signing-key-32-bytes-long!",
                            "claim-pairing-code")
    token, csrf = store.consume_pairing("claim-pairing-code", "teacher")
    human = store.authenticate(token, csrf)
    asset_path = tmp_path / "source.png"
    asset_path.write_bytes(b"fixture")
    asset = SourceAsset(id=SOURCE_ID, sha256=hashlib.sha256(b"fixture").hexdigest(),
        media_type="image/png", size=7,
        provenance=SourceProvenance(origin="test fixture", collected_by="teacher"),
        rights=SourceRights(basis="owned", reference="test fixture"),
        allowed_operations=["render"], created_at=now_iso())
    store.add_source(human, asset, asset_path)
    first = store.create_project(human, document())
    second = store.revise_project(human, first.project_id, first.revision,
                                  document(claims=[claim("suspended")]))
    old_agent_token = store.create_agent("teacher", "claim-agent-old", "Old revision",
                                         first.project_id, second.revision)
    old_agent = store.authenticate(old_agent_token)
    old_plan = store.create_plan(old_agent, {
        "project_id": first.project_id, "revision": second.revision, "locale": "en",
        "profile": "square", "narration_mode": "silent", "max_duration_seconds": 5,
        "max_output_bytes": 1_000_000,
    })
    store.authorize(human, old_plan.id, old_agent["id"], 300)
    third = store.revise_project(human, first.project_id, second.revision,
                                 document(claims=[claim("observed", evidence=True)]))
    assert len({first.document_hash, second.document_hash, third.document_hash}) == 3
    assert store.get_revision(human, first.project_id, 1).document.scenes[0].claims == []
    assert store.get_revision(human, first.project_id, 2).document.scenes[0].claims[0].status == "suspended"
    assert store.get_revision(human, first.project_id, 3).document.scenes[0].claims[0].status == "observed"
    assert store.get_plan(old_agent, old_plan.id).project_hash == second.document_hash
    new_agent_token = store.create_agent("teacher", "claim-agent-new", "New revision",
                                         first.project_id, third.revision)
    new_agent = store.authenticate(new_agent_token)
    new_plan = store.create_plan(new_agent, {
        "project_id": first.project_id, "revision": third.revision, "locale": "en",
        "profile": "square", "narration_mode": "silent", "max_duration_seconds": 5,
        "max_output_bytes": 1_000_000,
    })
    with pytest.raises(ContractError) as denied:
        store.enqueue_approved(new_agent, new_plan.id, "claim-new-revision-0001")
    assert denied.value.code == "APPROVAL_REQUIRED"


def test_tools_preserve_claims_in_proposals_without_mutating_revision() -> None:
    revision = ProjectRevision(project_id="prj_" + "b" * 32, revision=1,
        document_hash="c" * 64, document=document(), created_at=now_iso())

    class Store:
        def get_revision(self, actor: dict, project_id: str, revision_number: int):
            return revision

    service = ToolService(Store(), SimpleNamespace(), {"kind": "agent", "id": "agent-1"})
    proposed_scene = document(claims=[claim("hypothesis")]).scenes[0].model_dump(mode="json")
    storyboard = service.dispatch("quantech_stage_storyboard", {
        "project_id": revision.project_id, "revision": 1, "mode": "replace",
        "scenes": [proposed_scene],
    })
    assert storyboard["ok"] is True
    assert storyboard["result"]["diff"][0]["value"][0]["claims"][0]["status"] == "hypothesis"
    staged = service.dispatch("quantech_stage_scene_changes", {
        "project_id": revision.project_id, "revision": 1,
        "patches": [{"scene_id": "scene-1", "claims": [claim("disputed")]}],
    })
    assert staged["ok"] is True
    assert staged["result"]["diff"][0]["value"]["claims"][0]["status"] == "disputed"
    assert revision.document.scenes[0].claims == []


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
        return self.plan, self.revision, self.sources

    def update_job(self, job_id: str, **changes: object) -> None:
        self.job = self.job.model_copy(update=changes)

    def cancellation_requested(self, job_id: str) -> bool:
        return False

    def complete_job(self, job_id: str, paths: list[str], receipts: list[dict]) -> None:
        self.receipts = receipts
        self.job = self.job.model_copy(update={"status": "complete", "progress": 100,
                                               "receipts": receipts})


def test_render_projects_warning_and_receipted_claim_sidecar(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path, data_dir=tmp_path / "runtime", host="127.0.0.1", port=7476,
        allowed_asset_roots=(tmp_path,), tts_model="disabled", tts_voice_fr="disabled",
        tts_voice_en="disabled", max_workers=1)
    settings.ensure_directories()
    source_path = tmp_path / "source.png"
    source_path.write_bytes(b"source-bytes")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    claims = [claim("hypothesis", claim_id="h-1"), claim("disputed", claim_id="d-1"),
              claim("suspended", claim_id="s-1")]
    revision = ProjectRevision(project_id="prj_" + "b" * 32, revision=2,
        document_hash="c" * 64, document=document(claims=claims), created_at=now_iso())
    job = ProductionJob(id="job_" + "d" * 32, project_id=revision.project_id, revision=2,
        plan_id="plan_" + "e" * 32, status="queued", progress=0,
        created_at=now_iso(), updated_at=now_iso())
    plan = SimpleNamespace(id=job.plan_id, locale="en", profile="square",
        limits={"max_duration_seconds": 5, "max_output_bytes": 1_000_000})
    store = RenderStore(job, plan, revision,
        [{"id": SOURCE_ID, "sha256": source_hash, "internal_path": str(source_path)}])
    captured: dict = {}

    def fake_render(settings_arg, manifest_path, manifest, locale, profile, output_dir,
                    narration_mode, progress, cancelled):
        captured["body"] = manifest.scenes[0].body_en
        captured["notice"] = manifest.scenes[0].notice
        output_dir.mkdir(parents=True, exist_ok=True)
        names = ["video.mp4", "video.webm", "captions.srt", "captions.vtt", "poster.png",
                 "video-provenance.json", "video-qa.json"]
        paths = [output_dir / name for name in names]
        for path in paths:
            path.write_bytes(b"artifact")
        paths[-1].write_text(json.dumps({"passed": True, "webm": {"passed": True}}),
                                  encoding="utf-8")
        return paths

    result = ProductionService(settings, store, render_fn=fake_render, background=False).run_job(job.id)
    assert result.status == "complete"
    assert captured["body"] == "Original body."
    assert captured["notice"].startswith("[Claim notice:")
    assert all(status in captured["notice"] for status in ("hypothesis", "disputed", "suspended"))
    assert len(claim_notice([Claim.model_validate(item) for item in claims])) <= 500
    receipt = next(item for item in store.receipts if item["role"] == "claim-provenance")
    sidecar_path = settings.data_dir / "production" / job.id / "output" / receipt["name"]
    assert receipt["sha256"] == hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["project"] == {"id": revision.project_id, "revision": 2, "sha256": "c" * 64}
    assert sidecar["limitation"]["independent_verification"] is False
    assert [item["status"] for item in sidecar["scenes"][0]["claims"]] == [
        "hypothesis", "disputed", "suspended"]
    assert "internal_path" not in json.dumps(sidecar)


@pytest.mark.parametrize("size", [(320, 320), (1280, 720)])
def test_claim_banner_is_pixel_visible_and_independent_of_body_length(tmp_path: Path,
                                                                     size: tuple[int, int]) -> None:
    source = tmp_path / f"source-{size[0]}.png"
    from PIL import Image, ImageChops
    Image.new("RGB", size, "#557799").save(source)
    notice = claim_notice([Claim.model_validate(claim(status, claim_id=f"{status}-1"))
                           for status in ("hypothesis", "disputed", "suspended")])

    def frame(body: str, banner: str):
        return compose_frame(source, Scene(id="scene-1", duration=1, title_fr="Titre " * 40,
            title_en="Title " * 40, body_fr=body, body_en=body, notice=banner,
            asset=source.name), *size, "en")

    short = frame("Short body.", notice)
    long = frame("Long body text " * 300, notice)
    none = frame("Short body.", "")
    try:
        banner_box = (0, 0, size[0], int(size[1] * 0.40))
        short_crop = short.crop(banner_box)
        long_crop = long.crop(banner_box)
        none_crop = none.crop(banner_box)
        try:
            assert ImageChops.difference(short_crop, long_crop).getbbox() is None
            assert ImageChops.difference(short_crop, none_crop).getbbox() is not None
            assert short_crop.getbbox() is not None
        finally:
            short_crop.close()
            long_crop.close()
            none_crop.close()
    finally:
        short.close()
        long.close()
        none.close()
