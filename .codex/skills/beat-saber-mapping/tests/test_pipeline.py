from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
import sys
import wave
from pathlib import Path

import numpy as np
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from beatforge_core import (
    Anchor,
    AudioBuffer,
    MOTION_FINDING_FIELDS,
    ValidationReport,
    analyze_audio_buffer,
    build_grid_from_anchors,
    ffmpeg_clock_to_seconds,
    parse_difficulties,
    propose_and_verify_anchors,
)
from bootstrap import default_target, python_abi_tag, torch_abi_mismatch, verify_model_imports
import analyze_audio
from choreography import CONFIGS, generate_all, realize, select_intents, solve_hands, _pose_domain, _solve_hazards, _solve_joint_assignment, _solve_joint_model, _pose_blocks_bomb, build_bomb_candidates, _joint_flow_is_legal, build_lighting, lighting_object_count, _candidate_notes, cut_for
from kinematics import double_is_safe, saber_paths_intersect, transition_finding
from artwork import pick_cover_art_image, metadata_match_confidence
from generate_map import (
    info_dat,
    refuse_protected_output,
    write_default_cover,
    _load_approved_palette,
    _copy_file,
)
from official_corpus import (
    connect,
    detect_game_root,
    extract_bundle_assets,
    index_pack_definitions,
    insert_bundle,
    load_json_bytes,
    normalize_beatmap,
    report,
)
from validate_map import validate_package


GAME_ROOT = Path(r"C:\Program Files\Oculus\Software\Software\hyperbolic-magnetism-beat-saber")


def unitypy_is_importable() -> bool:
    try:
        import UnityPy
    except (ImportError, OSError):
        return False
    return callable(getattr(UnityPy, "load", None))


def synthetic_audio(seconds: float = 24.0, bpm: float = 120.0) -> AudioBuffer:
    rate = 44_100
    samples = np.zeros(int(seconds * rate), dtype=np.float32)
    for beat in np.arange(0.0, seconds * bpm / 60.0, 1.0):
        start = int(round(beat * 60.0 / bpm * rate))
        length = min(900, len(samples) - start)
        if length > 0:
            samples[start : start + length] += np.exp(-np.arange(length) / 150.0).astype(np.float32)
    return AudioBuffer(samples, rate, 1, len(samples), "0" * 64)


def test_ffmpeg_clock_to_seconds_parses_progress_fields() -> None:
    assert ffmpeg_clock_to_seconds("00:00:12.500000") == pytest.approx(12.5)
    assert ffmpeg_clock_to_seconds("12500000") == pytest.approx(12.5)
    assert ffmpeg_clock_to_seconds("N/A") is None


def test_parse_difficulties_keeps_canonical_order_and_aliases() -> None:
    assert parse_difficulties(["Expert+", "easy"]) == ("Easy", "ExpertPlus")
    with pytest.raises(ValueError):
        parse_difficulties(["Insane"])


def test_torch_bootstrap_rejects_wrong_interpreter_abi(tmp_path: Path) -> None:
    dist = tmp_path / "torch-2.13.0.dist-info"
    dist.mkdir()
    (dist / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: false\nTag: cp312-cp312-win_amd64\n",
        encoding="utf-8",
    )
    mismatch = torch_abi_mismatch(tmp_path)
    if python_abi_tag() == "cp312":
        assert mismatch is None
    else:
        assert mismatch is not None
        assert "cp312-cp312-win_amd64" in mismatch
        assert python_abi_tag() in mismatch


def test_pinned_model_imports_verify_without_shadowing_venv_numpy() -> None:
    try:
        report = verify_model_imports(default_target())
    except RuntimeError as error:
        pytest.skip(str(error))
    for name in ("torch", "beat_this", "demucs", "all-in-one", "beatnet-plus"):
        assert name in report
        assert not str(report[name]).startswith("ERROR:")


def test_anchor_grid_is_sample_accurate_and_piecewise() -> None:
    anchors = [Anchor(0.0, 441, "downbeat"), Anchor(16.0, 353_241, "downbeat"), Anchor(32.0, 700_000, "downbeat")]
    grid, regions = build_grid_from_anchors(anchors, 800_000)
    by_beat = {item["beat"]: item["sample"] for item in grid}
    assert by_beat[0.0] == 441
    assert by_beat[16.0] == 353_241
    assert by_beat[32.0] == 700_000
    assert len(regions) == 2
    assert regions[0]["bpm"] != regions[1]["bpm"]


def test_unconfirmed_internal_analysis_refuses_to_generate() -> None:
    analysis = analyze_audio_buffer(synthetic_audio())
    assert analysis["status"] == "needs_anchors"
    assert len(analysis["anchorSuggestions"]) == 3
    assert all("suggestedBeat" in item and item.get("unconfirmed") is True for item in analysis["anchorSuggestions"])
    evidence = analysis["clickTrackEvidence"]
    labels = [item["label"] for item in evidence["checkpoints"]]
    assert labels == ["start", "middle", "end"]
    assert all(int(item["sample"]) >= 0 for item in evidence["checkpoints"])
    assert evidence["checkpoints"][0]["sample"] < evidence["checkpoints"][2]["sample"]


def test_external_agreement_refuses_p95_and_cumulative_drift() -> None:
    aligned = [index * 0.5 for index in range(40)]
    drifted = [time * (1.0 + 3.040 / (aligned[-1] - aligned[0])) for time in aligned]
    noisy = [time + (0.012 if index % 5 == 0 else 0.0) for index, time in enumerate(aligned)]
    agreement = analyze_audio.pairwise_beat_agreement(
        [
            {"name": "beat-this", "beatsSeconds": aligned},
            {"name": "all-in-one", "beatsSeconds": drifted},
            {"name": "beatnet-plus", "beatsSeconds": noisy},
        ]
    )
    assert agreement["trackCount"] == 3
    assert agreement["maximumCumulativeDriftMs"] > 20.0
    assert not analyze_audio.external_agreement_passes(agreement)
    tight = analyze_audio.pairwise_beat_agreement(
        [
            {"name": "beat-this", "beatsSeconds": aligned},
            {"name": "beatnet-plus", "beatsSeconds": aligned},
        ]
    )
    assert analyze_audio.external_agreement_passes(tight)


def test_all_in_one_cannot_veto_the_beat_this_clock() -> None:
    aligned = [index * 0.5 for index in range(40)]
    drifted = [time * (1.0 + 3.040 / (aligned[-1] - aligned[0])) for time in aligned]
    tracks = [
        {"name": "beat-this", "beatsSeconds": aligned},
        {"name": "all-in-one", "beatsSeconds": drifted},
        {"name": "beatnet-plus", "beatsSeconds": aligned},
    ]
    clocks = analyze_audio.clock_tracks(tracks)
    assert analyze_audio.select_primary_track(tracks)["name"] == "beat-this"
    assert analyze_audio.external_agreement_passes(analyze_audio.pairwise_beat_agreement(clocks))
    assert not analyze_audio.external_agreement_passes(analyze_audio.pairwise_beat_agreement(tracks))


def test_dual_clock_vs_grid_requires_both_models() -> None:
    grid = [{"sample": int(round(index * 0.5 * 44100))} for index in range(40)]
    aligned = [index * 0.5 for index in range(40)]
    analysis = {
        "sampleRate": 44100,
        "beatGrid": grid,
        "trackers": [
            {"name": "beat-this", "beatsSeconds": aligned},
            {"name": "beatnet-plus", "beatsSeconds": aligned},
            {"name": "all-in-one", "beatsSeconds": [time + 0.2 for time in aligned]},
        ],
    }
    check = analyze_audio.dual_clock_vs_grid(analysis)
    assert check["stage"] == "post-ai"
    assert check["passes"] is True
    assert "all-in-one" not in check["models"]


def test_down_to_up_return_swing_is_legal_at_half_beat() -> None:
    previous = {"b": 4.0, "x": 1, "y": 1, "c": 0, "d": 1}
    current = {"b": 4.5, "x": 1, "y": 1, "c": 0, "d": 0}
    assert transition_finding(previous, current, 120.0, recovery_beats=0.22) is None


def test_far_lane_lean_reversal_is_illegal_within_half_beat() -> None:
    previous = {"b": 4.0, "x": 0, "y": 1, "c": 0, "d": 1}
    current = {"b": 4.5, "x": 3, "y": 1, "c": 0, "d": 0}
    finding = transition_finding(previous, current, 60.0, recovery_beats=0.25)
    assert finding is not None
    assert finding["failedConstraint"] == "body_lean_reversal"


def test_follow_through_momentum_cannot_fight_the_next_pre_swing() -> None:
    previous = {"b": 2.0, "x": 2, "y": 1, "c": 0, "d": 3}
    current = {"b": 2.75, "x": 0, "y": 1, "c": 0, "d": 1}
    finding = transition_finding(previous, current, 60.0, recovery_beats=0.75)
    assert finding is not None
    assert finding["failedConstraint"] == "rotational_momentum"


def test_one_hundred_thirty_five_degree_reversal_at_recovery_is_illegal() -> None:
    previous = {"b": 8.0, "x": 0, "y": 0, "c": 0, "d": 1}
    current = {"b": 8.22, "x": 3, "y": 2, "c": 0, "d": 5}
    finding = transition_finding(previous, current, 180.0, recovery_beats=0.22)
    assert finding is not None
    assert finding["failedConstraint"] == "impossible_rotational_reversal"


def test_one_hundred_thirty_five_degree_reversal_is_illegal_across_combo_spacing() -> None:
    previous = {"b": 4.0, "x": 1, "y": 1, "c": 1, "d": 1}
    current = {"b": 4.5, "x": 2, "y": 1, "c": 1, "d": 5}
    finding = transition_finding(previous, current, 120.0, recovery_beats=0.22)
    assert finding is not None
    assert finding["failedConstraint"] == "impossible_rotational_reversal"


def test_left_right_return_is_legal_at_combo_spacing() -> None:
    previous = {"b": 4.0, "x": 0, "y": 1, "c": 0, "d": 2}
    current = {"b": 4.5, "x": 0, "y": 1, "c": 0, "d": 3}
    assert transition_finding(previous, current, 120.0, recovery_beats=0.3) is None


