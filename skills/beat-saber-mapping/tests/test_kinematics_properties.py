from __future__ import annotations

import random
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from kinematics import double_is_safe, transition_finding
from choreography import (
    CONFIGS,
    _ensure_opposite_notes_at_hold_tails,
    _hazard_cell_blocked,
    _pose_domain,
    _solve_hazards,
    _wall_forces_body_damage,
    generate_all,
    select_intents,
)


DIRECTIONS = (0, 1, 2, 3, 4, 5, 6, 7, 8)
LANES = (0, 1, 2, 3)
ROWS = (0, 1, 2)
RECOVERIES = (0.125, 0.25, 0.5, 1.0)
BPMS = (60.0, 90.0, 120.0, 180.0, 220.0, 260.0)


def _note(beat: float, x: int, y: int, color: int, direction: int) -> dict:
    return {"b": beat, "x": x, "y": y, "c": color, "d": direction, "a": 0}


def _consecutive_same_color(notes: list[dict], bpm: float, recovery: float) -> list:
    ordered = sorted((note for note in notes if not note.get("_virtualTail")), key=lambda item: (float(item["b"]), int(item["c"])))
    findings = []
    last = {0: None, 1: None}
    for note in ordered:
        color = int(note["c"])
        previous = last[color]
        if previous is not None:
            finding = transition_finding(previous, note, bpm, recovery_beats=recovery)
            if finding:
                findings.append(finding)
        last[color] = note
    return findings


