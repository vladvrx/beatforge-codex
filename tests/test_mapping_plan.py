from __future__ import annotations

import json
from pathlib import Path
import sys
import threading

import pytest

from beatforge.mapping_plan import build_section_plan, normalize_mapping_plan

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "beat-saber-mapping" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import choreography
from timing_export import ExportClock, project_map_timing, project_plan_timing


def analysis_fixture() -> tuple[dict, dict]:
    analysis = {
        "bpm": 120.0, "durationSeconds": 32.0,
        "events": [{"beat": i * 0.5, "strength": (i % 5 + 1) / 5.0, "layer": "mix"} for i in range(4, 128)],
    }
    sections = {"source": "fixture", "sections": [
        {"label": "verse 1", "startBeat": 0, "endBeat": 32, "intensity": 0.5},
        {"label": "chorus 1", "startBeat": 32, "endBeat": 48, "intensity": 0.8},
        {"label": "chorus 2", "startBeat": 48, "endBeat": 64, "intensity": 0.8},
    ]}
    return analysis, sections


def test_supported_brief_has_explicit_precedence_and_roundtrips():
    result = normalize_mapping_plan({"brief": "Flowing, sparse verses, bigger chorus, no bombs; follow the bass", "chorusDensity": 1.1})
    assert result["noBombs"] is True
    assert result["verseDensity"] == 0.75
    assert result["chorusDensity"] == 1.1
    assert result["style"] == "flow"
    assert result["dominantInstrument"] == "bass"
    assert normalize_mapping_plan(json.loads(json.dumps(result))) == result


@pytest.mark.parametrize("value", [
    {"density": 2}, {"density": True}, {"density": float("nan")},
    {"noBombs": "false"}, {"candidateCount": 0}, {"schemaVersion": 2},
    {"dominantInstrument": "rain"}, {"style": "unsafe"}, {"typo": 1},
    {"sectionOverrides": [{"id": "x"}, {"id": "x"}]},
])
def test_invalid_controls_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_mapping_plan(value)


def test_section_override_preserves_other_sections_and_repeated_motif():
    analysis, sections = analysis_fixture()
    initial = build_section_plan(analysis, sections, {})
    changed = build_section_plan(analysis, sections, {"sectionOverrides": [{"id": "section-001", "density": 0.5}]})
    assert changed["sections"][0] == initial["sections"][0]
    assert changed["sections"][2] == initial["sections"][2]
    assert changed["sections"][1]["density"] == 0.5
    assert changed["sections"][1]["motifId"] == changed["sections"][2]["motifId"] == "chorus"
    with pytest.raises(ValueError, match="unknown section"):
        build_section_plan(analysis, sections, {"sectionOverrides": [{"id": "missing"}]})


def test_density_changes_real_intent_selection_without_changing_recovery_limits():
    analysis, sections = analysis_fixture()
    original_recovery = choreography.CONFIGS["Expert"].recovery_beats
    sparse = choreography.select_intents(analysis, build_section_plan(analysis, sections, {"density": 0.5}), "Expert")
    dense = choreography.select_intents(analysis, build_section_plan(analysis, sections, {"density": 1.5}), "Expert")
    assert len(sparse) < len(dense)
    assert choreography.CONFIGS["Expert"].recovery_beats == original_recovery
    assert all(right["beat"] - left["beat"] >= choreography.CONFIGS["Expert"].peak_min_gap for left, right in zip(dense, dense[1:]))


def test_dominant_instrument_selects_its_actual_stem_accents():
    analysis, sections = analysis_fixture()
    for index, event in enumerate(analysis["events"]):
        event["stemEnergy"] = {"bass": 1.0 if index % 2 else 0.0, "drums": 0.0 if index % 2 else 1.0}
        event["strength"] = 0.5
    bass = choreography.select_intents(analysis, build_section_plan(analysis, sections, {"dominantInstrument": "bass"}), "Normal")
    drums = choreography.select_intents(analysis, build_section_plan(analysis, sections, {"dominantInstrument": "drums"}), "Normal")
    assert [item["beat"] for item in bass] != [item["beat"] for item in drums]


def test_repeated_chorus_reuses_local_pose_preferences():
    analysis, sections = analysis_fixture()
    # Identical sound at the same local beat of two chorus instances.
    analysis["events"] = [{"beat": beat, "strength": 3.0} for beat in (34, 50)]
    planned = build_section_plan(analysis, sections, {"density": 1.5})
    intents = [item for item in choreography.select_intents(analysis, planned, "Hard") if item["beat"] in (34, 50)]
    assert len(intents) == 2
    domains = [choreography._pose_domain(intent, 0, 0, index, 120, choreography.CONFIGS["Hard"].recovery_beats) for index, intent in enumerate(intents)]
    shapes = [[(pose["head"]["x"], pose["head"]["y"], pose["head"]["d"]) for pose in domain] for domain in domains]
    assert shapes[0] == shapes[1]


def simple_map(notes):
    return {"version": "3.3.0", "colorNotes": notes, "bombNotes": [], "obstacles": [], "sliders": [], "burstSliders": []}