def test_bombs_stay_off_note_and_swing_cells() -> None:
    notes = [{"b": 1.0, "x": 0, "y": 2, "c": 0, "d": 1}]
    intents = [{"beat": 0.0, "section": "body"}, {"beat": 3.0, "section": "body"}]
    bombs = build_bomb_candidates(notes, intents, "ExpertPlus")
    for bomb in bombs:
        assert not (int(bomb["x"]) == 0 and int(bomb["y"]) == 2 and abs(float(bomb["b"]) - 1.0) <= 0.75)


def test_joint_sat_forbids_inward_handclap_doubles() -> None:
    intents = [
        {"beat": 4.0, "duration": 0.0, "kind": "note", "intensity": 0.85},
        {"beat": 4.0, "duration": 0.0, "kind": "note", "intensity": 0.85},
    ]
    result = _solve_joint_assignment(intents, CONFIGS["ExpertPlus"], 120.0)
    assert result is not None
    _hands, poses, label = result[:3]
    assert label == "joint-cp-sat"
    placed = [pose["head"] for pose in poses if pose is not None]
    red = [note for note in placed if int(note["c"]) == 0]
    blue = [note for note in placed if int(note["c"]) == 1]
    if len(red) == 1 and len(blue) == 1:
        assert double_is_safe(red[0], blue[0])
        assert not saber_paths_intersect(red[0], blue[0])


def test_joint_sat_simultaneous_doubles_are_path_safe() -> None:
    intents = [
        {"beat": 8.0, "duration": 0.0, "kind": "note", "intensity": 0.9},
        {"beat": 8.0, "duration": 0.0, "kind": "note", "intensity": 0.9},
    ]
    result = _solve_joint_assignment(intents, CONFIGS["ExpertPlus"], 140.0)
    assert result is not None
    _hands, poses, label = result[:3]
    assert label == "joint-cp-sat"
    placed = [pose["head"] for pose in poses if pose is not None]
    red = [note for note in placed if int(note["c"]) == 0]
    blue = [note for note in placed if int(note["c"]) == 1]
    if red and blue:
        assert double_is_safe(red[0], blue[0])
        assert not saber_paths_intersect(red[0], blue[0])


def test_joint_sat_moves_notes_off_forced_bomb_cells() -> None:
    intents = [
        {"beat": 4.0, "duration": 0.0, "kind": "note", "intensity": 0.9, "section": "body"},
        {"beat": 8.0, "duration": 0.0, "kind": "note", "intensity": 0.9, "section": "body"},
    ]
    bomb = {"b": 4.0, "x": 0, "y": 2}
    result = _solve_joint_model(
        intents,
        CONFIGS["ExpertPlus"],
        120.0,
        bomb_candidates=[bomb],
        wall_candidates=[],
        difficulty="ExpertPlus",
    )
    assert result is not None
    _hands, poses, label, bombs, walls = result
    assert label == "joint-cp-sat"
    assert not walls
    assert bombs == [bomb]
    for pose in poses:
        if pose is None:
            continue
        assert not _pose_blocks_bomb(pose, bomb)


def test_joint_sat_drops_body_damage_walls() -> None:
    intents = [{"beat": 4.0, "duration": 0.0, "kind": "note", "intensity": 0.5, "section": "intro"}]
    wall = {"b": 4.0, "d": 1.0, "x": 0, "y": 0, "w": 4, "h": 5}
    result = _solve_joint_model(
        intents,
        CONFIGS["Easy"],
        120.0,
        bomb_candidates=[],
        wall_candidates=[wall],
        difficulty="Easy",
    )
    assert result is not None
    _hands, _poses, _label, bombs, walls = result
    assert bombs == []
    assert walls == []


def test_path_crossing_findings_include_required_fields(tmp_path: Path) -> None:
    notes = [
        {"b": 4.0, "x": 1, "y": 1, "c": 0, "d": 6, "a": 0},
        {"b": 4.0, "x": 2, "y": 1, "c": 1, "d": 4, "a": 0},
    ]
    payload = {
        "version": "3.3.0",
        "bpmEvents": [],
        "rotationEvents": [],
        "colorNotes": notes,
        "bombNotes": [],
        "obstacles": [],
        "sliders": [],
        "burstSliders": [],
        "waypoints": [],
        "basicBeatmapEvents": [],
        "colorBoostBeatmapEvents": [],
        "lightColorEventBoxGroups": [],
        "lightRotationEventBoxGroups": [],
        "lightTranslationEventBoxGroups": [],
        "vfxEventBoxGroups": [],
        "basicEventTypesWithKeywords": {},
        "useNormalEventsAsCompatibleEvents": False,
    }
    (tmp_path / "ExpertPlus.dat").write_text(json.dumps(payload), encoding="utf-8")
    info = info_dat("Path Cross", "Tests", "Beatforge", 120.0)
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [
        {
            "_difficulty": "ExpertPlus",
            "_difficultyRank": 9,
            "_beatmapFilename": "ExpertPlus.dat",
            "_noteJumpMovementSpeed": 16.0,
            "_noteJumpStartBeatOffset": 0.0,
        }
    ]
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"placeholder")
    write_default_cover(tmp_path / "cover.png", "Path", 16)
    report = validate_package(tmp_path)
    collisions = [item for item in report.errors if item.code == "CROSS_HAND_PATH_COLLISION"]
    assert collisions
    details = collisions[0].details
    for key in MOTION_FINDING_FIELDS:
        assert key in details
    for key in (
        "redPosition",
        "bluePosition",
        "redDirection",
        "blueDirection",
    ):
        assert key in details
    assert details["failedConstraint"] == "saber_path_intersection"
    assert details["difficulty"] == "ExpertPlus"
    assert collisions[0].file == "ExpertPlus.dat"


def test_add_motion_rejects_incomplete_fields() -> None:
    report = ValidationReport()
    with pytest.raises(ValueError, match="missing required motion fields"):
        report.add_motion("SAME_COLOR_SIMULTANEOUS", "incomplete", file="Expert.dat", beat=1.0, difficulty="Expert")


def test_same_color_simultaneous_finding_has_motion_fields(tmp_path: Path) -> None:
    notes = [
        {"b": 4.0, "x": 0, "y": 1, "c": 0, "d": 1, "a": 0},
        {"b": 4.0, "x": 1, "y": 1, "c": 0, "d": 1, "a": 0},
    ]
    payload = {
        "version": "3.3.0",
        "bpmEvents": [],
        "rotationEvents": [],
        "colorNotes": notes,
        "bombNotes": [],
        "obstacles": [],
        "sliders": [],
        "burstSliders": [],
        "waypoints": [],
        "basicBeatmapEvents": [],
        "colorBoostBeatmapEvents": [],
        "lightColorEventBoxGroups": [],
        "lightRotationEventBoxGroups": [],
        "lightTranslationEventBoxGroups": [],
        "vfxEventBoxGroups": [],
        "basicEventTypesWithKeywords": {},
        "useNormalEventsAsCompatibleEvents": False,
    }
    (tmp_path / "Expert.dat").write_text(json.dumps(payload), encoding="utf-8")
    info = info_dat("Same Color", "Tests", "Beatforge", 120.0)
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [
        {
            "_difficulty": "Expert",
            "_difficultyRank": 7,
            "_beatmapFilename": "Expert.dat",
            "_noteJumpMovementSpeed": 16.0,
            "_noteJumpStartBeatOffset": 0.0,
        }
    ]
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"placeholder")
    write_default_cover(tmp_path / "cover.png", "Same", 16)
    report = validate_package(tmp_path)
    dupes = [item for item in report.errors if item.code == "SAME_COLOR_SIMULTANEOUS"]
    assert dupes
    for key in MOTION_FINDING_FIELDS:
        assert key in dupes[0].details
    assert dupes[0].details["failedConstraint"] == "simultaneous_same_color"
    assert dupes[0].file == "Expert.dat"


def test_bomb_on_follow_through_is_a_hard_failure(tmp_path: Path) -> None:
    notes = [{"b": 4.0, "x": 1, "y": 1, "c": 0, "d": 1, "a": 0}]
    payload = {
        "version": "3.3.0",
        "bpmEvents": [],
        "rotationEvents": [],
        "colorNotes": notes,
        "bombNotes": [{"b": 4.0, "x": 1, "y": 0}],
        "obstacles": [],
        "sliders": [],
        "burstSliders": [],
        "waypoints": [],
        "basicBeatmapEvents": [],
        "colorBoostBeatmapEvents": [],
        "lightColorEventBoxGroups": [],
        "lightRotationEventBoxGroups": [],
        "lightTranslationEventBoxGroups": [],
        "vfxEventBoxGroups": [],
        "basicEventTypesWithKeywords": {},
        "useNormalEventsAsCompatibleEvents": False,
    }
    (tmp_path / "ExpertPlus.dat").write_text(json.dumps(payload), encoding="utf-8")
    info = info_dat("Bomb Path", "Tests", "Beatforge", 120.0)
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [
        {
            "_difficulty": "ExpertPlus",
            "_difficultyRank": 9,
            "_beatmapFilename": "ExpertPlus.dat",
            "_noteJumpMovementSpeed": 16.0,
            "_noteJumpStartBeatOffset": 0.0,
        }
    ]
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"placeholder")
    write_default_cover(tmp_path / "cover.png", "Bomb", 16)
    report = validate_package(tmp_path)
    bombs = [item for item in report.errors if item.code == "BOMB_SWING_PATH"]
    assert bombs
    details = bombs[0].details
    for key in (
        "difficulty",
        "previousBeat",
        "currentBeat",
        "color",
        "previousPosition",
        "currentPosition",
        "previousDirection",
        "currentDirection",
        "availableRecoveryBeats",
        "requiredRecoveryBeats",
        "failedConstraint",
    ):
        assert key in details
    assert details["failedConstraint"] == "bomb_swing_path"
    assert bombs[0].file == "ExpertPlus.dat"