def test_five_thousand_same_color_transitions_cover_required_cases() -> None:
    rng = random.Random(20260822)
    seen = {
        "dot": 0,
        "parity_reset": 0,
        "lane_extreme": 0,
        "row_extreme": 0,
        "arc_exit": 0,
        "chain_exit": 0,
        "simultaneous": 0,
        "illegal": 0,
        "legal": 0,
    }
    bpm_hits = set()
    recovery_hits = set()
    for index in range(5_000):
        bpm = BPMS[index % len(BPMS)]
        recovery = RECOVERIES[index % len(RECOVERIES)]
        bpm_hits.add(bpm)
        recovery_hits.add(recovery)
        color = index % 2
        gap = recovery if index % 7 else 0.0
        previous = _note(
            4.0,
            LANES[index % 4],
            ROWS[index % 3],
            color,
            DIRECTIONS[index % 9],
        )
        current = _note(
            4.0 + gap,
            LANES[(index * 3) % 4],
            ROWS[(index * 5) % 3],
            color,
            DIRECTIONS[(index * 4) % 9],
        )
        if index % 11 == 0:
            previous["d"] = 8
            current["d"] = 1
            seen["dot"] += 1
        if index % 13 == 0:
            previous["d"] = 1
            current["d"] = 0
            seen["parity_reset"] += 1
        if index % 17 == 0:
            previous["x"] = 0
            current["x"] = 3
            seen["lane_extreme"] += 1
        if index % 19 == 0:
            previous["y"] = 0
            current["y"] = 2
            seen["row_extreme"] += 1
        if index % 23 == 0:
            previous = _note(2.0, 1, 1, color, 1)
            current = _note(2.0 + recovery, previous["x"], previous["y"], color, 0)
            seen["arc_exit"] += 1
        if index % 29 == 0:
            previous = _note(8.0, 2, 1, color, 1)
            current = _note(8.0 + 0.125, 2, 1, color, 8)
            seen["chain_exit"] += 1
        finding = transition_finding(previous, current, bpm, recovery_beats=recovery)
        actual_gap = float(current["b"]) - float(previous["b"])
        if actual_gap <= 1e-9:
            seen["simultaneous"] += 1
            assert finding is not None
            assert finding["failedConstraint"] == "simultaneous_same_color"
            assert finding["previousBeat"] == previous["b"]
            assert finding["currentBeat"] == current["b"]
            assert finding["color"] == color
        if finding:
            seen["illegal"] += 1
            for key in (
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
                assert key in finding
        else:
            seen["legal"] += 1
        if index % 31 == 0:
            other = _note(current["b"], 3 - int(current["x"]), int(current["y"]), 1 - color, int(current["d"]))
            red = current if color == 0 else other
            blue = other if color == 0 else current
            _ = double_is_safe(red, blue)
        if index % 37 == 0:
            bomb = {"b": current["b"], "x": int(current["x"]), "y": int(current["y"])}
            assert _hazard_cell_blocked([current], float(bomb["b"]), int(bomb["x"]), int(bomb["y"]))
        if index % 41 == 0:
            wall = {"b": current["b"], "x": 0, "y": 0, "w": 4, "h": 5, "d": 1.0}
            assert _wall_forces_body_damage(wall)
        previous["x"] = rng.choice(LANES)
    assert seen["dot"] >= 400
    assert seen["parity_reset"] >= 300
    assert seen["lane_extreme"] >= 250
    assert seen["row_extreme"] >= 250
    assert seen["arc_exit"] >= 200
    assert seen["chain_exit"] >= 150
    assert seen["simultaneous"] >= 600
    assert seen["illegal"] >= 700
    assert seen["legal"] >= 700
    assert bpm_hits == set(BPMS)
    assert recovery_hits == set(RECOVERIES)


def test_generated_spreads_have_zero_same_color_flow_failures() -> None:
    events = []
    for index, beat in enumerate([n / 4.0 for n in range(4, 96)]):
        events.append(
            {
                "beat": float(beat),
                "snappedBeat": float(beat),
                "strength": 1.2 + (index % 5),
                "sustainBeats": 1.5 if index % 23 == 0 else 0.0,
            }
        )
    analysis = {"bpm": 140.0, "events": events}
    sections = {
        "sections": [
            {"startBeat": 0, "endBeat": 12, "label": "intro", "intensity": 0.3},
            {"startBeat": 12, "endBeat": 40, "label": "body", "intensity": 0.7},
        ]
    }
    maps, _report = generate_all(analysis, sections, 7, Path("missing.sqlite3"))
    for difficulty, payload in maps.items():
        recovery = CONFIGS[difficulty].recovery_beats
        findings = _consecutive_same_color(payload["colorNotes"], 140.0, recovery)
        assert findings == [], (difficulty, findings[:3])
        bombs, walls = _solve_hazards(payload["colorNotes"], select_intents(analysis, sections, difficulty), difficulty, CONFIGS[difficulty])
        for bomb in bombs:
            assert not _hazard_cell_blocked(payload["colorNotes"], float(bomb["b"]), int(bomb["x"]), int(bomb["y"]))
        for wall in walls:
            assert not _wall_forces_body_damage(wall)


def test_hold_tail_helper_never_places_an_illegal_opposite_note() -> None:
    notes = [
        _note(1.0, 0, 1, 0, 1),
        _note(1.0, 3, 1, 1, 1),
        _note(4.5, 0, 1, 0, 1),
    ]
    arcs = [{"c": 1, "b": 1.0, "x": 3, "y": 1, "d": 1, "tb": 3.0, "tx": 3, "ty": 1, "tc": 1}]
    _ensure_opposite_notes_at_hold_tails(notes, arcs, [], 120.0, 0.3)
    opposite_tail = [note for note in notes if abs(float(note["b"]) - 3.0) < 1e-9 and int(note["c"]) == 0]
    assert not opposite_tail
    opposite_mid = [note for note in notes if abs(float(note["b"]) - 2.0) < 1e-9 and int(note["c"]) == 0]
    findings = _consecutive_same_color(notes, 120.0, 0.3)
    assert findings == []


def test_hold_pose_domain_bakes_opposite_companion_into_sat_choice() -> None:
    intent = {"kind": "arc", "beat": 4.0, "duration": 1.0, "intensity": 0.8}
    domain = _pose_domain(intent, 0, 0, 0, 120.0, 0.25)
    holds = [pose for pose in domain if pose["kind"] == "arc"]
    assert holds
    with_companion = [pose for pose in holds if pose.get("companion")]
    assert with_companion
    for pose in with_companion:
        companion = pose["companion"]
        assert int(companion["c"]) == 1
        mid = (float(pose["head"]["b"]) + float(pose["exit"]["b"])) / 2.0
        assert abs(float(companion["b"]) - mid) < 1e-6
        assert abs(float(companion["b"]) - float(pose["exit"]["b"])) > 1e-6
        assert (int(companion["x"]), int(companion["y"])) != (int(pose["exit"]["x"]), int(pose["exit"]["y"]))