def test_high_corpus_score_cannot_select_an_unsafe_candidate():
    good = simple_map([{"b": 4, "x": 0, "y": 0, "c": 0, "d": 1, "a": 0}])
    bad = simple_map([
        {"b": 4, "x": 0, "y": 1, "c": 0, "d": 5, "a": 0},
        {"b": 4, "x": 2, "y": 0, "c": 1, "d": 4, "a": 0},
    ])
    safe_rank = choreography.rank_choreography_candidate(good, "Expert", 120, {"score": 0}, {}, 1)
    unsafe_rank = choreography.rank_choreography_candidate(bad, "Expert", 120, {"score": 100}, {}, 1)
    assert safe_rank["eligible"]
    assert not unsafe_rank["eligible"]
    assert choreography.select_ranked_candidate([{"index": 0, **unsafe_rank}, {"index": 1, **safe_rank}])["index"] == 1
    with pytest.raises(ValueError, match="safety gates"):
        choreography.select_ranked_candidate([{"index": 0, **unsafe_rank}])


def test_corpus_ranking_changes_the_actual_winner():
    chart = simple_map([{"b": 4, "x": 0, "y": 0, "c": 0, "d": 1, "a": 0}])
    candidates = [{"index": i, **choreography.rank_choreography_candidate(chart, "Expert", 120, {"score": score}, {}, 1)} for i, score in enumerate((20, 90))]
    assert choreography.select_ranked_candidate(candidates)["index"] == 1


def test_premium_plan_generates_a_valid_accessible_chart(tmp_path):
    analysis, sections = analysis_fixture()
    # Keep the real solver fixture small; the unit rank test covers multiple candidates.
    analysis["durationSeconds"] = 8.0
    analysis["events"] = [event for event in analysis["events"] if event["beat"] < 16]
    sections["sections"] = [{"label": "verse", "startBeat": 0, "endBeat": 16, "intensity": 0.5}]
    maps, report = choreography.generate_all(analysis, sections, 123, tmp_path / "missing.sqlite", ("Easy",), mapping_plan={"candidateCount": 1, "noBombs": True, "noWalls": True})
    chart = maps["Easy"]
    assert chart["colorNotes"]
    assert chart["bombNotes"] == chart["obstacles"] == []
    assert report["candidateRanking"]["Easy"]["candidates"][0]["eligible"]
    assert report["mappingPlan"]["sections"][0]["id"] == "section-000"


def test_premium_cancellation_stops_a_real_spawned_mapper(tmp_path, monkeypatch):
    from beatforge import premium
    script = tmp_path / "generate_map.py"
    script.write_text("import sys, time\nprint('BEATFORGE_PROGRESS\\trunning\\tready', file=sys.stderr, flush=True)\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(premium, "SCRIPTS", tmp_path)
    cancelled = threading.Event()
    def progress(stage, detail, **kwargs):
        if stage == "running":
            cancelled.set()
    with pytest.raises(premium.PipelineCancelled):
        premium.run_premium_pipeline(audio=tmp_path / "song.wav", output=tmp_path / "out", title="Test", artist="Test", mapper="Test", seed=1, anchors=None, palette=None, progress=progress, cancel_requested=cancelled.is_set)


def test_export_uses_actual_offset_and_variable_tempo_for_notes_holds_and_lights():
    # Beat zero is 0.25 seconds into the song. The second bar doubles in duration.
    analysis = {"bpm": 120, "sampleRate": 44100, "beatGrid": [
        {"beat": 0, "sample": 11025}, {"beat": 4, "sample": 99225},
        {"beat": 8, "sample": 275625},
    ]}
    chart = simple_map([{"b": 6, "x": 0, "y": 0, "c": 0, "d": 1, "a": 0}])
    chart["sliders"] = [{"b": 3, "tb": 5}]
    chart["burstSliders"] = [{"b": 4, "tb": 5}]
    chart["obstacles"] = [{"b": 3, "d": 2}]
    chart["basicBeatmapEvents"] = [{"b": 6, "et": 1, "i": 5, "f": 1.0}]
    chart["lightColorEventBoxGroups"] = [{"b": 4, "g": 0, "e": [{"f": {"b": 1}, "e": [{"b": 1, "c": 0}]}]}]
    exported = project_map_timing(chart, analysis)
    assert exported["colorNotes"][0]["b"] == 8.5
    assert exported["sliders"][0] == {"b": 3.5, "tb": 6.5}
    assert exported["burstSliders"][0] == {"b": 4.5, "tb": 6.5}
    assert exported["obstacles"][0] == {"b": 3.5, "d": 3.0}
    assert exported["basicBeatmapEvents"][0]["b"] == 8.5
    box = exported["lightColorEventBoxGroups"][0]
    assert box["b"] == 4.5
    assert box["e"][0]["e"][0]["b"] == 2
    assert box["e"][0]["f"]["b"] == 1
    assert chart["colorNotes"][0]["b"] == 6  # authored input was preserved
    with pytest.raises(ValueError, match="already been projected"):
        project_map_timing(exported, analysis)


def test_exported_section_boundaries_match_notes_for_preview_and_revision():
    analysis = {"bpm": 120, "sampleRate": 44100, "beatGrid": [{"beat": 0, "sample": 11025}, {"beat": 8, "sample": 187425}]}
    plan = {"sections": [{"id": "section-001", "startBeat": 2, "endBeat": 4}]}
    projected = project_plan_timing(plan, analysis)
    section = projected["sections"][0]
    assert section["startBeat"] == ExportClock(analysis).beat(2)
    assert section["endBeat"] == ExportClock(analysis).beat(4)
    assert section["startSeconds"] == 1.25
    assert section["authoredStartBeat"] == 2
    assert plan["sections"][0]["startBeat"] == 2