def test_center_vision_double_is_a_hard_failure(tmp_path: Path) -> None:
    payload = {
        "version": "3.3.0",
        "bpmEvents": [],
        "rotationEvents": [],
        "colorNotes": [
            {"b": 8.0, "x": 1, "y": 1, "c": 0, "d": 1, "a": 0},
            {"b": 8.0, "x": 2, "y": 1, "c": 1, "d": 1, "a": 0},
        ],
        "bombNotes": [],
        "obstacles": [],
        "sliders": [],
        "burstSliders": [],
        "waypoints": [],
        "basicBeatmapEvents": [],
        "colorBoostBeatmapEvents": [],
        "lightColorEventBoxGroups": [],
        "lightRotationEventBoxGroups": [],
        "lightTranslationEventBoxGroups": [],
        "vfxEventBoxGroups": [],
        "basicEventTypesWithKeywords": {},
        "useNormalEventsAsCompatibleEvents": False,
    }
    (tmp_path / "ExpertPlus.dat").write_text(json.dumps(payload), encoding="utf-8")
    info = info_dat("Vision", "Tests", "Beatforge", 120.0)
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [
        {
            "_difficulty": "ExpertPlus",
            "_difficultyRank": 9,
            "_beatmapFilename": "ExpertPlus.dat",
            "_noteJumpMovementSpeed": 16.0,
            "_noteJumpStartBeatOffset": 0.0,
        }
    ]
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"placeholder")
    write_default_cover(tmp_path / "cover.png", "Vision", 16)
    report = validate_package(tmp_path)
    blocked = [item for item in report.errors if item.code == "CENTER_VISION_DOUBLE"]
    assert blocked
    assert blocked[0].details["failedConstraint"] == "vision_block"
    assert blocked[0].file == "ExpertPlus.dat"


def test_rapid_same_hand_after_hold_findings_include_required_fields(tmp_path: Path) -> None:
    payload = _minimal_v3(
        notes=[
            {"b": 8.0, "x": 0, "y": 1, "c": 0, "d": 1, "a": 0},
            {"b": 8.75, "x": 0, "y": 2, "c": 0, "d": 1, "a": 0},
        ],
        sliders=[{"c": 0, "b": 8.0, "x": 0, "y": 1, "d": 1, "mu": 1.0, "tb": 8.5, "tx": 0, "ty": 1, "tc": 1, "tmu": 1.0, "m": 0}],
    )
    (tmp_path / "ExpertPlusStandard.dat").write_text(json.dumps(payload), encoding="utf-8")
    info = info_dat("Rapid Hold", "Tests", "Beatforge", 120.0)
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [
        {
            "_difficulty": "ExpertPlus",
            "_difficultyRank": 9,
            "_beatmapFilename": "ExpertPlusStandard.dat",
            "_noteJumpMovementSpeed": 18.0,
            "_noteJumpStartBeatOffset": 0.0,
        }
    ]
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"placeholder")
    write_default_cover(tmp_path / "cover.png", "Rapid", 16)
    report = validate_package(tmp_path)
    rapid = [item for item in report.errors if item.code == "RAPID_SAME_HAND_AFTER_HOLD"]
    assert rapid
    details = rapid[0].details
    for key in (
        "difficulty",
        "previousBeat",
        "currentBeat",
        "color",
        "previousPosition",
        "currentPosition",
        "previousDirection",
        "currentDirection",
        "availableRecoveryBeats",
        "requiredRecoveryBeats",
        "failedConstraint",
    ):
        assert key in details
    assert details["failedConstraint"] == "post_hold_recovery"
    assert rapid[0].file == "ExpertPlusStandard.dat"


def test_hazard_sat_rejects_note_occupancy_and_full_body_walls() -> None:
    notes = [
        {"b": 1.0, "x": 0, "y": 2, "c": 0, "d": 1},
        {"b": 1.0, "x": 3, "y": 2, "c": 1, "d": 1},
    ]
    intents = [{"beat": 0.0, "section": "intro"}, {"beat": 8.0, "section": "body"}]
    bombs, walls = _solve_hazards(notes, intents, "ExpertPlus", CONFIGS["ExpertPlus"])
    for bomb in bombs:
        assert not (int(bomb["x"]) == 0 and int(bomb["y"]) == 2 and abs(float(bomb["b"]) - 1.0) <= 0.75)
        assert not (int(bomb["x"]) == 3 and int(bomb["y"]) == 2 and abs(float(bomb["b"]) - 1.0) <= 0.75)
    for wall in walls:
        assert not (int(wall["x"]) <= 0 and int(wall["w"]) >= 4 and int(wall["y"]) <= 0 and int(wall["h"]) >= 5)


def test_confirmed_anchors_verify_timing() -> None:
    audio = synthetic_audio()
    anchors = [Anchor(0.0, 0, "downbeat"), Anchor(24.0, 529_200, "downbeat"), Anchor(47.0, 1_036_350, "beat")]
    analysis = analyze_audio_buffer(audio, anchors)
    assert analysis["status"] == "timing_verified"
    assert analysis["gridSource"] == "confirmed"
    assert math.isclose(analysis["durationSeconds"], 24.0)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"_version": "2.2.0", "_notes": [{"_time": 1, "_lineIndex": 0, "_lineLayer": 1, "_type": 0, "_cutDirection": 1}], "_obstacles": [], "_events": []}, {"note": 1}),
        ({"version": "3.3.0", "colorNotes": [{"b": 1, "x": 0, "y": 1, "c": 0, "d": 1}], "bombNotes": [], "obstacles": [], "sliders": [], "burstSliders": []}, {"note": 1}),
        ({"version": "4.1.0", "colorNotes": [{"b": 1, "i": 0}], "colorNotesData": [{"x": 0, "y": 1, "c": 0, "d": 1}], "bombNotes": [], "bombNotesData": [], "obstacles": [], "obstaclesData": [], "arcs": [], "arcsData": [], "chains": [], "chainsData": []}, {"note": 1}),
    ],
)
def test_v2_v3_v4_normalization(payload: dict, expected: dict[str, int]) -> None:
    events, _lights = normalize_beatmap(payload)
    for kind, count in expected.items():
        assert sum(event["kind"] == kind for event in events) == count


def test_gzip_json_fixture() -> None:
    payload = {"version": "4.1.0", "colorNotes": []}
    assert load_json_bytes(gzip.compress(json.dumps(payload).encode())) == payload


def test_candidate_notes_include_lane_and_row_extremes() -> None:
    for color in (0, 1):
        notes = _candidate_notes(color, 0, 0, 0.35, 8.0)
        assert {note["x"] for note in notes} == {0, 1, 2, 3}
        assert {note["y"] for note in notes} == {0, 1, 2}
        assert 8 not in {note["d"] for note in notes}
        rare_dots = _candidate_notes(color, 0, 15, 0.35, 8.0)
        assert any(note["d"] == 8 for note in rare_dots)
        intent = {"beat": 8.0, "kind": "note", "intensity": 0.35, "duration": 0.0}
        domain = _pose_domain(intent, color, 0, 0, 120.0, 0.22)[:16]
        assert {pose["head"]["x"] for pose in domain} == {0, 1, 2, 3}
        assert {pose["head"]["y"] for pose in domain} == {0, 1, 2}


def test_candidate_notes_stay_in_the_current_parity_family() -> None:
    down = {1, 6, 7}
    up = {0, 4, 5}
    for color in (0, 1):
        downs = {note["d"] for note in _candidate_notes(color, 0, 2, 0.6, 4.0)}
        ups = {note["d"] for note in _candidate_notes(color, 1, 3, 0.6, 4.5)}
        assert downs <= down
        assert ups <= up


def test_phrase_rotation_changes_the_lead_cut() -> None:
    first = cut_for(0, 0, 0, 0.0)
    later = cut_for(0, 0, 0, 4.0)
    assert first in {1, 6, 7}
    assert later in {1, 6, 7}
    assert first != later


def test_every_fourth_bar_leads_with_a_side_cut() -> None:
    assert cut_for(0, 0, 0, 12.0) in {2, 3, 5, 7}
    assert cut_for(1, 0, 0, 12.0) in {2, 3, 4, 6}
    notes = _candidate_notes(0, 0, 0, 0.6, 12.0)
    assert {note["d"] for note in notes} <= {2, 3, 4, 5, 6, 7, 8}


def test_select_intents_stacks_two_color_combos_on_downbeats() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 4.0, "sustainBeats": 0.0}
        for beat in range(0, 16)
    ]
    analysis = {"bpm": 120.0, "events": events}
    sections = {"sections": [{"startBeat": 0, "endBeat": 32, "label": "body", "intensity": 0.6}]}
    hard = select_intents(analysis, sections, "Hard")
    expert = select_intents(analysis, sections, "Expert")
    assert not any(item.get("layer") == "combo-stack" for item in hard)
    stacks = [item for item in expert if item.get("layer") == "combo-stack"]
    assert stacks
    assert all(abs(float(item["beat"]) % 4.0) < 1e-9 for item in stacks)


def test_expert_plus_stacks_combos_on_peak_beats() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 4.0, "sustainBeats": 0.0}
        for beat in range(0, 32)
    ]
    analysis = {"bpm": 120.0, "events": events}
    sections = {
        "sections": [
            {"startBeat": 0, "endBeat": 16, "label": "verse", "intensity": 0.5},
            {"startBeat": 16, "endBeat": 32, "label": "drop", "intensity": 0.9},
        ]
    }
    plus = select_intents(analysis, sections, "ExpertPlus")
    stacks = [item for item in plus if item.get("layer") == "combo-stack"]
    peak = [item for item in stacks if float(item["beat"]) >= 16.0]
    assert peak
    assert any(abs(float(item["beat"]) % 1.0) < 1e-9 and abs(float(item["beat"]) % 2.0) > 1e-6 for item in peak)


def test_hard_uses_quarter_spacing_not_eighth_streams() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 4.0, "sustainBeats": 0.0}
        for beat in np.arange(0.0, 32.0, 0.25)
    ]
    analysis = {"bpm": 94.0, "events": events}
    sections = {"sections": [{"startBeat": 0, "endBeat": 40, "label": "body", "intensity": 0.7}]}
    hard = select_intents(analysis, sections, "Hard")
    expert = select_intents(analysis, sections, "Expert")
    expert_plus = select_intents(analysis, sections, "ExpertPlus")
    hard_beats = sorted({round(float(item["beat"]), 6) for item in hard})
    hard_gaps = [right - left for left, right in zip(hard_beats, hard_beats[1:])]
    assert min(hard_gaps or [1.0]) >= 1.0 - 1e-9
    assert len(hard) < len(expert) <= len(expert_plus)


