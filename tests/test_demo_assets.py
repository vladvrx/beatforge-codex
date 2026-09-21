"""The public demo must show the packaged charts and the real decoded audio."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf
import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "web" / "assets" / "demo"
SCRIPTS = ROOT / "skills" / "beat-saber-mapping" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_map import validate_package
from timing_export import ExportClock, project_map_timing, project_plan_timing
from mapping_plan import build_section_plan
from beatforge.preview import chart_identity, waveform


def test_demo_audio_decodes_to_the_advertised_30_seconds() -> None:
    preview = json.loads((DEMO / "preview.json").read_text())
    audio, sample_rate = sf.read(DEMO / "song.ogg", always_2d=True)
    assert sample_rate == 44100
    assert audio.shape == (30 * 44100, 2)
    assert preview["duration"] == len(audio) / sample_rate == 30.0
    assert float(np.sqrt(np.mean(audio ** 2))) > 0.02
    assert float(np.max(np.abs(audio))) < 1
    assert len(preview["waveform"]) >= 1000
    assert max(preview["waveform"]) > 0.2
    assert len(set(preview["waveform"])) > 100
    duration, peaks = waveform(DEMO / "song.ogg")
    assert preview["duration"] == duration
    # libsndfile/Vorbis builds can straddle a four-decimal rounding boundary.
    # Keep every bin aligned and permit only one stored quantization step.
    np.testing.assert_allclose(preview["waveform"], peaks, rtol=0, atol=0.00010001)
    assert preview["provenance"]["audio"]["sha256"] == hashlib.sha256((DEMO / "song.ogg").read_bytes()).hexdigest()


def test_demo_zip_and_displayed_charts_pass_canonical_validation(tmp_path: Path) -> None:
    preview = json.loads((DEMO / "preview.json").read_text())
    with zipfile.ZipFile(DEMO / "map.zip") as archive:
        assert archive.read("song.ogg") == (DEMO / "song.ogg").read_bytes()
        archive.extractall(tmp_path)
        info = json.loads(archive.read("Info.dat"))
        refs = info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"]
        assert len(refs) == 5
        for entry in refs:
            name = entry["_difficulty"]
            chart = json.loads(archive.read(entry["_beatmapFilename"]))
            assert preview["charts"][name] == chart
            assert preview["summary"]["difficulties"][name]["notes"] == len(chart["colorNotes"]) > 0
            for field, collection in (("arcs", "sliders"), ("chains", "burstSliders"), ("bombs", "bombNotes"), ("walls", "obstacles")):
                assert preview["summary"]["difficulties"][name][field] == len(chart[collection])
    assert preview["chart"] == preview["charts"]["Hard"]
    assert preview["difficulty"] == "Hard"
    validation = validate_package(tmp_path)
    assert not validation.errors, validation.to_dict()
    assert validation.status == "playtest_candidate"
    assert preview["qa"]["errors"] == []
    assert preview["qa"]["metrics"]["totals"] == validation.to_dict()["metrics"]["totals"]
    assert preview["chartHash"] == chart_identity(tmp_path)
    assert preview["audioHash"] == hashlib.sha256((tmp_path / "song.ogg").read_bytes()).hexdigest()


def test_demo_timing_provenance_describes_the_authored_score_honestly() -> None:
    preview = json.loads((DEMO / "preview.json").read_text())
    with zipfile.ZipFile(DEMO / "map.zip") as archive:
        analysis = json.loads(archive.read("_beatforge/analysis.json"))
        provenance = json.loads(archive.read("_beatforge/provenance.json"))
    assert analysis["source"] == preview["source"] == "synthetic-authored-score"
    assert analysis["timingEvidence"]["authoredSampleClock"] is True
    assert analysis["timingEvidence"]["trackerAnalysisPerformed"] is False
    assert analysis["timingEvidence"]["humanListeningVerified"] is False
    for event in analysis["events"]:
        assert event["sample"] == round(event["beat"] * 60 / analysis["bpm"] * analysis["sampleRate"])
        assert 0 <= event["sample"] < round(analysis["durationSeconds"] * analysis["sampleRate"])
    assert provenance["officialCorpusUsed"] is False
    assert provenance["humanPlaytests"] == []
    assert provenance["releaseGate"]["fullSpeedVrPlaytest"] is False
    assert provenance["releaseGate"]["freshSightRead"] is False
    assert provenance["releaseGate"]["structuralInspection"] is True
    assert preview["sections"][0]["startBeat"] == 0
    assert preview["sections"][-1]["endBeat"] == 60


def test_demo_grid_and_raw_sections_are_ready_for_safe_local_revision() -> None:
    preview = json.loads((DEMO / "preview.json").read_text())
    with zipfile.ZipFile(DEMO / "map.zip") as archive:
        analysis = json.loads(archive.read("_beatforge/analysis.json"))
        grid = json.loads(archive.read("_beatforge/beat_grid.json"))
        sections = json.loads(archive.read("_beatforge/sections.json"))
        plan = json.loads(archive.read("_beatforge/mapping_plan.json"))
        report = json.loads(archive.read("_beatforge/choreography_report.json"))
        provenance = json.loads(archive.read("_beatforge/provenance.json"))
    assert analysis["durationSamples"] == 30 * 44100
    assert grid["beats"] == analysis["beatGrid"]
    assert grid["tempoRegions"] == analysis["tempoRegions"]
    assert grid["sampleRate"] == analysis["sampleRate"] == 44100
    rows = grid["beats"]
    assert len(rows) >= 60
    for row in rows:
        assert row["sample"] == round(row["beat"] * 60 / analysis["bpm"] * analysis["sampleRate"])
        assert 0 <= row["sample"] < analysis["durationSamples"]
        assert row["timeSeconds"] == row["sample"] / analysis["sampleRate"]
    assert all(right["sample"] > left["sample"] and right["beat"] > left["beat"] for left, right in zip(rows, rows[1:]))
    assert "controls" not in sections
    assert all("authoredStartBeat" not in section and "density" not in section for section in sections["sections"])
    expected = project_plan_timing(build_section_plan(analysis, sections, plan["controls"]), analysis)
    assert plan == expected == report["mappingPlan"] == provenance["mappingPlan"] == preview["mappingPlan"]
    assert preview["sections"] == plan["sections"]
    assert plan["sections"][-1]["endSeconds"] == preview["duration"]


def test_demo_gameplay_and_hold_endpoints_use_the_exported_sample_clock_once() -> None:
    preview = json.loads((DEMO / "preview.json").read_text())
    with zipfile.ZipFile(DEMO / "map.zip") as archive:
        analysis = json.loads(archive.read("_beatforge/analysis.json"))
    clock = ExportClock(analysis)
    for event in analysis["events"]:
        exported_seconds = clock.beat(event["beat"]) * 60 / analysis["bpm"]
        assert abs(exported_seconds * analysis["sampleRate"] - event["sample"]) < 0.001
    for chart in preview["charts"].values():
        assert chart["customData"]["_beatforgeTiming"]["clock"] == "constant-bpm-sample-projection"
        assert chart["bpmEvents"] == []
        for collection in ("colorNotes", "bombNotes", "sliders", "burstSliders"):
            for item in chart.get(collection, []):
                for field in ("b", "tb"):
                    if field not in item:
                        continue
                    sample = item[field] * 60 / analysis["bpm"] * analysis["sampleRate"]
                    assert abs(sample - round(sample)) < 0.001
                    assert 0 <= sample < analysis["durationSamples"]
        with pytest.raises(ValueError, match="already been projected"):
            project_map_timing(chart, analysis)
