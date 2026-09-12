"""Human learning data stays local, attributable, bounded, and separate from clearance."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from beatforge import api, learning


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(learning, "LEARNING_FILE", tmp_path / "learning.json")
    return TestClient(api.app)


def create_map(identifier: str, variant: int = 0, audio_variant: int = 0, **status_fields) -> Path:
    api._write_status(identifier, {"status": "review_required", "localStatus": "playtest_candidate", "difficulties": ["Easy", "Hard"], "mappingPlan": {"density": 1.0}, "privateCorpus": "DO-NOT-EXPORT-CORPUS", "inputPath": "C:/private/song.wav", **status_fields})
    folder = api._job_dir(identifier) / "map"
    folder.mkdir()
    info = {"_version": "2.1.0", "_songName": "Original test score", "_songFilename": "song.wav", "_beatsPerMinute": 120, "_songTimeOffset": 0, "_difficultyBeatmapSets": [{"_beatmapCharacteristicName": "Standard", "_difficultyBeatmaps": []}]}
    for difficulty in ("Easy", "Hard"):
        name = f"{difficulty}Standard.dat"
        chart = {"version": "3.3.0", "colorNotes": [{"b": 1, "x": variant % 4, "y": 0, "c": 0, "d": 1}, {"b": 3, "x": 3, "y": 1, "c": 1, "d": 0}], "bombNotes": [], "obstacles": [], "sliders": [], "burstSliders": []}
        (folder / name).write_text(json.dumps(chart))
        info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"].append({"_difficulty": difficulty, "_beatmapFilename": name, "_noteJumpMovementSpeed": 12, "_noteJumpStartBeatOffset": 0.5})
    (folder / "Info.dat").write_text(json.dumps(info))
    with wave.open(str(folder / "song.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"".join(struct.pack("<h", round(1000 * math.sin(index * 2 * math.pi * (220 + audio_variant) / 8000))) for index in range(32000)))
    reports = folder / "_beatforge"
    reports.mkdir()
    (reports / "qa_report.json").write_text('{"status":"playtest_candidate","errors":[],"warnings":[]}')
    (reports / "provenance.json").write_text('{"releaseGate":{"fullSpeedVrPlaytest":false},"privateCorpus":"DO-NOT-EXPORT-CORPUS"}')
    return folder


def feedback(job: str = "one", **changes) -> dict:
    return {"jobId": job, "difficulty": "Hard", "tester": "local", "ratings": {"flow": 3, "overall": 4}, "tags": ["too_dense"], "notes": "Less dense chorus, please.", **changes}


def test_feedback_hashes_actual_chart_and_audio_without_creating_headset_evidence(studio) -> None:
    folder = create_map("one")
    before_status = api._read_status("one")
    before_provenance = (folder / "_beatforge" / "provenance.json").read_bytes()
    response = studio.post("/api/feedback", json=feedback(startBeat=2, endBeat=4))
    assert response.status_code == 200
    record = response.json()["feedback"]
    assert record["source"] == "human"
    assert len(record["chartHash"]) == len(record["mapHash"]) == 64
    assert record["audioHash"] == hashlib.sha256((folder / "song.wav").read_bytes()).hexdigest()
    assert record["startBeat"] == 2
    assert record["endBeat"] == 4
    assert api._read_status("one") == before_status
    assert (folder / "_beatforge" / "provenance.json").read_bytes() == before_provenance
    assert not (api._job_dir("one") / "playtests.json").exists()
    summary = studio.get("/api/learning").json()
    assert summary["feedbackCount"] == 1
    assert summary["recentFeedback"][0]["id"] == record["id"]
    assert summary["modelRegistry"]["automaticTraining"] is False


@pytest.mark.parametrize("changes", [
    {"ratings": {"flow": 6}}, {"ratings": {"overall": "5"}}, {"ratings": {"flow": True}},
    {"ratings": {"other": 3}}, {"tags": ["unknown"]}, {"startBeat": 2},
    {"startBeat": 4, "endBeat": 2}, {"startBeat": 2, "endBeat": 1000},
    {"startBeat": "NaN", "endBeat": 4}, {"tester": "   "},
    {"source": "human"}, {"chartHash": "forged"},
    {"ratings": {}, "tags": [], "notes": ""}, {"difficulty": "Expert"},
])
def test_malformed_feedback_never_writes_learning(studio, changes) -> None:
    create_map("one")
    response = studio.post("/api/feedback", json=feedback(**changes))
    assert response.status_code == 422
    assert not learning.LEARNING_FILE.exists()


def test_feedback_and_presets_survive_a_new_python_process(studio) -> None:
    create_map("one")
    assert studio.post("/api/feedback", json=feedback()).status_code == 200
    preset = studio.post("/api/presets", json={"name": "My smooth maps", "mappingPlan": {"density": 0.85, "style": "flow"}})
    assert preset.status_code == 200
    assert preset.json()["mappingPlan"]["density"] == 0.85
    script = "import json,sys; from pathlib import Path; from beatforge import learning; learning.LEARNING_FILE=Path(sys.argv[1]); value=learning._read_store(); print(json.dumps({'feedbackCount':len(value['feedback']),'preset':value['presets'][0]}))"
    result = subprocess.run([sys.executable, "-c", script, str(learning.LEARNING_FILE)], capture_output=True, text=True, check=True, timeout=30)
    recovered = json.loads(result.stdout)
    assert recovered["feedbackCount"] == 1
    assert recovered["preset"] == preset.json()
    assert studio.get("/api/learning").json()["presets"] == [preset.json()]


def test_suggestions_use_distinct_human_charts_and_leave_plan_unchanged(studio) -> None:
    for index in range(3):
        create_map(f"chart{index}", variant=index)
        assert studio.post("/api/feedback", json=feedback(f"chart{index}")).status_code == 200
    plan = {"density": 1.0, "candidateCount": 2}
    response = studio.post("/api/learning/suggestions", json={"mappingPlan": plan, "tester": "local", "difficulty": "Hard"})
    assert response.status_code == 200
    result = response.json()
    assert result["ready"] is True
    assert result["evidenceCount"] == 3
    assert result["suggestedPlan"]["density"] < 1.0
    assert all(api._read_status(f"chart{index}")["mappingPlan"]["density"] == 1.0 for index in range(3))
    other = studio.post("/api/learning/suggestions", json={"mappingPlan": plan, "tester": "someone else"}).json()
    assert other["ready"] is False
    assert other["evidenceCount"] == 0


def test_comparison_requires_identical_audio_and_different_selected_chart(studio) -> None:
    create_map("one", variant=0)
    create_map("otheraudio", variant=1, audio_variant=20)
    create_map("identical", variant=0)
    request = {"preferredJob": "one", "alternateJob": "otheraudio", "difficulty": "Hard", "tester": "local", "notes": "Preferred flow"}
    assert studio.post("/api/comparisons", json=request).status_code == 409
    request["alternateJob"] = "identical"
    assert studio.post("/api/comparisons", json=request).status_code == 409
    hard = api._job_dir("identical") / "map" / "HardStandard.dat"
    hard.write_text(json.dumps(json.loads(hard.read_text()), sort_keys=True, indent=4))
    assert studio.post("/api/comparisons", json=request).status_code == 409
    # Changing a different difficulty does not make Hard a meaningful A/B pair.
    easy = api._job_dir("identical") / "map" / "EasyStandard.dat"
    chart = json.loads(easy.read_text())
    chart["colorNotes"][0]["x"] = 2
    easy.write_text(json.dumps(chart))
    assert studio.post("/api/comparisons", json=request).status_code == 409
    assert not learning.LEARNING_FILE.exists()


def test_latest_comparison_replaces_the_same_testers_vote_in_either_direction(studio) -> None:
    create_map("one", variant=0)
    create_map("two", variant=1)
    request = {"preferredJob": "one", "alternateJob": "two", "difficulty": "Hard", "tester": "local", "notes": "First pass"}
    first = studio.post("/api/comparisons", json=request).json()
    assert first["comparisonCount"] == 1
    assert first["replaced"] is False
    request.update(preferredJob="two", alternateJob="one", notes="Changed my preference")
    second = studio.post("/api/comparisons", json=request).json()
    assert second["comparisonCount"] == 1
    assert second["replaced"] is True
    assert second["comparison"]["id"] == first["comparison"]["id"]
    assert second["comparison"]["preferredJob"] == "two"
    request["tester"] = "second player"
    assert studio.post("/api/comparisons", json=request).json()["comparisonCount"] == 2


def test_revision_acceptance_is_explicit_and_originals_or_failed_revisions_are_rejected(studio) -> None:
    create_map("one")
    create_map("revision", variant=1, revisionOf="one", revision={"difficulty": "Hard", "startBeat": 2, "endBeat": 4, "seed": 4, "baseHash": "a" * 64})
    before = api._read_status("revision")
    assert studio.get("/api/learning").json()["acceptedRevisionCount"] == 0
    assert studio.post("/api/jobs/one/accept-revision", json={"tester": "local"}).status_code == 409
    response = studio.post("/api/jobs/revision/accept-revision", json={"tester": "local", "notes": "Keep this passage"})
    assert response.status_code == 200
    record = response.json()["acceptedRevision"]
    assert record["parentJob"] == "one"
    assert record["revision"]["startBeat"] == 2
    assert record["source"] == "human"
    assert api._read_status("revision") == before
    assert not (api._job_dir("revision") / "playtests.json").exists()
    qa = api._job_dir("revision") / "map" / "_beatforge" / "qa_report.json"
    qa.write_text('{"status":"invalid","errors":["collision"],"warnings":[]}')
    assert studio.post("/api/jobs/revision/accept-revision", json={"tester": "local"}).status_code == 409


def test_export_contains_feedback_preferences_and_accepted_metadata_without_source_files(studio) -> None:
    create_map("one")
    create_map("two", variant=1, revisionOf="one", revision={"difficulty": "Hard", "startBeat": 2, "endBeat": 4})
    studio.post("/api/feedback", json=feedback())
    studio.post("/api/comparisons", json={"preferredJob": "two", "alternateJob": "one", "difficulty": "Hard"})
    studio.post("/api/jobs/two/accept-revision", json={"notes": "Keep it"})
    result = studio.get("/api/learning/export")
    assert result.status_code == 200
    exported = result.json()
    assert exported["schemaVersion"] == 1
    assert exported["containsAudio"] is False
    assert exported["containsOfficialCorpus"] is False
    assert len(exported["feedback"]) == len(exported["preferences"]) == len(exported["acceptedRevisions"]) == 1
    for forbidden in ("DO-NOT-EXPORT-CORPUS", "C:/private", "colorNotes", "inputPath", "RIFF"):
        assert forbidden not in result.text


@pytest.mark.parametrize("broken", ["{broken", '{"schemaVersion":2}', '{"schemaVersion":1,"feedback":"oops","comparisons":[],"presets":[],"acceptedRevisions":[]}'])
def test_unreadable_learning_file_is_preserved(studio, broken) -> None:
    learning.LEARNING_FILE.write_text(broken)
    assert studio.get("/api/learning").status_code == 503
    assert studio.post("/api/presets", json={"name": "Preset", "mappingPlan": {}}).status_code == 503
    assert learning.LEARNING_FILE.read_text() == broken


def test_concurrent_preset_saves_do_not_lose_records(studio) -> None:
    def save(index):
        return studio.post("/api/presets", json={"name": f"Preset {index}", "mappingPlan": {}}).status_code
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert list(pool.map(save, range(12))) == [200] * 12
    saved = json.loads(learning.LEARNING_FILE.read_text())
    assert len(saved["presets"]) == 12
    assert len({item["id"] for item in saved["presets"]}) == 12
    assert not list(learning.LEARNING_FILE.parent.glob("learning.*.tmp"))