def test_fold_double_time_when_clocks_disagree() -> None:
    beats = [index * 0.32 for index in range(32)]
    track = {"name": "beat-this", "bpm": 187.5, "beatsSeconds": beats, "beatsSeconds": beats}
    folded = analyze_audio.fold_double_time_track(
        track,
        consensus={"bpmMedian": 138.0, "bpmSpreadPercent": 68.0, "bpmMedian": 138.0, "bpmSpreadPercent": 68.0},
        dual_clock_passes=False,
    )
    assert folded["foldedDoubleTime"] is True
    assert abs(folded["bpm"] - 93.75) < 1e-6
    assert len(folded["beatsSeconds"]) == 16
    kept = analyze_audio.fold_double_time_track(
        {"name": "beat-this", "bpm": 174.0, "beatsSeconds": beats},
        consensus={"bpmMedian": 174.0, "bpmSpreadPercent": 4.0},
        dual_clock_passes=True,
    )
    assert kept.get("foldedDoubleTime") is not True
    assert kept["bpm"] == 174.0


def test_cover_lookup_accepts_unapproved_front_art() -> None:
    chosen = pick_cover_art_image(
        [
            {"front": False, "approved": True, "image": "back.jpg"},
            {"front": True, "approved": False, "image": "front.jpg"},
        ]
    )
    assert chosen is not None
    assert chosen["image"] == "front.jpg"


def test_unknown_artist_cover_match_uses_title() -> None:
    score = metadata_match_confidence("Human Nature", "Unknown Artist", "Human Nature", "Michael Jackson")
    assert score > 0.95


def test_half_beat_hits_alternate_hands() -> None:
    intents = [{"beat": index * 0.5, "duration": 0.0, "kind": "note", "intensity": 0.55} for index in range(12)]
    hands, _solver = solve_hands(intents, CONFIGS["ExpertPlus"])
    assert all(hands[index] != hands[index - 1] for index in range(1, len(hands)))


def test_arc_occupancy_assigns_idle_hand() -> None:
    intents = [
        {"beat": 1.0, "duration": 2.0, "kind": "arc"},
        {"beat": 1.5, "duration": 0.0, "kind": "note"},
        {"beat": 2.0, "duration": 0.0, "kind": "note"},
        {"beat": 3.25, "duration": 0.0, "kind": "note"},
    ]
    hands, _solver = solve_hands(intents, CONFIGS["ExpertPlus"])
    assert hands[1] != hands[0]
    assert hands[2] != hands[0]
    assert hands[3] != hands[0]


def test_joint_sat_assigns_color_lane_row_cut_and_occupancy() -> None:
    intents = [
        {"beat": 1.0, "duration": 2.0, "kind": "arc", "intensity": 0.6},
        {"beat": 2.0, "duration": 0.0, "kind": "note", "intensity": 0.5},
        {"beat": 3.0, "duration": 0.0, "kind": "note", "intensity": 0.5},
        {"beat": 4.25, "duration": 0.0, "kind": "note", "intensity": 0.4},
    ]
    result = _solve_joint_assignment(intents, CONFIGS["ExpertPlus"], 120.0)
    assert result is not None
    hands, poses, label = result[:3]
    assert label == "joint-cp-sat"
    assert poses[0] is not None
    assert poses[0]["kind"] == "arc"
    placed = [(hand, pose) for hand, pose in zip(hands, poses) if pose is not None]
    assert {int(pose["head"]["c"]) for _hand, pose in placed} == {hand for hand, _pose in placed}
    assert all(hands[index] != hands[0] for index, pose in enumerate(poses[1:], start=1) if pose is not None)
    assert all(0 <= int(pose["head"]["x"]) <= 3 for pose in poses if pose is not None)
    assert all(0 <= int(pose["head"]["y"]) <= 2 for pose in poses if pose is not None)
    assert all(0 <= int(pose["head"]["d"]) <= 8 for pose in poses if pose is not None)


def test_joint_sat_keeps_a_chain_when_notes_surround_the_tail() -> None:
    intents = [
        {"beat": 1.0, "duration": 0.25, "kind": "chain", "intensity": 0.8},
        {"beat": 1.125, "duration": 0.0, "kind": "note", "intensity": 0.7},
        {"beat": 1.5, "duration": 0.0, "kind": "note", "intensity": 0.7},
        {"beat": 2.0, "duration": 0.0, "kind": "note", "intensity": 0.5},
    ]
    result = _solve_joint_assignment(intents, CONFIGS["ExpertPlus"], 120.0)
    assert result is not None
    poses = result[1]
    assert poses[0] is not None
    assert poses[0]["kind"] == "chain"


def test_joint_sat_window_overlap_keeps_flow_legal() -> None:
    intents = [
        {"beat": float(index) * 0.5, "duration": 0.0, "kind": "note", "intensity": 0.7}
        for index in range(72)
    ]
    result = _solve_joint_assignment(intents, CONFIGS["ExpertPlus"], 120.0)
    assert result is not None
    hands, poses, label = result[:3]
    assert label == "joint-cp-sat"
    assert _joint_flow_is_legal(hands, poses, 120.0, CONFIGS["ExpertPlus"].recovery_beats)


def test_joint_sat_windows_keep_chains_when_overlap_cannot_stay_frozen() -> None:
    intents: list[dict[str, object]] = []
    beat = 0.0
    for index in range(80):
        hold = index % 9 == 0
        intents.append(
            {
                "beat": beat,
                "duration": 0.25 if hold else 0.0,
                "kind": "chain" if hold else "note",
                "intensity": 0.85 if hold else 0.65,
            }
        )
        beat += 0.25
    result = _solve_joint_assignment(intents, CONFIGS["Expert"], 120.0, difficulty="Expert")
    assert result is not None
    poses = result[1]
    assert result[2] == "joint-cp-sat"
    assert any(pose is not None and pose.get("kind") == "chain" for pose in poses)
    assert _joint_flow_is_legal(result[0], poses, 120.0, CONFIGS["Expert"].recovery_beats)


def test_short_chain_occupancy_matches_minimum_tail_length() -> None:
    head = 8.0
    short = 0.125
    intents = [
        {"beat": head, "duration": short, "kind": "chain", "intensity": 0.9},
        {"beat": head + 0.25, "duration": 0.0, "kind": "note", "intensity": 0.7},
        {"beat": head + 0.5, "duration": 0.0, "kind": "note", "intensity": 0.7},
        {"beat": head + 1.0, "duration": 0.0, "kind": "note", "intensity": 0.6},
    ]
    result = _solve_joint_assignment(intents, CONFIGS["ExpertPlus"], 120.0, difficulty="ExpertPlus")
    assert result is not None
    hands, poses, label = result[:3]
    assert label == "joint-cp-sat"
    assert poses[0] is not None and poses[0]["kind"] == "chain"
    assert abs(float(poses[0]["tail"]["b"]) - (head + 0.25)) < 1e-6
    if poses[2] is not None:
        assert int(poses[2]["head"]["c"]) != int(poses[0]["head"]["c"])
    beatmap, _trace = realize(
        intents,
        hands,
        "ExpertPlus",
        1,
        120.0,
        poses=poses,
        pose_solver=label,
        joint_bombs=[],
        joint_walls=[],
    )
    chains = beatmap["burstSliders"]
    assert chains
    color = int(chains[0]["c"])
    after = [note for note in beatmap["colorNotes"] if abs(float(note["b"]) - (head + 0.5)) < 1e-6 and int(note["c"]) == color]
    assert not after


def test_lighting_follows_musical_accents_not_every_note() -> None:
    intents = [
        {"beat": 0.0, "kind": "note", "intensity": 0.4, "section": "intro"},
        {"beat": 0.5, "kind": "note", "intensity": 0.4, "section": "intro"},
        {"beat": 1.0, "kind": "note", "intensity": 0.4, "section": "intro"},
        {"beat": 2.0, "kind": "note", "intensity": 0.4, "section": "intro"},
        {"beat": 4.0, "kind": "note", "intensity": 0.4, "section": "intro"},
        {"beat": 8.0, "kind": "chain", "intensity": 0.6, "section": "drop"},
        {"beat": 8.25, "kind": "note", "intensity": 0.9, "section": "drop"},
        {"beat": 12.0, "kind": "arc", "intensity": 0.5, "section": "drop"},
        {"beat": 12.5, "kind": "note", "intensity": 0.4, "section": "drop"},
    ]
    lighting = build_lighting(intents)
    events = lighting["basicBeatmapEvents"]
    boosts = lighting["colorBoostBeatmapEvents"]
    event_beats = [item["b"] for item in events]
    assert 0.0 in event_beats
    assert 4.0 in event_beats
    assert 8.0 in event_beats
    assert 12.0 in event_beats
    assert 0.5 not in event_beats
    assert 1.0 not in event_beats
    assert 2.0 not in event_beats
    assert boosts and boosts[0]["b"] == 8.0
    assert all(item["et"] in {0, 1, 2, 3, 4} for item in events)
    drop_events = [item for item in events if float(item["b"]) >= 8.0]
    intro_events = [item for item in events if float(item["b"]) < 8.0]
    assert len(drop_events) > len(intro_events)


def test_peak_lighting_matches_official_electronic_density_scale() -> None:
    intents = [
        {"beat": float(beat), "kind": "note", "intensity": 0.9, "section": "drop", "strength": 4.0, "duration": 0.0}
        for beat in range(0, 64)
    ]
    lighting = build_lighting(intents)
    notes = len(intents)
    objects = lighting_object_count(lighting)
    assert objects / notes >= 2.5
    seconds = 64.0 * 60.0 / 120.0
    assert objects / seconds >= 18.0
    assert lighting["lightColorEventBoxGroups"]
    assert lighting["lightRotationEventBoxGroups"]
    assert lighting["lightTranslationEventBoxGroups"]


def test_lighting_covers_the_song_after_the_last_note() -> None:
    intents = [{"beat": 0.0, "kind": "note", "intensity": 0.4, "section": "intro", "strength": 2.0, "duration": 0.0}]
    lighting = build_lighting(intents, end_beat=32.0)
    beats = [float(item["b"]) for item in lighting["basicBeatmapEvents"]]
    assert max(beats) >= 24.0


