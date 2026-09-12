"""Preview decoding and isolated revision contracts using actual audio and map files."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

from fastapi import HTTPException
from fastapi.testclient import TestClient
import numpy as np
import pytest
import soundfile as sf

from beatforge import api, preview

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "beat-saber-mapping" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import choreography
from validate_map import validate_package


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def note(beat: float, direction: int = 1, x: int = 0, color: int = 0) -> dict:
    return {"b": beat, "x": x, "y": 0 if direction == 1 else 1, "c": color, "d": direction, "a": 0}


def chart(notes: list[dict]) -> dict:
    return {"version": "3.3.0", "colorNotes": notes, "bombNotes": [], "obstacles": [], "sliders": [], "burstSliders": [], "basicBeatmapEvents": [], "bpmEvents": [], "customData": {"untouched": "source metadata"}}


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(api, "find_custom_levels", lambda: None)
    def forbidden_install(*args, **kwargs):
        raise AssertionError("Preview and revision tests must never install a map")
    monkeypatch.setattr(api, "_install_pack_to_custom_levels", forbidden_install)
    return TestClient(api.app)


@pytest.fixture
def saved_map(studio) -> Path:
    folder = api._job_dir("original") / "map"
    folder.mkdir(parents=True)
    with ZipFile(ROOT / "web" / "assets" / "demo" / "map.zip") as archive:
        info = json.loads(archive.read("Info.dat"))
        (folder / "cover.png").write_bytes(archive.read("cover.png"))
    info["_songName"] = "Revision fixture"
    info["_songFilename"] = "song.ogg"
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [entry for entry in info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] if entry["_difficulty"] in ("Easy", "Hard")]
    write_json(folder / "Info.dat", info)
    for difficulty in ("Easy", "Hard"):
        notes = [note(beat, 1 if index % 2 == 0 else 0) for index, beat in enumerate((2, 4, 6, 8, 10, 12))]
        if difficulty == "Hard":
            notes.append(note(14, 1))
        write_json(folder / f"{difficulty}Standard.dat", chart(notes))
    samples = np.zeros((44100 * 8, 2), dtype=np.float32)
    # Distinct stereo pulses prove the preview decodes samples, rather than fake bars.
    samples[11025:11525, 0] = 0.25
    samples[22050:22550, 1] = -0.75
    sf.write(folder / "song.ogg", samples, 44100, format="OGG", subtype="VORBIS")
    analysis = {
        "schemaVersion": 1, "status": "timing_verified", "bpm": 120, "sampleRate": 44100,
        "durationSeconds": 8, "durationSamples": len(samples),
        "beatGrid": [{"beat": index / 2, "sample": index * 11025, "timeSeconds": index / 4} for index in range(32)],
        "events": [{"beat": index / 2, "strength": 3.0, "layer": "mix"} for index in range(4, 31)],
        "clickTrackEvidence": {"checkpoints": [{"sample": value, "label": label} for value, label in ((0, "start"), (176400, "middle"), (330750, "end"))]},
    }
    write_json(folder / "_beatforge" / "analysis.json", analysis)
    write_json(folder / "_beatforge" / "sections.json", {"sections": [{"id": "section-000", "label": "verse", "startBeat": 0, "endBeat": 8, "intensity": 0.5}, {"id": "section-001", "label": "chorus", "startBeat": 8, "endBeat": 16, "intensity": 0.8}]})
    write_json(folder / "_beatforge" / "provenance.json", {"seed": 12, "releaseGate": {"vrPlaytest": True}, "humanPlaytests": [{"passed": True}], "reviews": [{"approved": True}], "vrPlaytestPassed": True})
    qa = validate_package(folder)
    assert not qa.errors, qa.to_dict()
    write_json(folder / "_beatforge" / "qa_report.json", qa.to_dict())
    api._write_status("original", {"id": "original", "status": "review_required", "localStatus": "playtest_candidate", "title": "Revision fixture", "artist": "Fixture", "difficulties": ["Easy", "Hard"], "installed": False})
    return folder


def revision_payload(folder: Path, **changes) -> dict:
    return {"difficulty": "Easy", "startBeat": 6, "endBeat": 8, "baseHash": preview.chart_identity(folder), "seed": 123, "mappingPlan": {"candidateCount": 1, "noWalls": True, "noBombs": True}, **changes}


@pytest.mark.parametrize("relative", ["../outside.dat", "missing.dat"])
def test_confined_rejects_escape_and_missing_files(tmp_path, relative):
    root = tmp_path / "map"
    root.mkdir()
    (tmp_path / "outside.dat").write_text("private")
    with pytest.raises(HTTPException) as raised:
        preview.confined(root, relative)
    assert raised.value.status_code == 404


def test_confined_rejects_absolute_external_path_and_symlink(tmp_path):
    root = tmp_path / "map"
    root.mkdir()
    outside = tmp_path / "outside.dat"
    outside.write_text("private")
    with pytest.raises(HTTPException):
        preview.confined(root, str(outside.resolve()))
    try:
        (root / "linked.dat").symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    with pytest.raises(HTTPException):
        preview.confined(root, "linked.dat")


def test_waveform_reads_real_stereo_amplitudes_in_bounded_bins(tmp_path):
    path = tmp_path / "pulses.wav"
    samples = np.zeros((800, 2), dtype=np.float32)
    samples[105, 0] = 0.25
    samples[505, 1] = -0.75
    sf.write(path, samples, 800, subtype="FLOAT")
    duration, peaks = preview.waveform(path, bins=8)
    assert duration == 1.0
    assert peaks == [0.0, 0.25, 0.0, 0.0, 0.0, 0.75, 0.0, 0.0]


def test_preview_selects_actual_difficulty_and_filters_qa(studio, saved_map):
    qa_path = saved_map / "_beatforge" / "qa_report.json"
    qa = preview.read_json(qa_path)
    qa["warnings"] = [{"file": "EasyStandard.dat", "code": "EASY_ONLY"}, {"file": "HardStandard.dat", "code": "HARD_ONLY"}, {"file": None, "code": "PACKAGE"}]
    write_json(qa_path, qa)
    response = studio.get("/api/jobs/original/preview?difficulty=Hard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["difficulty"] == "Hard"
    assert len(payload["chart"]["colorNotes"]) == 7
    assert {item["code"] for item in payload["findings"]} == {"HARD_ONLY", "PACKAGE"}
    assert payload["difficulties"] == ["Easy", "Hard"]
    assert payload["duration"] == 8
    assert max(payload["waveform"]) > 0.5
    assert payload["audioUrl"] == "/api/jobs/original/audio"
    assert studio.get("/api/jobs/original/preview?difficulty=ExpertPlus").status_code == 404
    assert studio.get("/api/jobs/original/audio").content == (saved_map / "song.ogg").read_bytes()


def test_preview_rejects_info_audio_path_escape(studio, saved_map):
    outside = saved_map.parent / "private.ogg"
    outside.write_bytes(b"private")
    info = preview.read_json(saved_map / "Info.dat")
    info["_songFilename"] = "../private.ogg"
    write_json(saved_map / "Info.dat", info)
    assert studio.get("/api/jobs/original/preview").status_code == 404
    assert studio.get("/api/jobs/original/audio").status_code == 404


def test_waveform_cache_invalidates_when_audio_changes(saved_map):
    first = preview.preview_payload(saved_map)
    sf.write(saved_map / "song.ogg", np.zeros(44100, dtype=np.float32), 44100, format="OGG", subtype="VORBIS")
    second = preview.preview_payload(saved_map)
    assert first["duration"] == 8 and second["duration"] == 1
    assert max(second["waveform"]) == 0


def test_corrupt_matching_hash_waveform_cache_is_recomputed(saved_map):
    audio_hash = hashlib.sha256((saved_map / "song.ogg").read_bytes()).hexdigest()
    write_json(saved_map / "_beatforge" / "preview-waveform.json", {"audioHash": audio_hash, "duration": 8, "waveform": [float("nan")]})
    payload = preview.preview_payload(saved_map)
    assert payload["duration"] == 8
    assert len(payload["waveform"]) > 1
    assert all(np.isfinite(value) for value in payload["waveform"])
    assert max(payload["waveform"]) > 0.5


def test_stale_revision_hash_does_not_create_a_child(studio, saved_map):
    request = revision_payload(saved_map)
    existing = preview.read_json(saved_map / "EasyStandard.dat")
    existing["customData"]["changedAfterPreview"] = True
    write_json(saved_map / "EasyStandard.dat", existing)
    response = studio.post("/api/jobs/original/revise", json=request)
    assert response.status_code == 409
    assert [folder.name for folder in api.JOBS_DIR.iterdir()] == ["original"]


def test_audio_change_invalidates_revision_base_hash(studio, saved_map):
    request = revision_payload(saved_map)
    sf.write(saved_map / "song.ogg", np.zeros(44100, dtype=np.float32), 44100, format="OGG", subtype="VORBIS")
    response = studio.post("/api/jobs/original/revise", json=request)
    assert response.status_code == 409
    assert [folder.name for folder in api.JOBS_DIR.iterdir()] == ["original"]


def test_half_open_merge_preserves_every_outside_object_and_custom_metadata():
    original = chart([note(1), note(4), note(5), note(8), note(10)])
    original["obstacles"] = [{"b": 0, "d": 1, "x": 0, "w": 1, "h": 3}, {"b": 5, "d": 1, "x": 3, "w": 1, "h": 3}, {"b": 9, "d": 1, "x": 0, "w": 1, "h": 3}]
    original["sliders"] = [{"b": 1, "tb": 2}, {"b": 5, "tb": 6}, {"b": 9, "tb": 10}]
    original["basicBeatmapEvents"] = [{"b": 1, "i": 1}, {"b": 4, "i": 2}, {"b": 8, "i": 3}]
    candidate = chart([note(0, x=3), note(4, x=1), note(7, x=1), note(8, x=3)])
    candidate["obstacles"] = [{"b": 6, "d": 1, "x": 2, "w": 1, "h": 3}]
    candidate["sliders"] = [{"b": 5, "tb": 7}]
    candidate["basicBeatmapEvents"] = [{"b": 4, "i": 7}, {"b": 8, "i": 9}]
    before = copy.deepcopy(original)
    merged = preview.merge_section(original, candidate, 4, 8)
    assert original == before
    for collection in preview.COLLECTIONS:
        expected = [item for item in before.get(collection, []) if item["b"] < 4 or item["b"] >= 8]
        actual = [item for item in merged.get(collection, []) if item["b"] < 4 or item["b"] >= 8]
        assert actual == expected
    assert merged["customData"] == original["customData"]
    assert [item["x"] for item in merged["colorNotes"] if 4 <= item["b"] < 8] == [1, 1]
    assert next(item for item in merged["colorNotes"] if item["b"] == 8)["x"] == 0


@pytest.mark.parametrize("collection,object_", [
    ("sliders", {"b": 3, "tb": 5}), ("burstSliders", {"b": 6, "tb": 9}),
    ("obstacles", {"b": 3, "d": 2}), ("obstacles", {"b": 7, "d": 2}),
])
@pytest.mark.parametrize("on_candidate", [False, True])
def test_crossing_holds_and_walls_are_rejected_on_either_side(collection, object_, on_candidate):
    original, candidate = chart([]), chart([])
    (candidate if on_candidate else original)[collection] = [object_]
    with pytest.raises(ValueError, match="cuts through"):
        preview.merge_section(original, candidate, 4, 8)


def test_crossing_nested_light_event_is_rejected():
    original = chart([])
    original["lightColorEventBoxGroups"] = [{"b": 7, "g": 1, "e": [{"e": [{"b": 2, "c": 0}]}]}]
    with pytest.raises(ValueError, match="cuts through|timed|light"):
        preview.merge_section(original, chart([]), 4, 8)


def test_existing_crossing_hold_refuses_revision_before_copy(studio, saved_map):
    path = saved_map / "EasyStandard.dat"
    original = preview.read_json(path)
    original["sliders"] = [{"b": 5, "tb": 7}]
    write_json(path, original)
    response = studio.post("/api/jobs/original/revise", json=revision_payload(saved_map))
    assert response.status_code == 422
    assert [folder.name for folder in api.JOBS_DIR.iterdir()] == ["original"]


def test_source_bpm_events_require_explicit_conversion_before_revision(studio, saved_map):
    path = saved_map / "EasyStandard.dat"
    original = preview.read_json(path)
    original["bpmEvents"] = [{"b": 8, "m": 60}]
    write_json(path, original)
    response = studio.post("/api/jobs/original/revise", json=revision_payload(saved_map))
    assert response.status_code in (409, 422)
    assert [folder.name for folder in api.JOBS_DIR.iterdir()] == ["original"]


def test_revision_range_cannot_extend_beyond_audio_duration(studio, saved_map):
    before = (saved_map / "EasyStandard.dat").read_bytes()
    response = studio.post("/api/jobs/original/revise", json=revision_payload(saved_map, startBeat=0, endBeat=16.01))
    assert response.status_code == 422
    assert "beyond the song" in response.json()["detail"]
    assert [folder.name for folder in api.JOBS_DIR.iterdir()] == ["original"]
    assert (saved_map / "EasyStandard.dat").read_bytes() == before


def test_revision_creates_child_preserves_original_and_other_difficulty(studio, saved_map, monkeypatch):
    before = {path.relative_to(saved_map).as_posix(): path.read_bytes() for path in saved_map.rglob("*") if path.is_file()}
    replacement = chart([note(6, 1, x=1)])
    monkeypatch.setattr(choreography, "generate_all", lambda *args, **kwargs: ({"Easy": replacement}, {"fixture": "safe section"}))
    response = studio.post("/api/jobs/original/revise", json=revision_payload(saved_map))
    assert response.status_code == 200
    child_id = response.json()["id"]
    assert child_id != "original"
    status = api._read_status(child_id)
    assert status["revisionOf"] == "original"
    assert status["localStatus"] == "playtest_candidate"
    assert status["installed"] is False
    child = api._job_dir(child_id) / "map"
    for relative, data in before.items():
        assert (saved_map / relative).read_bytes() == data
    assert (child / "HardStandard.dat").read_bytes() == before["HardStandard.dat"]
    original_chart = json.loads(before["EasyStandard.dat"])
    revised_chart = preview.read_json(child / "EasyStandard.dat")
    assert [n for n in revised_chart["colorNotes"] if n["b"] < 6 or n["b"] >= 8] == [n for n in original_chart["colorNotes"] if n["b"] < 6 or n["b"] >= 8]
    assert next(n for n in revised_chart["colorNotes"] if n["b"] == 6)["x"] == 1
    provenance = preview.read_json(child / "_beatforge" / "provenance.json")
    assert not provenance.get("humanPlaytests") and not provenance.get("reviews") and not provenance.get("vrPlaytestPassed")
    assert (api._job_dir(child_id) / "map.zip").is_file()
    assert studio.get(f"/api/jobs/{child_id}/download").status_code == 200


def test_boundary_failure_keeps_invalid_child_uninstalled_and_undownloadable(studio, saved_map, monkeypatch):
    before = (saved_map / "EasyStandard.dat").read_bytes()
    # The new note is safe alone but cannot follow the retained note at beat 6.
    replacement = chart([note(6.1, 1)])
    monkeypatch.setattr(choreography, "generate_all", lambda *args, **kwargs: ({"Easy": replacement}, {}))
    response = studio.post("/api/jobs/original/revise", json=revision_payload(saved_map, startBeat=6.05))
    assert response.status_code == 200
    child_id = response.json()["id"]
    status = api._read_status(child_id)
    assert status["status"] == "invalid" and status["localStatus"] == "invalid"
    assert status["qa"]["errors"] > 0
    assert status["installed"] is False
    assert (saved_map / "EasyStandard.dat").read_bytes() == before
    assert not (api._job_dir(child_id) / "map.zip").exists()
    assert studio.get(f"/api/jobs/{child_id}/download").status_code == 409
    assert studio.post(f"/api/jobs/{child_id}/install").status_code == 409


def test_real_generator_projection_and_complete_validation_in_revision(studio, saved_map, monkeypatch, tmp_path):
    from timing_export import project_map_timing
    analysis_path = saved_map / "_beatforge" / "analysis.json"
    analysis = preview.read_json(analysis_path)
    # A quarter-second origin catches a missing authored-to-audio projection.
    for row in analysis["beatGrid"]:
        row["sample"] += 11025
        row["timeSeconds"] += 0.25
    analysis["beatGrid"] = [row for row in analysis["beatGrid"] if row["sample"] < analysis["durationSamples"]]
    write_json(analysis_path, analysis)
    for name in ("EasyStandard.dat", "HardStandard.dat"):
        write_json(saved_map / name, project_map_timing(preview.read_json(saved_map / name), analysis))
    real_generate = choreography.generate_all
    def isolated_generate(analysis, sections, seed, **kwargs):
        return real_generate(analysis, sections, seed, corpus_database=tmp_path / "no-corpus.sqlite", **kwargs)
    monkeypatch.setattr(choreography, "generate_all", isolated_generate)
    before = (saved_map / "EasyStandard.dat").read_bytes()
    response = studio.post("/api/jobs/original/revise", json=revision_payload(saved_map, startBeat=0, endBeat=16))
    assert response.status_code == 200
    child_id = response.json()["id"]
    status = api._read_status(child_id)
    assert status.get("localStatus") == "playtest_candidate", status
    child = api._job_dir(child_id) / "map"
    assert not validate_package(child).errors
    assert (saved_map / "EasyStandard.dat").read_bytes() == before
    notes = preview.read_json(child / "EasyStandard.dat")["colorNotes"]
    assert notes
    assert all(abs((item["b"] % 1.0) - 0.5) < 1e-6 for item in notes)
    assert (api._job_dir(child_id) / "map.zip").is_file()
