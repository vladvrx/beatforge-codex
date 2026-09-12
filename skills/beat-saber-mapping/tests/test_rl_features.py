"""Regression checks for real analysis clocks and truthful RL inputs."""

import json

import numpy as np
import pytest

from rl.features import build_analysis_features, load_analysis_features, STEMS
from rl.library import load_library


def documents():
    # Offset is half a second; tempo changes from 120 to 60 at beat 1.
    rows = [{"beat": beat, "sample": sample} for beat, sample in
            [(0, 22050), (.5, 33075), (1, 44100), (1.5, 66150), (2, 88200)]]
    analysis = {"status": "timing_verified", "sampleRate": 44100, "durationSamples": 110250,
                "bpm": 120, "sourceSha256": "a" * 64,
                "events": [{"sample": 44100, "stemEnergy": {"drums": .8, "bass": .2}}]}
    grid = {"sampleRate": 44100, "status": "timing_verified", "beats": rows, "source": "confirmed"}
    sections = {"source": "all-in-one", "sections": [
        {"label": "verse", "startSeconds": 0, "endSeconds": 1, "startBeat": 0, "endBeat": 99},
        {"label": "chorus", "startSeconds": 1, "endSeconds": 2.5, "startBeat": 99, "endBeat": 100},
    ]}
    frames = {"times": np.arange(11) * .25, "combinedOnset": np.arange(11) / 10,
              "flux": np.linspace(1, 0, 11)}
    return analysis, grid, sections, frames


def write_analysis(path, *, source="a" * 64):
    path.mkdir(parents=True, exist_ok=True)
    analysis, grid, sections, frames = documents()
    analysis["sourceSha256"] = source
    for name, doc in [("analysis.json", analysis), ("beat_grid.json", grid), ("sections.json", sections)]:
        (path / name).write_text(json.dumps(doc), encoding="utf-8")
    np.savez_compressed(path / "audio_features.npz", **frames)
    return path


def test_offset_and_tempo_changes_use_actual_sample_positions():
    result = build_analysis_features(*documents())
    assert result.beat_grid == [i / 4 for i in range(9)]
    assert result.audio_features["samplePositions"] == [22050, 27562, 33075, 38588, 44100, 55125, 66150, 77175, 88200]
    assert result.audio_features["onsets"][0] == pytest.approx(.2)
    assert result.audio_features["onsets"][-1] == pytest.approx(.8)
    assert result.audio_features["stepBpms"][:4] == pytest.approx([120] * 4)
    assert result.audio_features["stepBpms"][4:] == pytest.approx([60] * 5)
    assert result.provenance["durationSamples"] == 110250


def test_sections_use_seconds_over_stale_constant_tempo_beat_fields():
    result = build_analysis_features(*documents())
    assert result.audio_features["sections"] == ["verse"] * 4 + ["chorus"] * 5


def test_stems_are_real_localized_annotations_and_missing_stems_are_honest():
    result = build_analysis_features(*documents())
    audio = result.audio_features
    assert audio["stems"]["drums"] == [0, 0, 0, 0, .8, 0, 0, 0, 0]
    assert audio["stemAvailability"]["drums"] is True
    assert audio["stemAvailability"]["vocals"] is False
    assert audio["stems"]["vocals"] == [0] * 9
    analysis, grid, sections, frames = documents()
    analysis["events"] = []
    missing = build_analysis_features(analysis, grid, sections, frames)
    assert all(not missing.audio_features["stemAvailability"][stem] for stem in STEMS)
    assert all(not any(values) for values in missing.audio_features["stems"].values())


def test_hop_clock_does_not_stretch_frames_to_grid_length():
    analysis, grid, sections, _ = documents()
    frames = {"hopSamples": 11025, "combinedOnset": np.arange(11) / 10}
    result = build_analysis_features(analysis, grid, sections, frames)
    assert result.audio_features["onsets"][0] == pytest.approx(.2)
    assert result.audio_features["onsets"][-1] == pytest.approx(.8)
    assert result.audio_features["flux"] == [0] * 9
    assert result.provenance["fluxAvailable"] is False


@pytest.mark.parametrize("bad", [float("nan"), 33074.5, 22050])
def test_invalid_sample_grid_fails(bad):
    args = list(documents())
    args[1]["beats"][1]["sample"] = bad
    with pytest.raises(ValueError, match="strictly increasing"):
        build_analysis_features(*args)


def test_unconfirmed_timing_requires_explicit_generation_override():
    analysis, grid, sections, frames = documents()
    analysis["status"] = grid["status"] = "needs_anchors"
    with pytest.raises(ValueError, match="verified"):
        build_analysis_features(analysis, grid, sections, frames)
    allowed = build_analysis_features(analysis, grid, sections, frames, require_verified=False)
    assert allowed.provenance["timingStatus"] == "needs_anchors"


def test_artifact_hashes_and_repeatability(tmp_path):
    path = write_analysis(tmp_path / "analysis")
    first = load_analysis_features(path)
    assert first == load_analysis_features(path)
    assert len(first.provenance["artifactSha256"]) == 4
    (path / "audio_features.npz").unlink()
    with pytest.raises(FileNotFoundError, match="Run analyze_audio"):
        load_analysis_features(path)


def test_library_rejects_content_overlap_even_if_track_names_differ(tmp_path):
    write_analysis(tmp_path / "one")
    write_analysis(tmp_path / "two")
    manifest = tmp_path / "library.json"
    manifest.write_text(json.dumps({"schemaVersion": 1, "tracks": [
        {"id": "first", "analysisDir": "one", "split": "train"},
        {"id": "different-name", "analysisDir": "two", "split": "validation"},
    ]}))
    with pytest.raises(ValueError, match="split leakage"):
        load_library(manifest)