def test_verse_lighting_ticks_side_lasers_without_flooding_the_intro() -> None:
    intents = [
        {"beat": 0.0, "kind": "note", "intensity": 0.3, "section": "intro", "strength": 2.0, "duration": 0.0},
        {"beat": 8.0, "kind": "note", "intensity": 0.55, "section": "verse", "strength": 3.0, "duration": 0.0},
    ]
    lighting = build_lighting(intents, end_beat=16.0)
    events = lighting["basicBeatmapEvents"]
    intro = [item for item in events if float(item["b"]) < 8.0]
    verse = [item for item in events if 8.0 <= float(item["b"]) < 16.0]
    assert all(abs(float(item["b"]) % 4.0) <= 1e-6 for item in intro)
    assert any(abs(float(item["b"]) % 0.5) <= 1e-6 and abs(float(item["b"]) % 2.0) > 1e-6 for item in verse)
    assert any(int(item["et"]) in {2, 3} for item in verse)


def test_lighting_reacts_to_unselected_onsets_and_stems() -> None:
    intents = [
        {"beat": 8.0, "kind": "note", "intensity": 0.55, "section": "verse", "strength": 3.0, "duration": 0.0, "layer": "bass"},
    ]
    accents = [
        {"beat": 0.5, "strength": 1.2, "layer": "vocals"},
        {"beat": 8.25, "strength": 4.2, "layer": "drums", "stemEnergy": {"drums": 0.8}},
        {"beat": 9.0, "strength": 3.0, "layer": "bass", "sustainBeats": 1.0},
    ]
    lighting = build_lighting(intents, end_beat=12.0, accents=accents)
    events = lighting["basicBeatmapEvents"]
    beats = {round(float(item["b"]), 6) for item in events}
    assert 0.5 not in beats
    assert 8.25 in beats
    drum_hits = [item for item in events if abs(float(item["b"]) - 8.25) < 1e-6]
    assert any(int(item["et"]) in {2, 3, 4} for item in drum_hits)
    assert any(abs(float(item["b"]) - 10.0) < 1e-6 for item in events)


def test_combo_stack_domain_leads_with_a_window_then_a_tower() -> None:
    intent = {"beat": 8.0, "kind": "note", "intensity": 0.8, "duration": 0.0, "layer": "combo-stack"}
    red = _pose_domain(intent, 0, 0, 0, 120.0, 0.3)
    blue = _pose_domain(intent, 1, 0, 1, 120.0, 0.3)
    assert (int(red[0]["head"]["x"]), int(red[0]["head"]["y"])) == (1, 1)
    assert (int(blue[0]["head"]["x"]), int(blue[0]["head"]["y"])) == (2, 1)
    assert int(red[0]["head"]["y"]) == int(blue[0]["head"]["y"])
    assert (int(red[2]["head"]["x"]), int(red[2]["head"]["y"])) == (1, 0)


def test_alternating_combo_prefers_the_same_cut_direction() -> None:
    intents = [
        {"beat": 0.0, "duration": 0.0, "kind": "note", "intensity": 0.85, "section": "drop"},
        {"beat": 0.5, "duration": 0.0, "kind": "note", "intensity": 0.85, "section": "drop"},
        {"beat": 1.0, "duration": 0.0, "kind": "note", "intensity": 0.85, "section": "drop"},
        {"beat": 1.5, "duration": 0.0, "kind": "note", "intensity": 0.85, "section": "drop"},
        {"beat": 4.0, "duration": 0.0, "kind": "note", "intensity": 0.85, "section": "drop"},
    ]
    result = _solve_joint_assignment(intents, CONFIGS["Expert"], 128.0, difficulty="Expert")
    assert result is not None
    hands, poses, _label, _bombs, _walls = result
    assert hands[0] != hands[1]
    cuts = [int(pose["head"]["d"]) for pose in poses[:4] if pose is not None]
    assert len(set(cuts)) >= 2
    assert not all(cut == 1 for cut in cuts)


def test_joint_sat_windows_sequences_longer_than_one_model() -> None:
    intents = [{"beat": float(index), "duration": 0.0, "kind": "note", "intensity": 0.45} for index in range(80)]
    result = _solve_joint_assignment(intents, CONFIGS["Hard"], 120.0)
    if result is None:
        return
    hands, poses, label = result[:3]
    assert label == "joint-cp-sat"
    assert len(hands) == 80
    placed = [pose for pose in poses if pose is not None]
    assert placed
    assert all(int(pose["head"]["c"]) in {0, 1} for pose in placed)


def test_hold_domain_includes_note_downgrade_poses() -> None:
    intent = {"beat": 1.0, "duration": 2.0, "kind": "arc", "intensity": 0.6}
    domain = _pose_domain(intent, 0, 0, 0, 120.0, 0.22)
    assert {pose["kind"] for pose in domain} >= {"arc", "note"}
    notes = [pose for pose in domain if pose["kind"] == "note"]
    arcs = [pose for pose in domain if pose["kind"] == "arc"]
    assert notes and arcs
    assert all(pose["tail"] is None for pose in notes)
    assert all(pose["tail"] is not None for pose in arcs)


def test_realize_uses_sat_hold_kind_not_the_intent_label() -> None:
    intents = [{"beat": 1.0, "duration": 2.0, "kind": "arc", "intensity": 0.6, "section": "body"}]
    head = {"b": 1.0, "x": 0, "y": 1, "c": 0, "d": 1, "a": 0}
    beatmap, trace = realize(
        intents,
        [0],
        "ExpertPlus",
        1,
        120.0,
        poses=[{"head": head, "tail": None, "exit": head, "kind": "note"}],
        pose_solver="joint-cp-sat",
        joint_bombs=[],
        joint_walls=[],
    )
    assert beatmap["sliders"] == []
    assert beatmap["burstSliders"] == []
    assert any(item.get("reason") == "hold_downgraded_to_note" for item in trace["densityRelaxations"])


def test_full_spread_is_independently_realized_and_valid(tmp_path: Path) -> None:
    events = []
    for index, beat in enumerate(np.arange(1.0, 64.0, 0.25)):
        events.append({"beat": float(beat), "snappedBeat": float(beat), "strength": 1.0 + index % 7, "sustainBeats": 2.0 if index % 41 == 0 else 0.0})
    analysis = {"bpm": 120.0, "events": events}
    sections = {"sections": [{"startBeat": 0, "endBeat": 32, "label": "body", "intensity": 0.5}, {"startBeat": 32, "endBeat": 80, "label": "peak", "intensity": 0.9}]}
    maps, report = generate_all(analysis, sections, 1234, tmp_path / "missing.sqlite3")
    assert set(maps) == {"Easy", "Normal", "Hard", "Expert", "ExpertPlus"}
    selected_counts = [len(select_intents(analysis, sections, difficulty)) for difficulty in maps]
    assert selected_counts == sorted(selected_counts)
    for difficulty, payload in maps.items():
        (tmp_path / f"{difficulty}Standard.dat").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "Info.dat").write_text(json.dumps(info_dat("Synthetic", "Tests", "Beatforge", 120.0)), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"test-placeholder")
    write_default_cover(tmp_path / "cover.png", "Synthetic", 32)
    analysis_dir = tmp_path / "_beatforge"
    analysis_dir.mkdir()
    (analysis_dir / "analysis.json").write_text(json.dumps({"status": "timing_verified"}), encoding="utf-8")
    validation = validate_package(tmp_path)
    assert validation.errors == []
    assert validation.status == "playtest_candidate"
    assert all(item["counts"]["notes"] > 0 for item in report["difficulties"].values())
    assert all(isinstance(item, dict) and "hands" in item and "poses" in item for item in report["solver"].values())
    pose_labels = [str(item.get("poses", "")) for item in report["solver"].values()]
    assert any(label.startswith("cp-sat") or label.startswith("joint-cp-sat") for label in pose_labels)
    assert report["solver"]["Easy"]["hands"] == "joint-cp-sat"
    assert report["solver"]["Normal"]["hands"] == "joint-cp-sat"


def test_demucs_is_optional_and_does_not_change_consensus_thresholds() -> None:
    import importlib.util

    source = Path(__file__).resolve().parents[1] / "scripts" / "analyze_audio.py"
    text = source.read_text(encoding="utf-8")
    assert "def external_agreement_passes" in text
    assert 'float(agreement["medianMs"]) <= 10.0' in text
    assert 'float(agreement["p95Ms"]) <= 20.0' in text
    assert 'float(agreement["maximumCumulativeDriftMs"]) <= 20.0' in text
    assert "demucs.separate" not in text
    assert "write_pcm16_wav" in text
    spec = importlib.util.find_spec("demucs")
    if spec is None:
        assert "Demucs stem separation was required but is not installed" in text
    else:
        assert spec.name == "demucs"


def test_stem_wav_writer_does_not_use_torchaudio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import analyze_audio

    def boom(*_args, **_kwargs):
        raise AssertionError("torchaudio.save must not be used for Demucs stems")

    try:
        import torchaudio

        monkeypatch.setattr(torchaudio, "save", boom, raising=False)
    except ImportError:
        pass
    samples = np.zeros((2, 4410), dtype=np.float32)
    samples[0, 100] = 0.5
    path = tmp_path / "drums.wav"
    analyze_audio.write_pcm16_wav(path, samples, 44_100)
    audio, rate = analyze_audio.read_canonical_wav(path)
    assert rate == 44_100
    assert audio.shape[0] == 2
    assert audio.shape[1] == 4410
    assert float(np.max(np.abs(audio[0]))) > 0.4


def test_separate_stems_writes_six_instrument_wavs_without_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("demucs")
    import torch

    import analyze_audio

    requested: list[str] = []

    class FakeModel:
        audio_channels = 2
        samplerate = 44_100
        sources = ["drums", "bass", "other", "vocals", "guitar", "piano"]

        def cpu(self):
            return self

        def eval(self):
            return self

    def fake_get_model(name: str):
        requested.append(name)
        return FakeModel()

    def fake_apply(model, mix, **_kwargs):
        return torch.zeros(mix.shape[0], len(model.sources), mix.shape[1], mix.shape[2])

    def boom(*_args, **_kwargs):
        raise AssertionError("torchaudio.save must not be used for Demucs stems")

    monkeypatch.setattr("demucs.pretrained.get_model", fake_get_model)
    monkeypatch.setattr("demucs.apply.apply_model", fake_apply)
    try:
        import torchaudio

        monkeypatch.setattr(torchaudio, "save", boom, raising=False)
    except ImportError:
        pass
    wav_path = tmp_path / "canonical.wav"
    analyze_audio.write_pcm16_wav(wav_path, np.zeros((2, 2205), dtype=np.float32), 44_100)
    stems = analyze_audio.separate_stems(wav_path, tmp_path / "stems")
    assert requested[0] == "htdemucs_6s"
    assert stems.model == "htdemucs_6s"
    assert set(stems.stems) == {"drums", "bass", "other", "vocals", "guitar", "piano"}
    assert all(path.is_file() and path.stat().st_size > 44 for path in stems.stems.values())


