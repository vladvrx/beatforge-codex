"""Studio contracts, failed gates, and recovery without a real game installation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from beatforge import api


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(api, "METADATA_DIR", tmp_path / "metadata")
    monkeypatch.setattr(api, "IMPORTS_DIR", tmp_path / "imports")
    monkeypatch.setattr(api, "find_custom_levels", lambda: None)
    monkeypatch.setattr(api, "_run_job", api._run_job)
    def forbid_install(*args, **kwargs):
        raise AssertionError("This test must not install a map")
    monkeypatch.setattr(api, "_install_pack_to_custom_levels", forbid_install)
    return TestClient(api.app)


def saved_job(identifier: str, state: str = "needs_anchors", **fields) -> Path:
    api._write_status(identifier, {"id": identifier, "status": state, "stages": [], **fields})
    directory = api._job_dir(identifier)
    (directory / "input.wav").write_bytes(b"saved-audio")
    return directory


def test_browser_metadata_and_creative_plan_reach_generation(studio, monkeypatch: pytest.MonkeyPatch) -> None:
    metadata_id = "0123456789abcdef0123"
    api.METADATA_DIR.mkdir()
    cover = api.METADATA_DIR / "cover.png"
    cover.write_bytes(b"cover")
    (api.METADATA_DIR / f"{metadata_id}.json").write_text(json.dumps({"_cachePath": str(cover)}))
    observed = {}
    def pipeline(**kwargs):
        observed.update(kwargs)
        return {"status": "needs_anchors", "payload": {}}
    monkeypatch.setattr(api, "run_premium_pipeline", pipeline)
    response = studio.post("/api/generate", files={"audio": ("song.wav", b"RIFF", "audio/wav")}, data={
        "metadataId": metadata_id,
        "mappingPlan": json.dumps({"brief": "Bass led", "density": 0.8}),
        "difficulties": "Easy,Hard",
    })
    assert response.status_code == 200
    job = api._read_status(response.json()["id"])
    assert job["metadataId"] == metadata_id
    assert observed["cover"] == cover
    assert observed["mapping_plan"]["density"] == 0.8
    assert observed["mapping_plan"]["brief"] == "Bass led"
    assert observed["difficulties"] == ["Easy", "Hard"]
    assert observed["allow_unconfirmed"] is False


@pytest.mark.parametrize("plan", ["not-json", "[]", '{"density": 900}', '{"noWalls": "false"}'])
def test_invalid_mapping_plan_is_rejected_before_creating_job(studio, plan: str) -> None:
    response = studio.post("/api/generate", files={"audio": ("song.wav", b"RIFF", "audio/wav")}, data={"mappingPlan": plan})
    assert response.status_code == 400
    assert not api.JOBS_DIR.exists()


@pytest.mark.parametrize("anchors,expected", [
    ([{"beat": 0, "sample": 0}, {"beat": 1, "sample": 22050}], [0, 22050]),
    ([{"beat": 0, "timeSeconds": 0}, {"beat": 1, "timeSeconds": 0.5}], [0, 22050]),
])
def test_both_anchor_formats_are_normalized_and_queued(studio, monkeypatch: pytest.MonkeyPatch, anchors, expected) -> None:
    directory = saved_job("anchors")
    scheduled = []
    monkeypatch.setattr(api, "_run_job", scheduled.append)
    response = studio.post("/api/jobs/anchors/anchors", json={"anchors": anchors})
    assert response.status_code == 200
    assert scheduled == ["anchors"]
    assert [item["sample"] for item in json.loads((directory / "anchors.json").read_text())["anchors"]] == expected


@pytest.mark.parametrize("anchor", [
    {"beat": 1, "sample": -1}, {"beat": "nan", "sample": 1},
    {"beat": 1, "timeSeconds": "inf"}, {"beat": "bad", "sample": 1},
    {"beat": 1, "sample": 2.5}, {"beat": 1, "timeSeconds": None},
])
def test_bad_anchors_return_actionable_400(studio, anchor) -> None:
    directory = saved_job("badanchors")
    response = studio.post("/api/jobs/badanchors/anchors", json={"anchors": [{"beat": 0, "sample": 0}, anchor]})
    assert response.status_code == 400
    assert "anchor" in response.json()["detail"].lower()
    assert not (directory / "anchors.json").exists()
    assert api._read_status("badanchors")["status"] == "needs_anchors"


@pytest.mark.parametrize("report", [None, "{broken", {}, {"status": "invalid", "errors": [], "warnings": []}, {"status": "invalid", "errors": ["collision"], "warnings": []}])
def test_failed_or_missing_qa_never_packages_or_installs(studio, monkeypatch: pytest.MonkeyPatch, report) -> None:
    directory = saved_job("failedqa", "queued")
    reports = directory / "map" / "_beatforge"
    reports.mkdir(parents=True)
    (directory / "map" / "Info.dat").write_text("{}")
    if report is not None:
        (reports / "qa_report.json").write_text(report if isinstance(report, str) else json.dumps(report))
    (reports / "analysis.json").write_text('{"status":"timing_verified"}')
    monkeypatch.setattr(api, "run_premium_pipeline", lambda **kwargs: {"status": "invalid"})
    api._run_job("failedqa")
    status = api._read_status("failedqa")
    assert status["status"] == status["localStatus"] == "invalid"
    assert status["qa"]["errors"] > 0
    assert not status.get("installed")
    assert not (directory / "map.zip").exists()
    assert studio.get("/api/jobs/failedqa/download").status_code == 409
    assert studio.post("/api/jobs/failedqa/install").status_code == 409


def test_download_rechecks_report_even_if_saved_status_claims_ready(studio) -> None:
    directory = saved_job("staleready", "review_required", localStatus="playtest_candidate")
    (directory / "map.zip").write_bytes(b"old-package")
    assert studio.get("/api/jobs/staleready/download").status_code == 409


def test_startup_marks_abandoned_workers_without_deleting_old_history(studio) -> None:
    running = saved_job("running", "running")
    cancelled = saved_job("cancelled", "cancelling")
    (cancelled / "cancel.requested").touch()
    ready = saved_job("oldready", "review_required", createdAt=1)
    os.utime(ready, (1, 1))
    api._on_startup()
    assert api._read_status("running")["status"] == "interrupted"
    assert api._read_status("cancelled")["status"] == "cancelled"
    assert api._read_status("oldready")["status"] == "review_required"
    assert (running / "input.wav").read_bytes() == b"saved-audio"
    assert ready.is_dir()
    history = studio.get("/api/jobs").json()
    assert history["total"] == 3
    assert history["jobs"][-1]["id"] == "oldready"
    assert all(job["canRetry"] for job in history["jobs"])


def test_retry_preserves_confirmed_inputs_without_stale_output_or_evidence(studio, monkeypatch: pytest.MonkeyPatch) -> None:
    original = saved_job("original", "interrupted", title="Song", seed=7, difficulties=["Hard"], mappingPlan={"density": 0.8}, metadataId="track", engine="premium")
    (original / "anchors.json").write_text('{"anchors":[]}')
    (original / "approved_palette.json").write_text('{"approved":true}')
    (original / "playtests.json").write_text('[{"passed":true}]')
    (original / "map.zip").write_bytes(b"stale")
    scheduled = []
    monkeypatch.setattr(api, "_run_job", scheduled.append)
    result = studio.post("/api/jobs/original/retry")
    assert result.status_code == 200
    identifier = result.json()["id"]
    assert identifier != "original"
    assert scheduled == [identifier]
    retry = api._job_dir(identifier)
    for name in ("input.wav", "anchors.json", "approved_palette.json"):
        assert (retry / name).read_bytes() == (original / name).read_bytes()
    assert not (retry / "map.zip").exists()
    assert not (retry / "playtests.json").exists()
    status = api._read_status(identifier)
    assert status["retryOf"] == "original"
    assert status["mappingPlan"] == {"density": 0.8}
    assert status["difficulties"] == ["Hard"]
    assert status["seed"] == 7
    assert status["metadataId"] == "track"
    assert api._read_status("original")["status"] == "interrupted"


def test_active_and_section_revision_jobs_cannot_plain_retry(studio) -> None:
    saved_job("active", "running")
    saved_job("revision", "error", revisionOf="original")
    assert studio.post("/api/jobs/active/retry").status_code == 409
    assert studio.post("/api/jobs/revision/retry").status_code == 409
    assert studio.get("/api/jobs/revision").json()["canRetry"] is False


def test_cancelling_queued_job_never_starts_pipeline(studio, monkeypatch: pytest.MonkeyPatch) -> None:
    saved_job("queued", "queued")
    def forbidden(**kwargs):
        pytest.fail("Cancelled job started generation")
    monkeypatch.setattr(api, "run_premium_pipeline", forbidden)
    assert studio.post("/api/jobs/queued/cancel").json()["status"] == "cancelled"
    api._run_job("queued")
    assert api._read_status("queued")["status"] == "cancelled"
    assert studio.post("/api/jobs/queued/cancel").status_code == 409


def test_running_cancel_is_forwarded_and_cannot_publish_output(studio, monkeypatch: pytest.MonkeyPatch) -> None:
    saved_job("working", "queued")
    def pipeline(**kwargs):
        kwargs["progress"]("mapping", "working")
        assert studio.post("/api/jobs/working/cancel").json()["status"] == "cancelling"
        assert kwargs["cancel_requested"]() is True
        raise api.PipelineCancelled("stopped")
    monkeypatch.setattr(api, "run_premium_pipeline", pipeline)
    api._run_job("working")
    status = studio.get("/api/jobs/working").json()
    assert status["status"] == "cancelled"
    assert status["localStatus"] is None
    assert status["canRetry"] is True
    assert status["canCancel"] is False
    assert not (api._job_dir("working") / "map.zip").exists()


def test_failed_retest_removes_old_pass_until_that_tester_passes_again(studio) -> None:
    directory = saved_job("retest", "review_required", localStatus="playtest_candidate", difficulties=["Easy"])
    evidence = {"difficulty": "Easy", "speed": "full", "tester": "mapper", "passed": True}
    response = studio.post("/api/jobs/retest/playtests", json=evidence)
    assert response.json()["releaseGate"]["allFiveFullSpeed"] is True
    evidence["passed"] = False
    response = studio.post("/api/jobs/retest/playtests", json=evidence)
    assert response.json()["releaseGate"]["allFiveFullSpeed"] is False
    assert response.json()["releaseGate"]["unresolvedFailures"] == 1
    evidence["passed"] = True
    response = studio.post("/api/jobs/retest/playtests", json=evidence)
    assert response.json()["releaseGate"]["allFiveFullSpeed"] is True
    assert response.json()["releaseGate"]["unresolvedFailures"] == 0
    assert len(json.loads((directory / "playtests.json").read_text())) == 3