def test_annotate_events_with_stem_energy_picks_loudest_layer(tmp_path: Path) -> None:
    import analyze_audio
    from demucs_stems import annotate_events_with_stem_energy

    drums = np.zeros((2, 4410), dtype=np.float32)
    vocals = np.zeros((2, 4410), dtype=np.float32)
    drums[:, 2205:2260] = 0.8
    vocals[:, 100:160] = 0.8
    drums_path = tmp_path / "drums.wav"
    vocals_path = tmp_path / "vocals.wav"
    analyze_audio.write_pcm16_wav(drums_path, drums, 44_100)
    analyze_audio.write_pcm16_wav(vocals_path, vocals, 44_100)
    analysis = {
        "sampleRate": 44_100,
        "events": [
            {"sample": 120, "beat": 0.0, "strength": 0.4},
            {"sample": 2230, "beat": 1.0, "strength": 0.4},
        ],
    }
    annotate_events_with_stem_energy(analysis, {"drums": drums_path, "vocals": vocals_path})
    assert analysis["events"][0]["layer"] == "vocals"
    assert analysis["events"][1]["layer"] == "drums"


def test_select_intents_keeps_expert_instrument_layers_below_quantile() -> None:
    events = []
    for index in range(40):
        beat = 1.25 + index * 0.5
        if index % 5 == 0:
            events.append(
                {
                    "beat": beat,
                    "snappedBeat": beat,
                    "strength": 0.05,
                    "sustainBeats": 0.0,
                    "layer": "drums",
                    "stemEnergy": {"drums": 0.9, "vocals": 0.1, "bass": 0.2, "guitar": 0.0},
                }
            )
        else:
            events.append(
                {
                    "beat": beat,
                    "snappedBeat": beat,
                    "strength": 1.0,
                    "sustainBeats": 0.0,
                    "layer": "vocals",
                    "stemEnergy": {"drums": 0.05, "vocals": 0.8, "bass": 0.0, "guitar": 0.0},
                }
            )
    analysis = {"bpm": 120.0, "events": events}
    sections = {"sections": [{"startBeat": 0, "endBeat": 80, "label": "body", "intensity": 0.5}]}
    easy = select_intents(analysis, sections, "Easy")
    expert = select_intents(analysis, sections, "ExpertPlus")
    assert not any(item.get("layer") == "drums" for item in easy)
    assert any(item.get("layer") == "drums" for item in expert)


def test_select_intents_puts_blocks_in_the_opening_window() -> None:
    analysis = {
        "bpm": 120.0,
        "events": [
            {"beat": 32.0, "snappedBeat": 32.0, "strength": 5.0, "sustainBeats": 0.0, "layer": "drums"},
            {"beat": 33.0, "snappedBeat": 33.0, "strength": 4.8, "sustainBeats": 0.0, "layer": "drums"},
        ],
    }
    sections = {"sections": [{"startBeat": 0, "endBeat": 80, "label": "intro", "intensity": 0.4}]}
    intents = select_intents(analysis, sections, "ExpertPlus")
    opening = [item for item in intents if float(item["beat"]) * 60.0 / 120.0 <= 10.0]
    assert opening
    first_seconds = min(float(item["beat"]) for item in opening) * 60.0 / 120.0
    assert first_seconds >= 1.0 - 1e-9
    assert first_seconds <= 2.0
    directions = [_candidate_notes(0, index % 2, index, 0.6, float(item["beat"]))[0]["d"] for index, item in enumerate(opening[:8])]
    assert len(set(directions)) >= 2


def test_first_note_is_at_least_one_second_in() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 5.0, "sustainBeats": 0.0}
        for beat in range(0, 16)
    ]
    analysis = {"bpm": 180.0, "events": events}
    sections = {"sections": [{"startBeat": 0, "endBeat": 32, "label": "intro", "intensity": 0.5}]}
    for difficulty in ("Easy", "Hard", "ExpertPlus"):
        intents = select_intents(analysis, sections, difficulty)
        assert intents
        first_seconds = min(float(item["beat"]) for item in intents) * 60.0 / 180.0
        assert first_seconds >= 1.0 - 1e-9, difficulty
        assert not any(abs(float(item["beat"])) < 1e-9 for item in intents)


def test_expert_keeps_hard_spacing_instead_of_expert_plus_sixteenths() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 4.0, "sustainBeats": 0.0}
        for beat in np.arange(0.0, 32.0, 0.125)
    ]
    analysis = {"bpm": 120.0, "events": events}
    sections = {"sections": [{"startBeat": 0, "endBeat": 40, "label": "peak", "intensity": 0.95}]}

    def sequential_gaps(intents: list[dict]) -> list[float]:
        beats = sorted(float(item["beat"]) for item in intents)
        return [right - left for left, right in zip(beats, beats[1:]) if right - left > 1e-9]

    hard = select_intents(analysis, sections, "Hard")
    expert = select_intents(analysis, sections, "Expert")
    expert_plus = select_intents(analysis, sections, "ExpertPlus")
    assert min(sequential_gaps(hard) or [1.0]) >= 1.0 - 1e-9
    assert min(sequential_gaps(expert) or [1.0]) >= 0.5 - 1e-9
    assert min(sequential_gaps(expert_plus) or [1.0]) >= 0.25 - 1e-9
    assert len(hard) < len(expert) <= len(expert_plus)
    assert not any(item.get("kind") == "chain" for item in expert)


def test_expert_plus_reserves_sixteenths_for_peaks() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 4.0, "sustainBeats": 0.0}
        for beat in np.arange(0.0, 64.0, 0.25)
    ]
    analysis = {"bpm": 120.0, "events": events}
    sections = {
        "sections": [
            {"startBeat": 0, "endBeat": 32, "label": "verse", "intensity": 0.45},
            {"startBeat": 32, "endBeat": 64, "label": "drop", "intensity": 0.9},
        ]
    }

    def sequential_gaps(intents: list[dict], start: float, end: float) -> list[float]:
        beats = sorted(float(item["beat"]) for item in intents if start <= float(item["beat"]) < end)
        return [right - left for left, right in zip(beats, beats[1:]) if right - left > 1e-9]

    expert = select_intents(analysis, sections, "Expert")
    expert_plus = select_intents(analysis, sections, "ExpertPlus")
    verse_plus = sequential_gaps(expert_plus, 0.0, 32.0)
    drop_plus = sequential_gaps(expert_plus, 32.0, 64.0)
    assert min(verse_plus or [1.0]) >= 0.5 - 1e-9
    assert min(drop_plus or [1.0]) >= 0.25 - 1e-9
    assert min(drop_plus) < 0.5 - 1e-9
    verse_count = sum(1 for item in expert_plus if float(item["beat"]) < 32.0)
    drop_count = sum(1 for item in expert_plus if float(item["beat"]) >= 32.0)
    assert drop_count > verse_count
    assert len(expert) < len(expert_plus)


def test_spread_follows_official_note_count_direction() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 3.5, "sustainBeats": 0.0}
        for beat in np.arange(0.0, 80.0, 0.25)
    ]
    analysis = {"bpm": 128.0, "events": events}
    sections = {
        "sections": [
            {"startBeat": 0, "endBeat": 32, "label": "intro", "intensity": 0.35},
            {"startBeat": 32, "endBeat": 56, "label": "verse", "intensity": 0.5},
            {"startBeat": 56, "endBeat": 80, "label": "chorus", "intensity": 0.85},
        ]
    }
    counts = {
        name: len(select_intents(analysis, sections, name))
        for name in ("Easy", "Normal", "Hard", "Expert", "ExpertPlus")
    }
    assert counts["Easy"] < counts["Normal"] < counts["Hard"] < counts["Expert"] <= counts["ExpertPlus"]
    hard_to_expert = counts["Expert"] / max(1, counts["Hard"])
    expert_to_plus = counts["ExpertPlus"] / max(1, counts["Expert"])
    assert 1.15 <= hard_to_expert <= 2.1
    assert 1.05 <= expert_to_plus <= 1.8


def test_realized_opening_prefers_arrow_combos_over_dots() -> None:
    analysis = {
        "bpm": 120.0,
        "events": [{"beat": float(beat), "snappedBeat": float(beat), "strength": 3.0, "sustainBeats": 0.0} for beat in range(0, 40)],
    }
    sections = {"sections": [{"startBeat": 0, "endBeat": 80, "label": "body", "intensity": 0.6}]}
    intents = select_intents(analysis, sections, "ExpertPlus")
    hands, poses, solver_label, bombs, walls = _solve_joint_assignment(intents, CONFIGS["ExpertPlus"], 120.0, difficulty="ExpertPlus")
    beatmap, _trace = realize(intents, hands, "ExpertPlus", 7, 120.0, poses=poses, pose_solver=solver_label, joint_bombs=bombs, joint_walls=walls)
    notes = beatmap["colorNotes"]
    first = min(float(note["b"]) for note in notes)
    assert first >= 2.0 - 1e-9
    assert first <= 4.0
    dots = sum(1 for note in notes if int(note["d"]) == 8)
    assert dots / max(len(notes), 1) < 0.25
    cuts = [int(note["d"]) for note in notes if int(note["d"]) != 8]
    assert len(set(cuts)) >= 2
    downs = sum(1 for note in notes if int(note["d"]) == 1)
    top_downs = sum(1 for note in notes if int(note["d"]) == 1 and int(note["y"]) == 2)
    assert downs / max(len(notes), 1) < 0.55
    assert top_downs / max(len(notes), 1) < 0.25


def test_generated_maps_do_not_tile_down_from_the_top() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 4.0, "sustainBeats": 0.0}
        for beat in range(0, 48)
    ]
    analysis = {"bpm": 128.0, "events": events, "durationSeconds": 22.5}
    sections = {
        "sections": [
            {"startBeat": 0, "endBeat": 16, "label": "verse", "intensity": 0.5},
            {"startBeat": 16, "endBeat": 48, "label": "chorus", "intensity": 0.85},
        ]
    }
    maps, _report = generate_all(analysis, sections, 11, Path("missing.sqlite3"), difficulties=("Hard", "Expert"))
    for difficulty, payload in maps.items():
        notes = payload["colorNotes"]
        assert notes, difficulty
        cuts = [int(note["d"]) for note in notes if int(note["d"]) != 8]
        downs = sum(cut == 1 for cut in cuts)
        top_downs = sum(1 for note in notes if int(note["d"]) == 1 and int(note["y"]) == 2)
        assert len(set(cuts)) >= 3, (difficulty, set(cuts))
        assert downs / max(len(cuts), 1) < 0.5, (difficulty, downs, len(cuts))
        assert top_downs / max(len(notes), 1) < 0.22, (difficulty, top_downs, len(notes))
        assert any(cut in {2, 3} for cut in cuts), (difficulty, set(cuts))
        bottom = sum(1 for note in notes if int(note["y"]) == 0)
        top = sum(1 for note in notes if int(note["y"]) == 2)
        assert bottom >= top, (difficulty, bottom, top)


@pytest.mark.corpus
@pytest.mark.skipif(
    not (GAME_ROOT / "Beat Saber_Data" / "StreamingAssets" / "BeatmapLevelsData" / "magic").is_file()
    or not unitypy_is_importable(),
    reason="installed Magic fixture or pinned UnityPy importer unavailable",
)
def test_official_magic_v4_gold_fixture(tmp_path: Path) -> None:
    pack_paths = list((GAME_ROOT / "Beat Saber_Data" / "StreamingAssets" / "aa" / "StandaloneWindows64").glob("ostvol5_pack_assets_all_*.bundle"))
    database = connect(tmp_path / "corpus.sqlite3")
    catalog, _hashes = index_pack_definitions(database, pack_paths)
    status, count = insert_bundle(database, GAME_ROOT / "Beat Saber_Data" / "StreamingAssets" / "BeatmapLevelsData" / "magic", "base", "OstVol5", catalog["magic"])
    assert (status, count) == ("indexed", 8)
    expert_plus = database.execute("SELECT d.notes,d.arcs,d.chains,d.bombs,d.walls FROM difficulties d JOIN levels l ON l.id=d.level_id WHERE l.title='Magic' AND d.characteristic='Standard' AND d.difficulty='ExpertPlus'").fetchone()
    assert tuple(expert_plus) == (595, 49, 36, 11, 14)


def _stub_official_assets(path: Path, include_audio: bool = False):
    info = {
        "version": "4.0.0",
        "song": {"title": "Stub Song", "author": "Stub Artist"},
        "audio": {"bpm": 128.0},
        "difficultyBeatmaps": [
            {
                "characteristic": "Standard",
                "difficulty": "ExpertPlus",
                "beatmapDataFilename": "ExpertPlus.dat",
                "lightshowDataFilename": "Lightshow.dat",
                "noteJumpMovementSpeed": 18,
                "noteJumpStartBeatOffset": 0,
            }
        ],
    }
    beatmap = {"version": "3.3.0", "colorNotes": [{"b": 4.0, "x": 1, "y": 1, "c": 0, "d": 1, "a": 0}], "bombNotes": [], "obstacles": [], "sliders": [], "burstSliders": []}
    lightshow = {"version": "3.3.0", "basicBeatmapEvents": [{"b": 0.0, "et": 0, "i": 1, "f": 1.0}]}
    text = {
        "Info.dat": json.dumps(info).encode("utf-8"),
        "ExpertPlus.dat": json.dumps(beatmap).encode("utf-8"),
        "Lightshow.dat": json.dumps(lightshow).encode("utf-8"),
    }
    return text, [], {}, {}


def test_insert_bundle_skips_unchanged_hash_and_reprocesses_on_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("official_corpus.extract_bundle_assets", _stub_official_assets)
    bundle = tmp_path / "stub-level"
    bundle.write_bytes(b"first-payload")
    database = connect(tmp_path / "corpus.sqlite3")
    first, imported = insert_bundle(database, bundle, "base", "OstVol1")
    assert (first, imported) == ("indexed", 1)
    repeat, skipped = insert_bundle(database, bundle, "base", "OstVol1")
    assert (repeat, skipped) == ("unchanged", 0)
    before = database.execute("SELECT COUNT(*) AS n FROM difficulties").fetchone()["n"]
    bundle.write_bytes(b"changed-payload")
    again, reimported = insert_bundle(database, bundle, "base", "OstVol1")
    assert (again, reimported) == ("indexed", 1)
    after = database.execute("SELECT COUNT(*) AS n FROM difficulties").fetchone()["n"]
    assert before == after == 1


def test_corpus_report_covers_pack_song_difficulty_and_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("official_corpus.extract_bundle_assets", _stub_official_assets)
    bundle = tmp_path / "stub-level"
    bundle.write_bytes(b"report-payload")
    database_path = tmp_path / "corpus.sqlite3"
    database = connect(database_path)
    assert insert_bundle(database, bundle, "base", "OstVol1") == ("indexed", 1)
    database.commit()
    exit_code = report(argparse.Namespace(database=database_path))
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["packs"][0]["pack_id"] == "OstVol1"
    assert payload["songs"][0]["song"] == "Stub Song"
    assert payload["songs"][0]["gameplay"] == 1
    assert payload["songs"][0]["lightshow"] == 1
    assert payload["formats"][0]["format"] == 4
    assert payload["characteristics"][0]["characteristic"] == "Standard"
    assert payload["difficulties"][0]["difficulty"] == "ExpertPlus"
    assert payload["extraction"]["gameplay"] == 1
    assert payload["extraction"]["lightshow"] == 1
    assert payload["failures"] == []


@pytest.mark.corpus
def test_detect_game_root_finds_installed_beat_saber() -> None:
    root = detect_game_root()
    if root is None:
        pytest.skip("Beat Saber is not installed on a known Oculus or Steam path")
    assert (root / "Beat Saber_Data").is_dir()
    assert (root / "Beat Saber_Data" / "StreamingAssets").is_dir()


def test_refuse_protected_pacific_coast_highway_output(tmp_path: Path) -> None:
    v5 = tmp_path / "CustomLevels" / "Pacific_Coast_Highway"
    nested = v5 / "_beatforge"
    assert "Pacific_Coast_Highway" in str(refuse_protected_output(v5))
    assert "Pacific_Coast_Highway" in str(refuse_protected_output(nested))
    allowed = tmp_path / "CustomLevels" / "Pacific_Coast_Highway_BeatForge_V6"
    assert refuse_protected_output(allowed) is None


def test_copy_file_skips_same_path(tmp_path: Path) -> None:
    target = tmp_path / "song.ogg"
    target.write_bytes(b"ogg-bytes")
    _copy_file(target, tmp_path / "song.ogg")
    assert target.read_bytes() == b"ogg-bytes"
    other = tmp_path / "copy.ogg"
    _copy_file(target, other)
    assert other.read_bytes() == b"ogg-bytes"


def test_approved_palette_manifest_stamps_nested_palette_status(tmp_path: Path) -> None:
    cover = tmp_path / "cover.png"
    write_default_cover(cover, "Stub Cover")
    scheme = {
        "useOverride": True,
        "colorScheme": {
            "colorSchemeId": "Album",
            "saberAColor": {"r": 0.9, "g": 0.1, "b": 0.2, "a": 1.0},
            "saberBColor": {"r": 0.1, "g": 0.4, "b": 1.0, "a": 1.0},
            "environmentColor0": {"r": 0.8, "g": 0.1, "b": 0.2, "a": 1.0},
            "environmentColor1": {"r": 0.1, "g": 0.3, "b": 0.9, "a": 1.0},
            "obstaclesColor": {"r": 0.4, "g": 0.4, "b": 0.4, "a": 1.0},
            "environmentColor0Boost": {"r": 1.0, "g": 0.2, "b": 0.3, "a": 1.0},
            "environmentColor1Boost": {"r": 0.2, "g": 0.5, "b": 1.0, "a": 1.0},
        },
    }
    path = tmp_path / "palette.json"
    path.write_text(
        json.dumps(
            {
                "status": "approved",
                "artwork": {"path": str(cover), "source": "user-file"},
                "palette": {"status": "needs_approval", "colorScheme": scheme},
            }
        ),
        encoding="utf-8",
    )
    cover_path, color_scheme, manifest = _load_approved_palette(path)
    assert cover_path == cover
    assert color_scheme["useOverride"] is True
    assert manifest["status"] == "approved"
    assert manifest["palette"]["status"] == "approved"
    assert manifest["approvalRequired"] is False


@pytest.mark.corpus
@pytest.mark.skipif(not (GAME_ROOT / "Beat Saber_Data" / "CustomLevels" / "Pacific_Coast_Highway").is_dir(), reason="Pacific Coast Highway V5 unavailable")
def test_pacific_coast_highway_v5_regression() -> None:
    report = validate_package(GAME_ROOT / "Beat Saber_Data" / "CustomLevels" / "Pacific_Coast_Highway")
    totals = report.metrics["totals"]
    assert totals["arcs"] == 42
    assert totals["missingArcTails"] == 42
    assert totals["nextSameColorAfterHold"] == 40
    assert totals["rapidSameHandAfterHold"] == 12
    assert totals["crossHandPathCollisions"] == 8
    assert report.metrics["maps"]["Expert.dat"]["crossHandPathCollisions"] == 4
    assert report.metrics["maps"]["ExpertPlus.dat"]["crossHandPathCollisions"] == 4
    assert report.metrics["maps"]["ExpertPlus.dat"]["rapidSameHandAfterHold"] == 11
    assert report.metrics["totals"].get("postHoldMissingOppositeSetup", 0) == 0


def _minimal_v3(notes: list[dict], sliders: list[dict] | None = None, bursts: list[dict] | None = None) -> dict:
    return {
        "version": "3.3.0",
        "bpmEvents": [],
        "rotationEvents": [],
        "colorNotes": notes,
        "bombNotes": [],
        "obstacles": [],
        "sliders": sliders or [],
        "burstSliders": bursts or [],
        "waypoints": [],
        "basicBeatmapEvents": [],
        "colorBoostBeatmapEvents": [],
        "lightColorEventBoxGroups": [],
        "lightRotationEventBoxGroups": [],
        "lightTranslationEventBoxGroups": [],
        "vfxEventBoxGroups": [],
        "basicEventTypesWithKeywords": {},
        "useNormalEventsAsCompatibleEvents": False,
    }


def test_post_hold_opposite_at_tail_is_not_a_missing_setup(tmp_path: Path) -> None:
    legal = _minimal_v3(
        notes=[
            {"b": 4.0, "x": 3, "y": 1, "c": 1, "d": 1, "a": 0},
            {"b": 4.5, "x": 0, "y": 1, "c": 0, "d": 1, "a": 0},
            {"b": 5.5, "x": 3, "y": 2, "c": 1, "d": 1, "a": 0},
        ],
        bursts=[{"b": 4.0, "c": 1, "d": 1, "x": 3, "y": 1, "tb": 4.5, "tx": 3, "ty": 1, "sc": 2, "s": 0.5}],
    )
    illegal = _minimal_v3(
        notes=[
            {"b": 8.0, "x": 0, "y": 1, "c": 0, "d": 1, "a": 0},
            {"b": 9.0, "x": 0, "y": 2, "c": 0, "d": 1, "a": 0},
        ],
        bursts=[{"b": 8.0, "c": 0, "d": 1, "x": 0, "y": 1, "tb": 8.5, "tx": 0, "ty": 1, "sc": 2, "s": 0.5}],
    )
    (tmp_path / "ExpertStandard.dat").write_text(json.dumps(legal), encoding="utf-8")
    (tmp_path / "ExpertPlusStandard.dat").write_text(json.dumps(illegal), encoding="utf-8")
    info = info_dat("Hold Setup", "Tests", "Beatforge", 120.0)
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [
        {
            "_difficulty": "Expert",
            "_difficultyRank": 7,
            "_beatmapFilename": "ExpertStandard.dat",
            "_noteJumpMovementSpeed": 16.0,
            "_noteJumpStartBeatOffset": 0.0,
        },
        {
            "_difficulty": "ExpertPlus",
            "_difficultyRank": 9,
            "_beatmapFilename": "ExpertPlusStandard.dat",
            "_noteJumpMovementSpeed": 18.0,
            "_noteJumpStartBeatOffset": 0.0,
        },
    ]
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"placeholder")
    write_default_cover(tmp_path / "cover.png", "Hold", 16)
    report = validate_package(tmp_path)
    assert report.metrics["maps"]["ExpertStandard.dat"]["nextSameColorAfterHold"] == 1
    assert report.metrics["maps"]["ExpertStandard.dat"]["postHoldMissingOppositeSetup"] == 0
    assert report.metrics["maps"]["ExpertPlusStandard.dat"]["postHoldMissingOppositeSetup"] == 0
    assert not any(item.code == "POST_HOLD_MISSING_OPPOSITE" for item in report.errors)


def test_build_lighting_is_reactive_and_valid() -> None:
    intents = [
        {"beat": 0.0, "strength": 3.0, "intensity": 0.3, "kind": "note", "section": "intro"},
        {"beat": 4.0, "strength": 3.5, "intensity": 0.5, "kind": "note", "section": "verse"},
        {"beat": 8.0, "strength": 4.0, "intensity": 0.85, "kind": "note", "section": "chorus"},
        {"beat": 8.5, "strength": 4.5, "intensity": 0.9, "kind": "arc", "duration": 1.0, "section": "chorus"},
        {"beat": 10.0, "strength": 5.0, "intensity": 0.95, "kind": "chain", "duration": 0.5, "section": "chorus"},
        {"beat": 12.0, "strength": 2.0, "intensity": 0.2, "kind": "note", "section": "outro"},
    ]
    lighting = build_lighting(intents)
    events = lighting["basicBeatmapEvents"]
    boost = lighting["colorBoostBeatmapEvents"]
    assert len(events) > 0
    assert len(boost) >= 3  # Section changes
    event_types = {e["et"] for e in events}
    assert len(event_types) >= 3  # Multi-channel lighting
    assert lighting["lightColorEventBoxGroups"]
    assert lighting["lightRotationEventBoxGroups"]
    assert lighting["lightTranslationEventBoxGroups"]
    assert len(events) >= 12


def test_arc_matches_sustain_and_keeps_the_same_saber() -> None:
    events = [
        {"beat": float(beat), "snappedBeat": float(beat), "strength": 4.0, "sustainBeats": 2.0 if beat == 8.0 else 0.0}
        for beat in (0, 4, 8, 12, 16, 20)
    ]
    analysis = {"bpm": 120.0, "events": events}
    sections = {"sections": [{"startBeat": 0, "endBeat": 40, "label": "body", "intensity": 0.8}]}
    intents = select_intents(analysis, sections, "Hard")
    arcs = [item for item in intents if item.get("kind") == "arc"]
    assert arcs
    duration = float(arcs[0]["duration"])
    assert 1.5 <= duration <= 2.0
    hands, poses, label, bombs, walls = _solve_joint_assignment(intents, CONFIGS["Hard"], 120.0, difficulty="Hard")
    beatmap, _trace = realize(intents, hands, "Hard", 3, 120.0, poses=poses, pose_solver=label, joint_bombs=bombs, joint_walls=walls)
    sliders = beatmap["sliders"]
    assert sliders
    slider = sliders[0]
    assert abs(float(slider["tb"]) - float(slider["b"]) - duration) < 1e-6
    head = next(note for note in beatmap["colorNotes"] if abs(float(note["b"]) - float(slider["b"])) < 1e-6 and int(note["c"]) == int(slider["c"]))
    tail = next(note for note in beatmap["colorNotes"] if abs(float(note["b"]) - float(slider["tb"])) < 1e-6 and int(note["c"]) == int(slider["c"]))
    assert int(head["c"]) == int(tail["c"]) == int(slider["c"])
    opposite_on_tail = [
        note
        for note in beatmap["colorNotes"]
        if abs(float(note["b"]) - float(slider["tb"])) < 1e-6 and int(note["c"]) != int(slider["c"])
    ]
    assert not opposite_on_tail


def test_hold_consumes_the_onset_on_its_tail_beat() -> None:
    events = [
        {"beat": 0.0, "snappedBeat": 0.0, "strength": 4.0, "sustainBeats": 0.0},
        {"beat": 8.0, "snappedBeat": 8.0, "strength": 5.0, "sustainBeats": 2.0},
        {"beat": 10.0, "snappedBeat": 10.0, "strength": 5.0, "sustainBeats": 0.0},
        {"beat": 16.0, "snappedBeat": 16.0, "strength": 4.0, "sustainBeats": 0.0},
    ]
    analysis = {"bpm": 120.0, "events": events}
    sections = {"sections": [{"startBeat": 0, "endBeat": 32, "label": "chorus", "intensity": 0.8}]}
    intents = [item for item in select_intents(analysis, sections, "Hard") if item.get("layer") != "opening-pulse"]
    arcs = [item for item in intents if item.get("kind") == "arc" and abs(float(item["beat"]) - 8.0) < 1e-6]
    assert arcs
    assert abs(float(arcs[0]["duration"]) - 2.0) < 1e-6
    assert not any(abs(float(item["beat"]) - 10.0) < 1e-6 for item in intents)


def test_propose_and_verify_local_anchors_behavior() -> None:
    audio = synthetic_audio(seconds=12.0, bpm=120.0)
    analysis = analyze_audio_buffer(audio)
    events = analysis.get("events", [])
    proposal = propose_and_verify_anchors(audio, events, 120.0)
    if proposal is not None:
        anchors, metrics = proposal
        assert len(anchors) >= 2
        assert metrics["medianMs"] <= 10.0
        assert metrics["p95Ms"] <= 20.0


@pytest.mark.parametrize("bpm", [90.0, 140.0, 190.0])
@pytest.mark.parametrize("total_beats", [80, 180])
def test_parametric_joint_sat_long_map_feasibility(tmp_path: Path, bpm: float, total_beats: int) -> None:
    events = []
    for index, beat in enumerate(np.arange(1.0, float(total_beats), 0.5)):
        events.append({
            "beat": float(beat),
            "snappedBeat": float(beat),
            "strength": 1.5 + (index % 5),
            "sustainBeats": 1.0 if index % 23 == 0 else 0.0,
        })
    analysis = {"bpm": bpm, "events": events}
    sections = {
        "sections": [
            {"startBeat": 0, "endBeat": total_beats // 2, "label": "verse", "intensity": 0.5},
            {"startBeat": total_beats // 2, "endBeat": total_beats, "label": "peak", "intensity": 0.85},
        ]
    }
    intents = select_intents(analysis, sections, "ExpertPlus")
    assert len(intents) > 0
    hands, poses, solver_label, bombs, walls = _solve_joint_assignment(intents, CONFIGS["ExpertPlus"], bpm, difficulty="ExpertPlus")
    assert solver_label == "joint-cp-sat"
    assert len(hands) == len(intents)
    assert len(poses) == len(intents)
    beatmap, trace = realize(
        intents,
        hands,
        "ExpertPlus",
        42,
        bpm,
        poses=poses,
        pose_solver=solver_label,
        joint_bombs=bombs,
        joint_walls=walls,
    )
    assert trace["poseSolver"] == "joint-cp-sat"
    assert beatmap["version"] == "3.3.0"
    (tmp_path / "ExpertPlusStandard.dat").write_text(json.dumps(beatmap), encoding="utf-8")
    info = info_dat("Parametric", "Tests", "Beatforge", bpm)
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [
        {
            "_difficulty": "ExpertPlus",
            "_difficultyRank": 9,
            "_beatmapFilename": "ExpertPlusStandard.dat",
            "_noteJumpMovementSpeed": 18.0,
            "_noteJumpStartBeatOffset": -0.25,
        }
    ]
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"test-placeholder")
    write_default_cover(tmp_path / "cover.png", "Parametric", 16)
    analysis_dir = tmp_path / "_beatforge"
    analysis_dir.mkdir(exist_ok=True)
    (analysis_dir / "analysis.json").write_text(json.dumps({"status": "timing_verified"}), encoding="utf-8")
    validation = validate_package(tmp_path)
    assert validation.errors == []
    assert validation.status == "playtest_candidate"


