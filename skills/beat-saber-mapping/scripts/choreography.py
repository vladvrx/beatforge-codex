#!/usr/bin/env python3
"""Stateful five-difficulty Beat Saber choreography and v3 serialization."""

from __future__ import annotations

import json
import math
import random
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from beatforge_core import (
    DIFFICULTIES,
    HandState,
    circular_direction_distance,
    default_cache_dir,
    difficulty_rank,
    direction_vector,
    emit_progress,
)
from kinematics import bomb_on_swing_path, double_is_safe, swing_pose, transition_finding
from official_corpus import map_features, normalize_beatmap
from safety_contract import (
    RECOVERY_BEATS,
    hold_span_beats,
    occupied_until,
    occupied_until_from_intent,
    intent_tail_beat,
    same_hand_after_hold_too_soon,
)


def _cp_model_module():
    """Load OR-Tools from the active environment, not a broken bootstrap cache."""

    managed = default_cache_dir() / "python"
    saved = list(sys.path)
    sys.path = [entry for entry in sys.path if Path(entry).resolve() != managed.resolve()]
    try:
        from ortools.sat.python import cp_model
    finally:
        sys.path = saved
    return cp_model


@dataclass(frozen=True)
class DifficultyConfig:
    min_gap: float
    quantile: float
    njs: float
    spawn_offset: float
    recovery_beats: float
    arcs: bool
    chains: bool
    bombs: bool
    walls: bool
    peak_min_gap: float


CONFIGS = {
    "Easy": DifficultyConfig(2.0, 0.78, 10.0, 0.5, RECOVERY_BEATS["Easy"], False, False, False, True, 2.0),
    # Keep Normal below Hard in note density while preserving a full-beat
    # readability floor for non-peak passages.
    "Normal": DifficultyConfig(1.25, 0.68, 11.0, 0.5, RECOVERY_BEATS["Normal"], False, False, True, True, 1.25),
    "Hard": DifficultyConfig(1.0, 0.62, 13.0, 0.5, RECOVERY_BEATS["Hard"], True, True, True, True, 1.0),
    "Expert": DifficultyConfig(0.5, 0.48, 15.0, 0.5, RECOVERY_BEATS["Expert"], True, True, True, True, 0.5),
    "ExpertPlus": DifficultyConfig(0.5, 0.28, 16.0, 0.5, RECOVERY_BEATS["ExpertPlus"], True, True, True, True, 0.25),
}

# Corpus Standard averages across 123 official maps. Shape spreads; never copy notes.
OFFICIAL_NOTE_COUNT_RATIOS = {
    ("Easy", "Normal"): 1.58,
    ("Normal", "Hard"): 1.56,
    ("Hard", "Expert"): 1.42,
    ("Expert", "ExpertPlus"): 1.29,
}

OPENING_SECONDS = 10.0
# Players cannot read a block on beat 0. Keep the first note at least one second in.
FIRST_NOTE_SECONDS = 1.0
# Adjacent hits this close belong to different hands so each saber can finish a swing.
HAND_ALTERNATE_BEATS = 1.0
OPENING_PULSE_SPACING = {
    "Easy": 4.0,
    "Normal": 2.0,
    "Hard": 2.0,
    "Expert": 1.0,
    "ExpertPlus": 1.0,
}


def _hold_tail_beat(intent: dict[str, Any]) -> float:
    return intent_tail_beat(intent)


def _hold_occupied_until(intent: dict[str, Any], recovery_beats: float) -> float:
    return occupied_until_from_intent(intent, recovery_beats)


def section_for_beat(beat: float, sections: dict[str, Any]) -> dict[str, Any]:
    entries = sections.get("sections", [])
    for section in entries:
        start = float(section.get("startBeat", 0.0))
        end = float(section.get("endBeat", math.inf))
        if start <= beat < end:
            return section
    return {"label": "body", "intensity": 0.5}


def dedupe_events(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    by_beat: dict[float, dict[str, Any]] = {}
    for event in analysis.get("events", []):
        beat = float(event.get("snappedBeat", event.get("beat", 0.0)))
        if beat < 0:
            continue
        previous = by_beat.get(beat)
        if previous is None or float(event.get("strength", 0.0)) > float(previous.get("strength", 0.0)):
            by_beat[beat] = {**event, "beat": beat}
    return [by_beat[beat] for beat in sorted(by_beat)]


def first_playable_beat(bpm: float) -> float:
    return FIRST_NOTE_SECONDS * max(float(bpm), 1.0) / 60.0


def opening_pulse_events(analysis: dict[str, Any], difficulty: str) -> list[dict[str, Any]]:
    """Fill a quiet intro on the beat grid, starting after the first-note read window."""

    bpm = float(analysis.get("bpm") or 120.0)
    if bpm <= 0:
        return []
    limit = OPENING_SECONDS * bpm / 60.0
    spacing = OPENING_PULSE_SPACING[difficulty]
    start = first_playable_beat(bpm)
    occupied = {
        round(float(event.get("snappedBeat", event.get("beat", 0.0))), 6)
        for event in analysis.get("events", [])
        if float(event.get("snappedBeat", event.get("beat", 0.0))) <= limit + 1e-9
    }
    events: list[dict[str, Any]] = []
    beat = 0.0
    while beat + 1e-9 < start:
        beat += spacing
    while beat <= limit + 1e-9:
        key = round(beat, 6)
        if key not in occupied:
            events.append(
                {
                    "beat": beat,
                    "snappedBeat": beat,
                    "strength": 2.6,
                    "sustainBeats": 0.0,
                    "layer": "opening-pulse",
                }
            )
        beat += spacing
    return events


def select_intents(analysis: dict[str, Any], sections: dict[str, Any], difficulty: str) -> list[dict[str, Any]]:
    config = CONFIGS[difficulty]
    events = dedupe_events(
        {**analysis, "events": [*analysis.get("events", []), *opening_pulse_events(analysis, difficulty)]}
    )
    if not events:
        return []
    earliest = first_playable_beat(float(analysis.get("bpm") or 120.0))
    base_threshold = float(np.quantile([float(event.get("strength", 0.0)) for event in events], config.quantile))
    selected: list[dict[str, Any]] = []
    last_beat = -math.inf
    for index, event in enumerate(events):
        beat = float(event["beat"])
        if beat + 1e-9 < earliest:
            continue
        section = section_for_beat(beat, sections)
        intensity = float(section.get("intensity", 0.5))
        is_peak = intensity >= 0.7 or any(
            token in str(section.get("label", "")).lower() for token in ("peak", "chorus", "drop")
        )
        gap = config.peak_min_gap if is_peak else config.min_gap
        adaptive_gap = max(gap, gap * (1.15 - 0.35 * intensity))
        if beat - last_beat + 1e-9 < adaptive_gap:
            continue
        if any(
            old.get("kind") in {"arc", "chain"}
            and float(old["beat"]) + 1e-9 < beat <= float(old["beat"]) + float(old.get("duration") or 0.0) + 1e-9
            for old in selected
        ):
            continue
        strength = float(event.get("strength", 0.0))
        energies = event.get("stemEnergy") if isinstance(event.get("stemEnergy"), dict) else {}
        layer = str(event.get("layer") or "")
        instrument = float(energies.get("drums") or 0.0) + float(energies.get("bass") or 0.0) + float(energies.get("guitar") or 0.0)
        vocal = float(energies.get("vocals") or 0.0)
        layered = bool(energies) and instrument >= vocal * 1.15 and layer in {"drums", "bass", "guitar", "piano", "other"}
        if difficulty in {"Expert", "ExpertPlus"} and layered:
            strength *= 1.35
        threshold = base_threshold
        if is_peak:
            threshold *= 0.88 if difficulty in {"Expert", "ExpertPlus"} else 0.94
        elif intensity < 0.4:
            threshold *= 1.12
        pulse = str(event.get("layer") or "") == "opening-pulse"
        if (
            strength < threshold
            and beat % 4 != 0
            and not (difficulty in {"Expert", "ExpertPlus"} and layered)
            and not pulse
        ):
            continue
        hold = 0.0
        sustain = float(event.get("sustainBeats", 0.0))
        is_vocal_hold = float(energies.get("vocals", 0.0)) >= 0.20
        is_synth_glide = float(energies.get("other", 0.0)) >= 0.15 or float(energies.get("piano", 0.0)) >= 0.15
        is_bass_glide = float(energies.get("bass", 0.0)) >= 0.30
        is_guitar_strum = float(energies.get("guitar", 0.0)) >= 0.25
        is_accent_phrase = (is_peak or intensity >= 0.55) and (abs(beat % 2.0) < 1e-6 or abs(beat % 4.0) < 1e-6)
        is_held_sound = (sustain >= 0.125 or is_vocal_hold or is_synth_glide or is_bass_glide or is_guitar_strum or is_accent_phrase)
        
        kind = "note"
        if config.arcs and is_held_sound:
            next_beat = float(events[index + 1]["beat"]) if index + 1 < len(events) else beat + (sustain if sustain > 0 else 1.0)
            available = next_beat - beat
            arc_lockout = 0.5 if difficulty == "ExpertPlus" else (1.0 if difficulty == "Expert" else 1.5)
            recent_arc = any(abs(beat - float(old.get("beat", -99.0))) < arc_lockout for old in selected if old.get("kind") == "arc")
            
            if not recent_arc and available >= 0.25:
                kind = "arc"
                hold = min(3.0, max(0.5, round(max(sustain, min(available, 1.5)) * 4.0) / 4.0))
                if difficulty == "ExpertPlus" and is_peak and available <= 1.0 + 1e-9:
                    # Leave a readable pickup slot before the next peak accent.
                    hold = 0.5
            
        if config.chains and kind == "note" and index + 1 < len(events):
            next_gap = float(events[index + 1]["beat"]) - beat
            is_drum_roll = float(energies.get("drums", 0.0)) >= 0.70
            if is_drum_roll and 0.0625 <= next_gap <= 0.375:
                kind = "chain"
                hold = min(0.25, max(0.0625, next_gap * 0.5))
        if difficulty == "Expert" and abs(beat % 4.0) < 1e-9:
            layer = "combo-stack"
        elif difficulty == "ExpertPlus" and is_peak and abs(beat % 2.0) > 1e-9:
            layer = "combo-stack"
        payload = {
            "beat": beat,
            "strength": strength,
            "kind": kind,
            "duration": hold,
            "section": str(section.get("label", "body")),
            "intensity": intensity,
            "layer": layer or "mix",
            "stemEnergy": energies,
        }
        selected.append(payload)
        last_beat = beat
    return selected


def _solve_with_cp_sat(intents: list[dict[str, Any]], config: DifficultyConfig) -> list[int] | None:
    try:
        cp_model = _cp_model_module()
    except ImportError:
        return None
    if not intents:
        return []
    model = cp_model.CpModel()
    hand = [model.new_bool_var(f"hand_{index}") for index in range(len(intents))]
    repeats = []
    hold_active: dict[int, Any] = {}
    for index in range(1, len(intents)):
        gap = float(intents[index]["beat"]) - float(intents[index - 1]["beat"])
        later_beat = float(intents[index]["beat"])
        inside_hold = False
        for hold in intents[:index]:
            if hold["kind"] not in {"arc", "chain"}:
                continue
            start = float(hold["beat"])
            until = _hold_occupied_until(hold, config.recovery_beats)
            if start < later_beat < until - 1e-9:
                inside_hold = True
                break
        if inside_hold:
            continue
        if gap <= max(config.min_gap, config.recovery_beats, HAND_ALTERNATE_BEATS if gap > 1e-9 else 0.0):
            model.add(hand[index] != hand[index - 1])
        same = model.new_bool_var(f"same_hand_{index}")
        model.add(hand[index] == hand[index - 1]).only_enforce_if(same)
        model.add(hand[index] != hand[index - 1]).only_enforce_if(same.Not())
        repeats.append(same)
    for index, intent in enumerate(intents):
        if intent["kind"] not in {"arc", "chain"}:
            continue
        active = model.new_bool_var(f"hold_active_{index}")
        hold_active[index] = active
        occupied_until = _hold_occupied_until(intent, config.recovery_beats)
        tail = _hold_tail_beat(intent)
        for later in range(index + 1, len(intents)):
            later_beat = float(intents[later]["beat"])
            if later_beat >= occupied_until:
                break
            if abs(later_beat - tail) <= 1e-9:
                continue
            model.add(hand[later] != hand[index]).only_enforce_if(active)
        first_after_tail = next(
            (later for later in range(index + 1, len(intents)) if float(intents[later]["beat"]) > _hold_tail_beat(intent) + 1e-9),
            None,
        )
        if first_after_tail is not None:
            model.add(hand[first_after_tail] != hand[index]).only_enforce_if(active)
    imbalance = model.new_int_var(0, len(intents), "imbalance")
    total_blue = sum(hand)
    model.add_abs_equality(imbalance, 2 * total_blue - len(intents))
    model.minimize(imbalance * 4 + sum(repeats) - sum(hold_active.values()) * 100)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    solver.parameters.num_search_workers = 8
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    for index, active in hold_active.items():
        if not solver.value(active):
            intents[index]["downgradedFrom"] = intents[index]["kind"]
            intents[index]["kind"] = "note"
            intents[index]["duration"] = 0.0
    return [int(solver.value(variable)) for variable in hand]


def _solve_with_beam(intents: list[dict[str, Any]], config: DifficultyConfig, beam_width: int = 256) -> list[int]:
    beam: list[tuple[float, list[int], tuple[float, float], tuple[int, int]]] = [(0.0, [], (-math.inf, -math.inf), (0, 0))]
    for intent in intents:
        intent_index = len(beam[0][1]) if beam else 0
        beat = float(intent["beat"])
        duration = float(intent["duration"])
        next_beam: list[tuple[float, list[int], tuple[float, float], tuple[int, int]]] = []
        for score, sequence, occupied, counts in beam:
            for color in (0, 1):
                if beat < occupied[color] - 1e-9:
                    continue
                wrong_post_hold_hand = False
                for hold_index, hold in enumerate(intents[:intent_index]):
                    if hold["kind"] not in {"arc", "chain"}:
                        continue
                    tail = _hold_tail_beat(hold)
                    earlier_after = any(tail < float(item["beat"]) < beat - 1e-9 for item in intents[hold_index + 1 : intent_index])
                    if not earlier_after and beat > tail + 1e-9 and sequence[hold_index] == color:
                        wrong_post_hold_hand = True
                        break
                if wrong_post_hold_hand:
                    continue
                repeat = bool(sequence and sequence[-1] == color)
                previous_beat = float(intents[len(sequence) - 1]["beat"]) if sequence else -math.inf
                gap = beat - previous_beat
                if repeat and 1e-9 < gap <= HAND_ALTERNATE_BEATS:
                    other_free = occupied[1 - color] <= beat + 1e-9
                    if other_free:
                        continue
                penalty = abs((counts[color] + 1) - counts[1 - color]) * 0.15
                if repeat:
                    penalty += 8.0 if gap <= config.recovery_beats else 1.25
                new_occupied = list(occupied)
                if intent["kind"] in {"arc", "chain"}:
                    new_occupied[color] = _hold_occupied_until(intent, config.recovery_beats)
                new_counts = list(counts)
                new_counts[color] += 1
                next_beam.append((score + penalty, sequence + [color], tuple(new_occupied), tuple(new_counts)))
        if not next_beam:
            raise RuntimeError(f"no legal hand assignment at beat {beat}")
        next_beam.sort(key=lambda item: item[0])
        beam = next_beam[:beam_width]
    return beam[0][1]


def solve_hands(intents: list[dict[str, Any]], config: DifficultyConfig) -> tuple[list[int], str]:
    result = _solve_with_cp_sat(intents, config)
    if result is not None:
        return result, "cp-sat"
    try:
        return _solve_with_beam(intents, config), "beam-search"
    except RuntimeError:
        pass
    holds = [index for index, intent in enumerate(intents) if intent["kind"] in {"arc", "chain"}]
    if holds:
        for index in holds:
            intents[index]["downgradedFrom"] = intents[index]["kind"]
            intents[index]["kind"] = "note"
            intents[index]["duration"] = 0.0
        try:
            return _solve_with_beam(intents, config), "beam-search-with-hold-relaxation"
        except RuntimeError:
            pass
    raise RuntimeError("no legal two-hand assignment exists even after relaxing every hold")


def lane_for(color: int, index: int, intensity: float) -> int:
    normal = ((0, 1, 0, 1), (3, 2, 3, 2))[color][index % 4]
    if intensity > 0.72 and index % 11 == 5:
        return 2 if color == 0 else 1
    return normal


def row_for(index: int, intensity: float) -> int:
    pattern = (1, 0, 1, 2, 1, 0, 2, 1) if intensity > 0.55 else (1, 0, 1, 1, 2, 1)
    return pattern[index % len(pattern)]


def swing_family(color: int, parity: int, beat: float) -> tuple[int, int, int]:
    """Cardinal plus two in-family diagonals. Every fourth bar is a left/right phrase."""

    horizontal = int(beat // 4.0) % 4 == 3
    if horizontal:
        if color == 0:
            return (3, 7, 5) if parity == 0 else (2, 6, 4)
        return (2, 6, 4) if parity == 0 else (3, 7, 5)
    if color == 0:
        return (1, 6, 7) if parity == 0 else (0, 4, 5)
    return (1, 7, 6) if parity == 0 else (0, 5, 4)


def cut_for(color: int, parity: int, index: int, beat: float = 0.0) -> int:
    family = swing_family(color, parity, beat)
    rotate = int(beat // 4.0) % 3
    return family[(index + rotate + color) % 3]


def _candidate_notes(
    color: int,
    parity: int,
    index: int,
    intensity: float,
    beat: float,
) -> list[dict[str, Any]]:
    """Enumerate combo-first arrow poses. Dots are a rare reset, not the default cut."""

    if color == 0:
        home = (0, 1)
    else:
        home = (3, 2)
    preferred = swing_family(color, parity, beat)
    lead = cut_for(color, parity, index, beat)
    notes: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()

    def add(x: int, y: int, direction: int) -> None:
        key = (x, y, direction)
        if key in seen:
            return
        seen.add(key)
        notes.append({"b": beat, "x": x, "y": y, "c": color, "d": direction, "a": 0})

    lane = (home[0], home[1], home[0], 2 if color == 0 else 1)[index % 4]
    # Official electronic Expert+ is bottom- and mid-heavy. Top row is an accent.
    add(lane, 0, lead)
    add(lane, 1, lead)
    add(home[0], 0, lead)
    add(home[1], 1, preferred[1])
    add(home[1], 0, preferred[0])
    add(2, 1, lead)
    add(1, 0, preferred[1])
    add(home[0], 1, preferred[0])
    add(home[1], 1, preferred[1])
    add(lane, 2, preferred[0])
    add(3 if color == 0 else 0, 1, preferred[1])
    if intensity >= 0.45:
        add(0 if color == 0 else 3, 2, preferred[2])
        add(3 if color == 0 else 0, 0, preferred[1])
    if index % 16 == 15:
        add(home[0], 1, 8)
    return notes


def _annotate_pose(pose: dict[str, Any]) -> dict[str, Any]:
    """Attach lean and exit momentum so SAT and realize share the same hand state."""

    exit_note = pose["exit"] or pose["head"]
    color = int(pose["head"]["c"])
    dx, dy = direction_vector(int(exit_note["d"]))
    pose["momentum"] = (float(dx), float(dy))
    pose["bodyLean"] = (int(exit_note["x"]) - 1.5) * (1.0 if color == 0 else -1.0) * 0.15
    return pose


def _companion_for_hold(head: dict[str, Any], tail: dict[str, Any]) -> dict[str, Any] | None:
    """Opposite-hand note in the *middle* of a hold so the arc tail stays the same color."""

    span = float(tail["b"]) - float(head["b"])
    if span < 1.0 - 1e-9:
        return None
    hold_color = int(tail["c"])
    other = 1 - hold_color
    mid = round(float(head["b"]) + span * 0.5, 6)
    hold_note = {
        "b": mid,
        "x": int(head["x"]),
        "y": int(head["y"]),
        "c": hold_color,
        "d": int(head["d"]),
        "a": 0,
    }
    seed = int(round(mid * 8)) + hold_color
    for candidate in _candidate_notes(other, 0, seed, 0.55, mid):
        if int(candidate["x"]) == int(tail["x"]) and int(candidate["y"]) == int(tail["y"]):
            continue
        if int(candidate["x"]) == int(head["x"]) and int(candidate["y"]) == int(head["y"]):
            continue
        red = candidate if other == 0 else hold_note
        blue = hold_note if other == 0 else candidate
        if double_is_safe(red, blue):
            return {**candidate, "b": mid}
    return None


def _hold_with_companion(
    head: dict[str, Any],
    tail: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    pose = {"head": head, "tail": tail, "exit": tail, "kind": kind}
    companion = _companion_for_hold(head, tail)
    if companion is not None:
        pose["companion"] = companion
    return _annotate_pose(pose)


def _candidate_chain_tails(
    head: dict[str, Any],
    color: int,
    duration: float,
    index: int,
) -> list[dict[str, Any]]:
    """Generate rich candidate chain tails across all 5 official corpus archetypes (In-Place, Vertical, Horizontal, Diagonal, Knight's Arc)."""
    beat = round(float(head["b"]) + max(0.03125, hold_span_beats("chain", duration)), 6)
    hx = int(head["x"])
    hy = int(head["y"])
    d = int(head["d"])

    tails = []
    # Dynamic base slice count
    if duration <= 0.0625:
        base_sc = 3
    elif duration <= 0.125:
        base_sc = 4
    elif duration <= 0.25:
        base_sc = 5
    elif duration <= 0.50:
        base_sc = 6
    else:
        base_sc = 8

    # 1. Linear In-Place Roll (Buzz / Stutter) - dx=0, dy=0 [31.1% of official corpus]
    tails.append({"b": beat, "x": hx, "y": hy, "c": color, "d": d, "a": 0, "slices": base_sc, "squish": 1.0})

    # 2. Direction-aligned directional sweeps (dx, dy)
    dir_deltas = {
        0: [(0, 1), (0, 2), (-1 if color == 0 else 1, 1), (1 if color == 0 else -1, 1)],
        1: [(0, -1), (0, -2), (1 if color == 0 else -1, -1), (-1 if color == 0 else 1, -1)],
        2: [(-1, 0), (-2, 0), (-3, 0), (-1, 1), (-1, -1)],
        3: [(1, 0), (2, 0), (3, 0), (1, 1), (1, -1)],
        4: [(-1, 1), (-1, 2), (-2, 1), (0, 1), (-1, 0)],
        5: [(1, 1), (1, 2), (2, 1), (0, 1), (1, 0)],
        6: [(-1, -1), (-1, -2), (-2, -1), (0, -1), (-1, 0)],
        7: [(1, -1), (1, -2), (2, -1), (0, -1), (1, 0)],
        8: [(0, 1), (0, -1), (1 if color == 1 else -1, 0), (-1 if color == 1 else 1, 0), (0, 2), (1, 1), (-1, 1)],
    }

    deltas = dir_deltas.get(d, [(0, 1), (1 if color == 1 else -1, 0)])
    for dx, dy in deltas:
        tx = min(3, max(0, hx + dx))
        ty = min(2, max(0, hy + dy))
        if (tx, ty) != (hx, hy):
            sc = base_sc if abs(dx) + abs(dy) <= 1 else min(8, base_sc + 1)
            tails.append({"b": beat, "x": tx, "y": ty, "c": color, "d": d, "a": 0, "slices": sc, "squish": 1.0})

    return tails


def _default_tail(head: dict[str, Any], color: int, kind: str, duration: float, index: int) -> dict[str, Any]:
    beat = round(float(head["b"]) + hold_span_beats(kind, duration), 6)
    x = int(head["x"])
    y = int(head["y"])
    d = int(head["d"])
    if kind == "chain":
        tails = _candidate_chain_tails(head, color, duration, index)
        return tails[0] if tails else {"b": beat, "x": x, "y": y, "c": color, "d": d, "a": 0}
    
    # Rich Arc Tail Varieties (Upward surges, downward plunges, lateral swoops)
    if d in (0, 4, 5):  # Upward cuts -> finish on downward natural cut
        tail_y = min(2, y + 1)
        tail_x = min(3, max(0, x + (1 if d == 5 else (-1 if d == 4 else (1 if color == 0 else -1)))))
        tail_d = 1
    elif d in (1, 6, 7):  # Downward cuts -> finish on upward natural cut
        tail_y = max(0, y - 1)
        tail_x = min(3, max(0, x + (1 if d == 7 else (-1 if d == 6 else (-1 if color == 1 else 1)))))
        tail_d = 0
    elif d in (2, 3):  # Horizontal cuts -> finish on opposite lateral
        tail_y = y
        tail_x = min(3, max(0, x + (-1 if d == 2 else 1)))
        tail_d = 3 if d == 2 else 2
    else:  # Dot -> natural parity
        tail_y = 2 if y == 0 else 0
        tail_x = min(3, max(0, x + (1 if color == 0 else -1)))
        tail_d = 1 if tail_y == 2 else 0

    return {
        "b": beat,
        "x": tail_x,
        "y": tail_y,
        "c": color,
        "d": tail_d,
        "a": 0,
    }


def _pose_domain(
    intent: dict[str, Any],
    color: int,
    parity: int,
    index: int,
    bpm: float,
    recovery_beats: float,
) -> list[dict[str, Any]]:
    beat = float(intent["beat"])
    intensity = float(intent.get("intensity", 0.5))
    kind = intent["kind"]
    duration = float(intent.get("duration") or 0.0)
    heads = _candidate_notes(color, parity, index, intensity, beat)
    if str(intent.get("layer") or "") == "combo-stack":
        lead = cut_for(color, parity, index, beat)
        if color == 0:
            pair_cells = ((1, 1), (0, 1), (1, 0), (1, 2))
        else:
            pair_cells = ((2, 1), (3, 1), (2, 2), (2, 0))
        preferred = [
            {"b": beat, "x": x, "y": y, "c": color, "d": lead, "a": 0}
            for x, y in pair_cells
        ]
        heads = preferred + [head for head in heads if (int(head["x"]), int(head["y"])) not in set(pair_cells)]
    note_poses = [_annotate_pose({"head": head, "tail": None, "exit": head, "kind": "note"}) for head in heads[:16]]
    if kind not in {"arc", "chain"}:
        return note_poses
    hold_poses: list[dict[str, Any]] = []
    if kind == "chain":
        for head in heads[:8]:
            cand_tails = _candidate_chain_tails(head, color, duration, index)
            for tail in cand_tails:
                pose = _hold_with_companion(head, tail, "chain")
                hold_poses.append(pose)
        return note_poses[:3] + hold_poses[:12]
    for head in heads[:12]:
        tail = _default_tail(head, color, kind, duration, index)
        if not transition_finding(head, tail, bpm, recovery_beats=recovery_beats):
            hold_poses.append(_hold_with_companion(head, tail, "arc"))
    return note_poses[:4] + hold_poses[:8]


def _joint_combined_domain(
    intent: dict[str, Any],
    index: int,
    bpm: float,
    recovery_beats: float,
) -> list[dict[str, Any]]:
    """Mix colors and swing families so CP-SAT cannot default to down-from-top on every hit."""

    beat = float(intent["beat"])
    stack = str(intent.get("layer") or "") == "combo-stack"
    lead_parity = int(beat // 4.0) % 2 if stack else (index + int(beat // 2.0)) % 2
    buckets = {
        (color_id, parity): _pose_domain(intent, color_id, parity, index, bpm, recovery_beats)[:6]
        for color_id in (0, 1)
        for parity in (0, 1)
    }
    order = (
        (0, lead_parity),
        (1, lead_parity),
        (0, 1 - lead_parity),
        (1, 1 - lead_parity),
    )
    combined: list[dict[str, Any]] = []
    depth = max((len(buckets[key]) for key in order), default=0)
    for slot in range(depth):
        for key in order:
            bucket = buckets[key]
            if slot < len(bucket):
                combined.append(bucket[slot])
    return combined


def _same_hand_transition_is_illegal(
    previous: dict[str, Any],
    current: dict[str, Any],
    bpm: float,
    recovery_beats: float,
) -> bool:
    if transition_finding(previous, current, bpm, recovery_beats=recovery_beats):
        return True
    gap = float(current["b"]) - float(previous["b"])
    if gap <= 0.25 + 1e-7:
        dist = math.hypot(int(current["x"]) - int(previous["x"]), int(current["y"]) - int(previous["y"]))
        if dist > 2.5:
            return True
    return False


def _companion_conflicts(
    hold_pose: dict[str, Any],
    other_pose: dict[str, Any],
    bpm: float,
    recovery_beats: float,
) -> bool:
    companion = hold_pose.get("companion")
    if not companion:
        return False
    other_head = other_pose["head"]
    other_exit = other_pose["exit"] or other_head
    companion_beat = float(companion["b"])
    other_beat = float(other_head["b"])
    companion_color = int(companion["c"])
    other_color = int(other_head["c"])
    if abs(other_beat - companion_beat) <= 1e-9:
        if other_color == companion_color:
            return True
        red = companion if companion_color == 0 else other_head
        blue = other_head if companion_color == 0 else companion
        return not double_is_safe(red, blue)
    if other_color != companion_color:
        return False
    if other_beat < companion_beat:
        return bool(_same_hand_transition_is_illegal(other_exit, companion, bpm, recovery_beats))
    return bool(_same_hand_transition_is_illegal(companion, other_head, bpm, recovery_beats))


def _hold_pose_conflicts_with_other(
    hold_pose: dict[str, Any],
    other_pose: dict[str, Any],
    bpm: float,
    recovery_beats: float,
) -> bool:
    if _companion_conflicts(hold_pose, other_pose, bpm, recovery_beats):
        return True
    tail = hold_pose.get("tail")
    if tail is None or hold_pose.get("kind") not in {"arc", "chain"}:
        return False
    other_head = other_pose["head"]
    if abs(float(tail["b"]) - float(other_head["b"])) > 1e-9:
        return False
    if int(tail["c"]) == int(other_head["c"]):
        return True
    red = tail if int(tail["c"]) == 0 else other_head
    blue = other_head if int(tail["c"]) == 0 else tail
    return not double_is_safe(red, blue)


def _next_same_hand(hands: list[int], start: int) -> int | None:
    color = hands[start]
    for later in range(start + 1, len(hands)):
        if hands[later] == color:
            return later
    return None


def _strip_redundant_companions(intents: list[dict[str, Any]], domains: list[list[dict[str, Any]]]) -> None:
    """Drop baked companions when an intent already occupies that beat."""

    beats = {round(float(intent["beat"]), 6) for intent in intents}
    for index, intent in enumerate(intents):
        if intent["kind"] not in {"arc", "chain"}:
            continue
        for pose in domains[index]:
            companion = pose.get("companion")
            if companion is not None and round(float(companion["b"]), 6) in beats:
                pose.pop("companion", None)


def _solve_poses_with_cp_sat(
    intents: list[dict[str, Any]],
    hands: list[int],
    config: DifficultyConfig,
    bpm: float,
    *,
    allow_skip: bool,
) -> list[dict[str, Any] | None] | None:
    try:
        cp_model = _cp_model_module()
    except ImportError:
        return None
    if not intents:
        return []
    domains: list[list[dict[str, Any]]] = []
    parities = {0: 0, 1: 0}
    for index, (intent, color) in enumerate(zip(intents, hands)):
        domain = _pose_domain(intent, color, parities[color], index, bpm, config.recovery_beats)
        domains.append(domain)
        parities[color] = 1 - parities[color]
        if intent["kind"] in {"arc", "chain"}:
            parities[color] = 1 - parities[color]
    _strip_redundant_companions(intents, domains)
    if any(not domain for domain in domains) and not allow_skip:
        return None
    model = cp_model.CpModel()
    choice = []
    for index, domain in enumerate(domains):
        skip_index = len(domain)
        upper = skip_index if domain or allow_skip else 0
        variable = model.new_int_var(0, max(upper, 0), f"pose_{index}")
        choice.append(variable)
        if not domain:
            model.add(variable == 0)
            continue
        if not allow_skip:
            model.add(variable < skip_index)
    for index in range(len(hands)):
        later = _next_same_hand(hands, index)
        if later is None or not domains[index] or not domains[later]:
            continue
        forbidden: list[tuple[int, int]] = []
        for left_i, left in enumerate(domains[index]):
            for right_i, right in enumerate(domains[later]):
                if _same_hand_transition_is_illegal(
                    left["exit"],
                    right["head"],
                    bpm,
                    recovery_beats=config.recovery_beats,
                ):
                    forbidden.append((left_i, right_i))
        if forbidden:
            model.add_forbidden_assignments([choice[index], choice[later]], forbidden)
    by_beat: dict[float, list[int]] = {}
    for index, intent in enumerate(intents):
        by_beat.setdefault(round(float(intent["beat"]), 6), []).append(index)
    same_cut_terms = []
    flow_cut_terms = []
    for indexes in by_beat.values():
        reds = [index for index in indexes if hands[index] == 0]
        blues = [index for index in indexes if hands[index] == 1]
        if len(reds) != 1 or len(blues) != 1:
            continue
        red_i, blue_i = reds[0], blues[0]
        if not domains[red_i] or not domains[blue_i]:
            continue
        forbidden = [
            (left_i, right_i)
            for left_i, left in enumerate(domains[red_i])
            for right_i, right in enumerate(domains[blue_i])
            if not double_is_safe(left["head"], right["head"])
        ]
        if forbidden:
            model.add_forbidden_assignments([choice[red_i], choice[blue_i]], forbidden)
        for left_i, left in enumerate(domains[red_i]):
            for right_i, right in enumerate(domains[blue_i]):
                if left_i > 3 or right_i > 3:
                    continue
                if int(left["head"].get("d", 8)) != int(right["head"].get("d", 8)):
                    continue
                if int(left["head"].get("d", 8)) == 8:
                    continue
                flag = model.new_bool_var(f"same_cut_{red_i}_{blue_i}_{left_i}_{right_i}")
                model.add(choice[red_i] == left_i).only_enforce_if(flag)
                model.add(choice[blue_i] == right_i).only_enforce_if(flag)
                same_cut_terms.append(flag)
    for index in range(1, len(intents)):
        if hands[index] == hands[index - 1]:
            continue
        gap = float(intents[index]["beat"]) - float(intents[index - 1]["beat"])
        if gap > 1.0 + 1e-9 or gap <= 1e-9:
            continue
        if not domains[index - 1] or not domains[index]:
            continue
        for left_i, left in enumerate(domains[index - 1][:4]):
            for right_i, right in enumerate(domains[index][:4]):
                if int(left["head"].get("d", 8)) != int(right["head"].get("d", 8)):
                    continue
                if int(left["head"].get("d", 8)) == 8:
                    continue
                flag = model.new_bool_var(f"flow_cut_{index}_{left_i}_{right_i}")
                model.add(choice[index - 1] == left_i).only_enforce_if(flag)
                model.add(choice[index] == right_i).only_enforce_if(flag)
                flow_cut_terms.append(flag)
    placed_terms = []
    for index, domain in enumerate(domains):
        if not domain:
            continue
        placed = model.new_bool_var(f"kept_{index}")
        model.add(choice[index] < len(domain)).only_enforce_if(placed)
        model.add(choice[index] >= len(domain)).only_enforce_if(placed.Not())
        placed_terms.append(placed)
    dot_terms = []
    for index, domain in enumerate(domains):
        for pose_index, pose in enumerate(domain):
            if int(pose["head"]["d"]) != 8:
                continue
            flag = model.new_bool_var(f"dot_{index}_{pose_index}")
            model.add(choice[index] == pose_index).only_enforce_if(flag)
            model.add(choice[index] != pose_index).only_enforce_if(flag.Not())
            dot_terms.append(flag)
    if placed_terms:
        model.maximize(
            sum(placed_terms) * 1000
            - sum(choice)
            - sum(dot_terms) * 80
            + sum(same_cut_terms) * 8
            + sum(flow_cut_terms) * 4
        )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 4.0
    solver.parameters.num_search_workers = 4
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    poses: list[dict[str, Any] | None] = []
    for index, domain in enumerate(domains):
        if not domain:
            poses.append(None)
            continue
        selected = int(solver.value(choice[index]))
        poses.append(None if selected >= len(domain) else domain[selected])
    return poses


def _pick_note(
    *,
    color: int,
    parity: int,
    index: int,
    intensity: float,
    beat: float,
    bpm: float,
    recovery_beats: float,
    previous: dict[str, Any] | None,
    simultaneous: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for candidate in _candidate_notes(color, parity, index, intensity, beat):
        if previous and _same_hand_transition_is_illegal(
            previous,
            candidate,
            bpm,
            recovery_beats=recovery_beats,
        ):
            continue
        if simultaneous:
            red, blue = (
                (candidate, simultaneous)
                if color == 0
                else (simultaneous, candidate)
            )
            if not double_is_safe(red, blue):
                continue
        return candidate
    return None


def _solve_poses(
    intents: list[dict[str, Any]],
    hands: list[int],
    config: DifficultyConfig,
    bpm: float,
) -> tuple[list[dict[str, Any] | None], str]:
    strict = _solve_poses_with_cp_sat(intents, hands, config, bpm, allow_skip=False)
    if strict is not None and all(item is not None for item in strict):
        return strict, "cp-sat"
    poses: list[dict[str, Any] | None] = []
    last_exit: dict[int, dict[str, Any] | None] = {0: None, 1: None}
    parities = {0: 0, 1: 0}
    for index, (intent, color) in enumerate(zip(intents, hands)):
        domain = _pose_domain(intent, color, parities[color], index, bpm, config.recovery_beats)
        chosen = None
        simultaneous = next(
            (
                poses[other]["head"]
                for other in range(index)
                if poses[other] is not None
                and abs(float(intents[other]["beat"]) - float(intent["beat"])) <= 1e-9
                and hands[other] != color
            ),
            None,
        )
        if simultaneous:
            partner_cut = int(simultaneous.get("d", 8))
            domain = sorted(
                domain,
                key=lambda pose: 0 if int(pose["head"].get("d", 8)) == partner_cut else 1,
            )
        for candidate in domain:
            previous = last_exit[color]
            if previous and _same_hand_transition_is_illegal(
                previous, candidate["head"], bpm, recovery_beats=config.recovery_beats
            ):
                continue
            if simultaneous and not double_is_safe(
                candidate["head"] if color == 0 else simultaneous,
                simultaneous if color == 0 else candidate["head"],
            ):
                continue
            chosen = candidate
            break
        poses.append(chosen)
        parities[color] = 1 - parities[color]
        if intent["kind"] in {"arc", "chain"}:
            parities[color] = 1 - parities[color]
        if chosen is not None:
            last_exit[color] = chosen["exit"]
    return poses, "beam-search"


def _pose_notes(pose: dict[str, Any]) -> list[dict[str, Any]]:
    notes = [pose["head"]]
    tail = pose.get("tail")
    if tail is not None:
        notes.append(tail)
    return notes


def _pose_blocks_bomb(pose: dict[str, Any], bomb: dict[str, Any]) -> bool:
    return any(bomb_on_swing_path(bomb, note) for note in _pose_notes(pose))


def _pose_blocks_wall(pose: dict[str, Any], wall: dict[str, Any]) -> bool:
    return _wall_blocked(
        _pose_notes(pose),
        float(wall["b"]),
        float(wall["d"]),
        int(wall["x"]),
        int(wall["w"]),
    )


def _solve_joint_model(
    intents: list[dict[str, Any]],
    config: DifficultyConfig,
    bpm: float,
    *,
    fixed_colors: dict[int, int] | None = None,
    fixed_poses: dict[int, dict[str, Any] | None] | None = None,
    incoming_occupancy: list[tuple[int, float]] | None = None,
    incoming_exits: dict[int, dict[str, Any]] | None = None,
    incoming_heads_by_beat: dict[float, list[dict[str, Any]]] | None = None,
    time_limit: float = 3.0,
    bomb_candidates: list[dict[str, Any]] | None = None,
    wall_candidates: list[dict[str, Any]] | None = None,
    difficulty: str = "ExpertPlus",
) -> tuple[list[int], list[dict[str, Any] | None], str, list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Assign color, lane, row, cut, hold lifecycle, bombs, and walls together."""

    try:
        cp_model = _cp_model_module()
    except ImportError:
        return None
    if not intents:
        return [], [], "joint-cp-sat", [], []
    domains: list[list[dict[str, Any]]] = []
    for index, intent in enumerate(intents):
        combined = _joint_combined_domain(intent, index, bpm, config.recovery_beats)
        frozen = None if not fixed_poses or index not in fixed_poses else fixed_poses[index]
        if frozen is not None:
            combined = [frozen] + [pose for pose in combined if pose is not frozen]
        if not combined and not (fixed_poses and index in fixed_poses and frozen is None):
            return None
        domains.append(combined)
    _strip_redundant_companions(intents, domains)
    model = cp_model.CpModel()
    choice = []
    color = []
    placed = []
    skip_index = []
    for index, domain in enumerate(domains):
        skip = len(domain)
        skip_index.append(skip)
        choice.append(model.new_int_var(0, skip, f"joint_{index}"))
        color.append(model.new_int_var(0, 1, f"color_{index}"))
        kept = model.new_bool_var(f"placed_{index}")
        placed.append(kept)
        model.add(choice[index] < skip).only_enforce_if(kept)
        model.add(choice[index] >= skip).only_enforce_if(kept.Not())
        if fixed_colors and index in fixed_colors:
            model.add(color[index] == int(fixed_colors[index]))
        if fixed_poses and index in fixed_poses:
            frozen = fixed_poses[index]
            if frozen is None:
                model.add(placed[index] == 0)
            else:
                model.add(choice[index] == 0)
                model.add(placed[index] == 1)
                model.add(color[index] == int(frozen["head"]["c"]))
        model.add_allowed_assignments(
            [choice[index], color[index]],
            [(pose_index, int(pose["head"]["c"])) for pose_index, pose in enumerate(domain)]
            + [(skip, 0), (skip, 1)],
        )
    hold_flags = []
    for index, domain in enumerate(domains):
        is_hold = model.new_bool_var(f"is_hold_{index}")
        hold_flags.append(is_hold)
        model.add_allowed_assignments(
            [choice[index], is_hold],
            [(pose_index, 1 if pose.get("kind") in {"arc", "chain"} else 0) for pose_index, pose in enumerate(domain)]
            + [(skip_index[index], 0)],
        )
        model.add(is_hold == 0).only_enforce_if(placed[index].Not())
    for index, intent in enumerate(intents):
        if intent["kind"] not in {"arc", "chain"}:
            model.add(hold_flags[index] == 0)
            continue
        occupied_until = _hold_occupied_until(intent, config.recovery_beats)
        for later in range(index + 1, len(intents)):
            if float(intents[later]["beat"]) >= occupied_until:
                break
            model.add(color[later] != color[index]).only_enforce_if(
                [placed[later], placed[index], hold_flags[index]]
            )
        first_after_tail = next(
            (
                later
                for later in range(index + 1, len(intents))
                if float(intents[later]["beat"]) > _hold_tail_beat(intent) + 1e-9
            ),
            None,
        )
        if first_after_tail is not None:
            model.add(color[first_after_tail] != color[index]).only_enforce_if(
                [placed[first_after_tail], placed[index], hold_flags[index]]
            )
        neighbors: list[int] = []
        tail_beat = round(_hold_tail_beat(intent), 6)
        for other, other_intent in enumerate(intents):
            if other == index:
                continue
            if round(float(other_intent["beat"]), 6) == tail_beat:
                neighbors.append(other)
        seen_neighbors: set[int] = set()
        for other in neighbors:
            if other in seen_neighbors:
                continue
            seen_neighbors.add(other)
            forbidden: list[tuple[int, int]] = []
            for hold_i, hold_pose in enumerate(domains[index]):
                if hold_pose.get("kind") not in {"arc", "chain"}:
                    continue
                for other_i, other_pose in enumerate(domains[other]):
                    if _hold_pose_conflicts_with_other(hold_pose, other_pose, bpm, config.recovery_beats):
                        forbidden.append((hold_i, other_i))
            if forbidden:
                model.add_forbidden_assignments([choice[index], choice[other]], forbidden)
    for occupied_color, until in incoming_occupancy or []:
        for later, intent in enumerate(intents):
            if float(intent["beat"]) >= until:
                continue
            model.add(color[later] != occupied_color).only_enforce_if(placed[later])
    if incoming_exits:
        seen_before = {
            saber: model.new_bool_var(f"seen_{saber}_before_0")
            for saber in (0, 1)
        }
        model.add(seen_before[0] == 0)
        model.add(seen_before[1] == 0)
        for index, domain in enumerate(domains):
            for saber, previous in incoming_exits.items():
                for pose_index, pose in enumerate(domain):
                    if int(pose["head"]["c"]) != saber:
                        continue
                    if not _same_hand_transition_is_illegal(
                        previous,
                        pose["head"],
                        bpm,
                        recovery_beats=config.recovery_beats,
                    ):
                        continue
                    model.add(choice[index] != pose_index).only_enforce_if(seen_before[saber].Not())
            if index + 1 >= len(domains):
                continue
            updated = {}
            for saber in (0, 1):
                this_color = model.new_bool_var(f"color_is_{saber}_{index}")
                model.add(color[index] == saber).only_enforce_if(this_color)
                model.add(color[index] != saber).only_enforce_if(this_color.Not())
                this_hit = model.new_bool_var(f"hit_{saber}_{index}")
                model.add_bool_and([placed[index], this_color]).only_enforce_if(this_hit)
                model.add_bool_or([placed[index].Not(), this_color.Not()]).only_enforce_if(this_hit.Not())
                nxt = model.new_bool_var(f"seen_{saber}_before_{index + 1}")
                model.add_bool_or([seen_before[saber], this_hit]).only_enforce_if(nxt)
                model.add_bool_and([seen_before[saber].Not(), this_hit.Not()]).only_enforce_if(nxt.Not())
                updated[saber] = nxt
            seen_before = updated
    if incoming_heads_by_beat:
        for index, domain in enumerate(domains):
            priors = list(incoming_heads_by_beat.get(round(float(intents[index]["beat"]), 6), []))
            if not priors:
                continue
            for pose_index, pose in enumerate(domain):
                head = pose["head"]
                blocked = False
                for prior in priors:
                    if int(prior["c"]) == int(head["c"]):
                        blocked = True
                        break
                    red = prior if int(prior["c"]) == 0 else head
                    blue = head if int(prior["c"]) == 0 else prior
                    if not double_is_safe(red, blue):
                        blocked = True
                        break
                if blocked:
                    model.add(choice[index] != pose_index)
    if fixed_poses:
        for local_index, frozen in fixed_poses.items():
            if frozen is None or frozen.get("kind") not in {"arc", "chain"}:
                continue
            until = _hold_occupied_until(intents[local_index], config.recovery_beats)
            hold_color = int(frozen["head"]["c"])
            for later, intent in enumerate(intents):
                if later == local_index or float(intent["beat"]) >= until:
                    continue
                model.add(color[later] != hold_color).only_enforce_if(placed[later])

    def hold_covering(note_index: int) -> int | None:
        beat = float(intents[note_index]["beat"])
        for hold_index, hold in enumerate(intents):
            if hold_index == note_index or hold["kind"] not in {"arc", "chain"}:
                continue
            start = float(hold["beat"])
            until = _hold_occupied_until(hold, config.recovery_beats)
            if start < beat < until - 1e-9:
                return hold_index
        return None

    for index in range(1, len(intents)):
        gap = float(intents[index]["beat"]) - float(intents[index - 1]["beat"])
        if gap <= max(config.min_gap, config.recovery_beats) or (
            1e-9 < gap <= HAND_ALTERNATE_BEATS
        ):
            left_hold = hold_covering(index - 1)
            right_hold = hold_covering(index)
            if left_hold is not None and left_hold == right_hold:
                model.add(color[index] != color[index - 1]).only_enforce_if(
                    [placed[index], placed[index - 1], hold_flags[left_hold].Not()]
                )
                continue
            model.add(color[index] != color[index - 1]).only_enforce_if([placed[index], placed[index - 1]])
    window = 6
    for index in range(len(intents)):
        for later in range(index + 1, min(len(intents), index + 1 + window)):
            same_beat = abs(float(intents[index]["beat"]) - float(intents[later]["beat"])) <= 1e-9
            kinematic_forbidden: list[tuple[int, int]] = []
            double_forbidden: list[tuple[int, int]] = []
            same_color_same_beat: list[tuple[int, int]] = []
            for left_i, left in enumerate(domains[index]):
                for right_i, right in enumerate(domains[later]):
                    left_color = int(left["head"]["c"])
                    right_color = int(right["head"]["c"])
                    if same_beat and left_color == right_color:
                        same_color_same_beat.append((left_i, right_i))
                        continue
                    if same_beat and left_color != right_color:
                        red_head = left["head"] if left_color == 0 else right["head"]
                        blue_head = right["head"] if left_color == 0 else left["head"]
                        if not double_is_safe(red_head, blue_head):
                            double_forbidden.append((left_i, right_i))
                        continue
                    if left_color == right_color and _same_hand_transition_is_illegal(
                        left["exit"],
                        right["head"],
                        bpm,
                        recovery_beats=config.recovery_beats,
                    ):
                        kinematic_forbidden.append((left_i, right_i))
            if same_color_same_beat:
                model.add_forbidden_assignments([choice[index], choice[later]], same_color_same_beat)
            if double_forbidden:
                model.add_forbidden_assignments([choice[index], choice[later]], double_forbidden)
            if not kinematic_forbidden:
                continue
            equal = model.new_bool_var(f"same_color_{index}_{later}")
            model.add(color[index] == color[later]).only_enforce_if(equal)
            model.add(color[index] != color[later]).only_enforce_if(equal.Not())
            consecutive = model.new_bool_var(f"consec_{index}_{later}")
            between_ok = []
            for mid in range(index + 1, later):
                skipped_or_opp = model.new_bool_var(f"clear_{index}_{mid}_{later}")
                opp = model.new_bool_var(f"opp_{index}_{mid}_{later}")
                model.add(color[mid] != color[index]).only_enforce_if(opp)
                model.add(color[mid] == color[index]).only_enforce_if(opp.Not())
                model.add_bool_or([placed[mid].Not(), opp]).only_enforce_if(skipped_or_opp)
                model.add_bool_and([placed[mid], opp.Not()]).only_enforce_if(skipped_or_opp.Not())
                between_ok.append(skipped_or_opp)
            model.add_bool_and([placed[index], placed[later], equal, *between_ok]).only_enforce_if(consecutive)
            blockers = [placed[index].Not(), placed[later].Not(), equal.Not(), *[item.Not() for item in between_ok]]
            model.add_bool_or(blockers).only_enforce_if(consecutive.Not())
            consecutive_flag = model.new_int_var(0, 1, f"consec_flag_{index}_{later}")
            model.add(consecutive_flag == 1).only_enforce_if(consecutive)
            model.add(consecutive_flag == 0).only_enforce_if(consecutive.Not())
            model.add_forbidden_assignments(
                [choice[index], choice[later], consecutive_flag],
                [(left_i, right_i, 1) for left_i, right_i in kinematic_forbidden],
            )
    imbalance = model.new_int_var(0, len(intents), "joint_imbalance")
    model.add_abs_equality(imbalance, 2 * sum(color) - len(intents))
    bombs_in = bomb_candidates if bomb_candidates is not None else (
        enumerate_bomb_candidates(intents, difficulty) if config.bombs else []
    )
    walls_in = wall_candidates if wall_candidates is not None else (
        enumerate_wall_candidates(intents, difficulty) if config.walls else []
    )
    bomb_vars = [model.new_bool_var(f"joint_bomb_{index}") for index in range(len(bombs_in))]
    wall_vars = [model.new_bool_var(f"joint_wall_{index}") for index in range(len(walls_in))]
    cap = {"Normal": 4, "Hard": 6, "Expert": 8, "ExpertPlus": 10}.get(difficulty, 0)
    if bomb_vars:
        model.add(sum(bomb_vars) <= cap)
    if wall_vars:
        model.add(sum(wall_vars) <= 6)
    for bomb_i, bomb in enumerate(bombs_in):
        for index, domain in enumerate(domains):
            forbidden = [(pose_i, 1) for pose_i, pose in enumerate(domain) if _pose_blocks_bomb(pose, bomb)]
            if forbidden:
                bomb_flag = model.new_int_var(0, 1, f"bomb_flag_{bomb_i}_{index}")
                model.add(bomb_flag == bomb_vars[bomb_i])
                model.add_forbidden_assignments([choice[index], bomb_flag], forbidden)
    for wall_i, wall in enumerate(walls_in):
        if _wall_forces_body_damage(wall):
            model.add(wall_vars[wall_i] == 0)
            continue
        for index, domain in enumerate(domains):
            forbidden = [(pose_i, 1) for pose_i, pose in enumerate(domain) if _pose_blocks_wall(pose, wall)]
            if forbidden:
                wall_flag = model.new_int_var(0, 1, f"wall_flag_{wall_i}_{index}")
                model.add(wall_flag == wall_vars[wall_i])
                model.add_forbidden_assignments([choice[index], wall_flag], forbidden)
    for left_i, left in enumerate(bombs_in):
        for right_i, right in enumerate(bombs_in[left_i + 1 :], start=left_i + 1):
            if (
                round(float(left["b"]), 6) == round(float(right["b"]), 6)
                and int(left["x"]) == int(right["x"])
                and int(left["y"]) == int(right["y"])
            ):
                model.add(bomb_vars[left_i] + bomb_vars[right_i] <= 1)
    for bomb_i, bomb in enumerate(bombs_in):
        for wall_i, wall in enumerate(walls_in):
            if _bomb_occupies_wall(bomb, wall):
                model.add(bomb_vars[bomb_i] + wall_vars[wall_i] <= 1)
    cut = []
    for index, domain in enumerate(domains):
        cut_var = model.new_int_var(0, 8, f"cut_{index}")
        cut.append(cut_var)
        model.add_allowed_assignments(
            [choice[index], cut_var],
            [(pose_index, int(pose["head"]["d"])) for pose_index, pose in enumerate(domain)]
            + [(skip_index[index], 8)],
        )
    cut_distance_table = [
        (left_d, right_d, circular_direction_distance(left_d, right_d))
        for left_d in range(9)
        for right_d in range(9)
    ]
    stutter_flags = []
    return_flags = []
    alt_combo_flags = []
    run3_flags = []
    aligned_even = []
    aligned_odd = []
    window_even = []
    window_odd = []
    for index in range(1, len(intents)):
        gap = float(intents[index]["beat"]) - float(intents[index - 1]["beat"])
        same_color = model.new_bool_var(f"same_color_{index}")
        model.add(color[index] == color[index - 1]).only_enforce_if(same_color)
        model.add(color[index] != color[index - 1]).only_enforce_if(same_color.Not())
        same_cut = model.new_bool_var(f"same_cut_{index}")
        model.add(cut[index] == cut[index - 1]).only_enforce_if(same_cut)
        model.add(cut[index] != cut[index - 1]).only_enforce_if(same_cut.Not())
        if gap > 1e-9:
            stutter = model.new_bool_var(f"stutter_{index}")
            stutter_flags.append(stutter)
            model.add_bool_and([placed[index], placed[index - 1], same_color, same_cut]).only_enforce_if(stutter)
            model.add_bool_or(
                [placed[index].Not(), placed[index - 1].Not(), same_color.Not(), same_cut.Not(), stutter]
            )
            alt_combo = model.new_bool_var(f"alt_combo_{index}")
            alt_combo_flags.append(alt_combo)
            model.add_bool_and(
                [placed[index], placed[index - 1], same_color.Not(), same_cut]
            ).only_enforce_if(alt_combo)
            model.add_bool_or(
                [
                    placed[index].Not(),
                    placed[index - 1].Not(),
                    same_color,
                    same_cut.Not(),
                    alt_combo,
                ]
            )
            if index >= 2:
                prev_same = model.new_bool_var(f"prev_same_cut_{index}")
                model.add(cut[index - 1] == cut[index - 2]).only_enforce_if(prev_same)
                model.add(cut[index - 1] != cut[index - 2]).only_enforce_if(prev_same.Not())
                run3 = model.new_bool_var(f"run3_{index}")
                run3_flags.append(run3)
                model.add_bool_and(
                    [placed[index], placed[index - 1], placed[index - 2], same_cut, prev_same]
                ).only_enforce_if(run3)
                model.add_bool_or(
                    [
                        placed[index].Not(),
                        placed[index - 1].Not(),
                        placed[index - 2].Not(),
                        same_cut.Not(),
                        prev_same.Not(),
                        run3,
                    ]
                )
            if gap <= 1.0 + 1e-9:
                dist = model.new_int_var(0, 8, f"seq_cut_dist_{index}")
                model.add_allowed_assignments([cut[index - 1], cut[index], dist], cut_distance_table)
                opposite = model.new_bool_var(f"seq_opp_{index}")
                model.add(dist == 4).only_enforce_if(opposite)
                model.add(dist != 4).only_enforce_if(opposite.Not())
                saber_return = model.new_bool_var(f"return_{index}")
                return_flags.append(saber_return)
                model.add_bool_and(
                    [placed[index], placed[index - 1], same_color, opposite]
                ).only_enforce_if(saber_return)
                model.add_bool_or(
                    [
                        placed[index].Not(),
                        placed[index - 1].Not(),
                        same_color.Not(),
                        opposite.Not(),
                        saber_return,
                    ]
                )
            continue
        dist = model.new_int_var(0, 8, f"cut_dist_{index}")
        model.add_allowed_assignments([cut[index - 1], cut[index], dist], cut_distance_table)
        opposite = model.new_bool_var(f"opp_cut_{index}")
        model.add(dist == 4).only_enforce_if(opposite)
        model.add(dist != 4).only_enforce_if(opposite.Not())
        aligned = model.new_bool_var(f"aligned_{index}")
        window = model.new_bool_var(f"window_{index}")
        model.add_bool_and(
            [placed[index], placed[index - 1], same_color.Not(), same_cut]
        ).only_enforce_if(aligned)
        model.add_bool_or(
            [placed[index].Not(), placed[index - 1].Not(), same_color, same_cut.Not(), aligned]
        )
        model.add_bool_and(
            [placed[index], placed[index - 1], same_color.Not(), opposite]
        ).only_enforce_if(window)
        model.add_bool_or(
            [placed[index].Not(), placed[index - 1].Not(), same_color, opposite.Not(), window]
        )
        if int(float(intents[index]["beat"]) // 8.0) % 2:
            aligned_odd.append(aligned)
            window_odd.append(window)
        else:
            aligned_even.append(aligned)
            window_even.append(window)
    model.maximize(
        sum(placed) * 100
        - imbalance
        + sum(hold_flags) * 150
        + sum(bomb_vars) * 2
        + sum(wall_vars)
        - sum(stutter_flags) * 6
        + sum(return_flags) * 8
        + sum(alt_combo_flags) * 6
        - sum(run3_flags) * 12
        + sum(aligned_even) * 6
        + sum(window_even) * 6
        + sum(aligned_odd) * 6
        + sum(window_odd) * 6
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 4
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    hands = [int(solver.value(variable)) for variable in color]
    poses: list[dict[str, Any] | None] = []
    for index, domain in enumerate(domains):
        selected = int(solver.value(choice[index]))
        poses.append(None if selected >= skip_index[index] else domain[selected])
    bombs = [item for item, variable in zip(bombs_in, bomb_vars) if solver.value(variable)]
    walls = [item for item, variable in zip(walls_in, wall_vars) if solver.value(variable)]
    return hands, poses, "joint-cp-sat", bombs, walls


JOINT_WINDOW = 64
JOINT_OVERLAP = 10


def _prefix_heads_by_beat(poses: list[dict[str, Any] | None], start: int) -> dict[float, list[dict[str, Any]]]:
    by_beat: dict[float, list[dict[str, Any]]] = {}
    for pose in poses[:start]:
        if pose is None:
            continue
        by_beat.setdefault(round(float(pose["head"]["b"]), 6), []).append(pose["head"])
        tail = pose.get("tail")
        if tail is not None:
            by_beat.setdefault(round(float(tail["b"]), 6), []).append(tail)
    return by_beat


def _prefix_exits(poses: list[dict[str, Any] | None], start: int) -> dict[int, dict[str, Any]]:
    exits: dict[int, dict[str, Any]] = {}
    for pose in poses[:start]:
        if pose is None:
            continue
        exits[int(pose["head"]["c"])] = pose["exit"]
    return exits


def _repair_joint_flow(
    poses: list[dict[str, Any] | None],
    bpm: float,
    recovery_beats: float,
) -> list[dict[str, Any] | None]:
    repaired = list(poses)
    last_exit: dict[int, dict[str, Any] | None] = {0: None, 1: None}
    for index, pose in enumerate(repaired):
        if pose is None:
            continue
        color = int(pose["head"]["c"])
        previous = last_exit[color]
        if previous is not None and _same_hand_transition_is_illegal(
            previous,
            pose["head"],
            bpm,
            recovery_beats=recovery_beats,
        ):
            repaired[index] = None
            continue
        last_exit[color] = pose["exit"]
    by_beat: dict[float, list[int]] = {}
    for index, pose in enumerate(repaired):
        if pose is None:
            continue
        by_beat.setdefault(round(float(pose["head"]["b"]), 6), []).append(index)
    for indices in by_beat.values():
        red = [index for index in indices if repaired[index] is not None and int(repaired[index]["head"]["c"]) == 0]
        blue = [index for index in indices if repaired[index] is not None and int(repaired[index]["head"]["c"]) == 1]
        for extra in red[1:]:
            repaired[extra] = None
        for extra in blue[1:]:
            repaired[extra] = None
        red = [index for index in indices if repaired[index] is not None and int(repaired[index]["head"]["c"]) == 0]
        blue = [index for index in indices if repaired[index] is not None and int(repaired[index]["head"]["c"]) == 1]
        if len(red) == 1 and len(blue) == 1 and not double_is_safe(
            repaired[red[0]]["head"],
            repaired[blue[0]]["head"],
        ):
            repaired[blue[0]] = None
    return repaired


def _solve_joint_window(
    chunk: list[dict[str, Any]],
    config: DifficultyConfig,
    bpm: float,
    *,
    difficulty: str,
    fixed_colors: dict[int, int] | None,
    frozen_poses: dict[int, dict[str, Any] | None] | None,
    incoming_occupancy: list[tuple[int, float]] | None,
    incoming_exits: dict[int, dict[str, Any]] | None,
    incoming_heads_by_beat: dict[float, list[dict[str, Any]]] | None,
    time_limit: float,
    drop_hazards: bool = False,
) -> tuple[list[int], list[dict[str, Any] | None], str, list[dict[str, Any]], list[dict[str, Any]]] | None:
    kwargs: dict[str, Any] = {
        "fixed_colors": fixed_colors,
        "fixed_poses": frozen_poses,
        "incoming_occupancy": incoming_occupancy,
        "incoming_exits": incoming_exits,
        "incoming_heads_by_beat": incoming_heads_by_beat,
        "time_limit": time_limit,
        "difficulty": difficulty,
    }
    if drop_hazards:
        kwargs["bomb_candidates"] = []
        kwargs["wall_candidates"] = []
    return _solve_joint_model(chunk, config, bpm, **kwargs)


def _joint_flow_is_legal(
    hands: list[int],
    poses: list[dict[str, Any] | None],
    bpm: float,
    recovery_beats: float,
) -> bool:
    last_exit: dict[int, dict[str, Any] | None] = {0: None, 1: None}
    by_beat: dict[float, list[dict[str, Any]]] = {}
    ordered = sorted(
        (
            (int(pose["head"]["c"]), pose)
            for pose in poses
            if pose is not None
        ),
        key=lambda item: (float(item[1]["head"]["b"]), item[0]),
    )
    for color, pose in ordered:
        previous = last_exit[color]
        if previous is not None and _same_hand_transition_is_illegal(previous, pose["head"], bpm, recovery_beats=recovery_beats):
            return False
        last_exit[color] = pose["exit"]
        head = pose["head"]
        by_beat.setdefault(round(float(head["b"]), 6), []).append(head)
    for beat_notes in by_beat.values():
        red = [note for note in beat_notes if int(note["c"]) == 0]
        blue = [note for note in beat_notes if int(note["c"]) == 1]
        if len(red) > 1 or len(blue) > 1:
            return False
        if len(red) == 1 and len(blue) == 1 and not double_is_safe(red[0], blue[0]):
            return False
    return True


def _solve_joint_assignment(
    intents: list[dict[str, Any]],
    config: DifficultyConfig,
    bpm: float,
    difficulty: str = "ExpertPlus",
) -> tuple[list[int], list[dict[str, Any] | None], str, list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Joint SAT over the full sequence, using overlapping windows when the map is long."""

    if not intents:
        return [], [], "joint-cp-sat", [], []
    if len(intents) <= JOINT_WINDOW:
        emit_progress("choreography", f"{difficulty} joint SAT on all {len(intents)} events")
        result = _solve_joint_model(intents, config, bpm, difficulty=difficulty)
        if result is None:
            return None
        hands, poses, label, bombs, walls = result
        if not _joint_flow_is_legal(hands, poses, bpm, config.recovery_beats):
            poses = _repair_joint_flow(poses, bpm, config.recovery_beats)
            if not _joint_flow_is_legal(hands, poses, bpm, config.recovery_beats):
                return None
        return hands, poses, label, bombs, walls
    count = len(intents)
    hands = [0] * count
    poses: list[dict[str, Any] | None] = [None] * count
    bombs: list[dict[str, Any]] = []
    walls: list[dict[str, Any]] = []
    start = 0
    incoming: list[tuple[int, float]] = []
    while start < count:
        end = min(count, start + JOINT_WINDOW)
        emit_progress(
            "choreography",
            f"{difficulty} joint SAT window beats {intents[start]['beat']:.2f}–{intents[end - 1]['beat']:.2f} ({end}/{count} events)",
        )
        chunk = intents[start:end]
        fixed = {index - start: hands[index] for index in range(start, end) if start > 0 and index < start + JOINT_OVERLAP}
        frozen_poses = {
            index - start: poses[index]
            for index in range(start, end)
            if start > 0 and index < start + JOINT_OVERLAP
        }
        exits = _prefix_exits(poses, start) or None
        prefix_heads = _prefix_heads_by_beat(poses, start) or None
        result = None
        for frozen, colors, use_exits, limit, drop_hazards in (
            (frozen_poses or None, fixed or None, None, 2.5, False),
            (frozen_poses or None, fixed or None, None, 8.0, False),
            (frozen_poses or None, fixed or None, None, 8.0, True),
            (None, fixed or None, exits, 8.0, False),
            (None, None, exits, 8.0, False),
            (None, None, exits, 8.0, True),
        ):
            result = _solve_joint_window(
                chunk,
                config,
                bpm,
                difficulty=difficulty,
                frozen_poses=frozen,
                fixed_colors=colors,
                incoming_occupancy=incoming or None,
                incoming_exits=use_exits,
                incoming_heads_by_beat=prefix_heads,
                time_limit=limit,
                drop_hazards=drop_hazards,
            )
            if result is not None:
                break
        if result is None:
            return None
        local_hands, local_poses, _label, local_bombs, local_walls = result
        for offset, (hand, pose) in enumerate(zip(local_hands, local_poses)):
            hands[start + offset] = hand
            poses[start + offset] = pose
        keep_until = float(intents[end - JOINT_OVERLAP]["beat"]) if end < count else math.inf
        beat_min = float(intents[start]["beat"]) if start else -math.inf
        bombs.extend(item for item in local_bombs if beat_min - 1e-9 <= float(item["b"]) < keep_until - 1e-9)
        walls.extend(item for item in local_walls if beat_min - 1e-9 <= float(item["b"]) < keep_until - 1e-9)
        if end >= count:
            break
        next_start = end - JOINT_OVERLAP
        next_beat = float(intents[next_start]["beat"])
        incoming = []
        for index in range(start, next_start):
            intent = intents[index]
            if poses[index] is None or poses[index].get("kind") not in {"arc", "chain"}:
                continue
            until = _hold_occupied_until(intent, config.recovery_beats)
            if until > next_beat:
                incoming.append((int(poses[index]["head"]["c"]), until))
        start = next_start
    if not _joint_flow_is_legal(hands, poses, bpm, config.recovery_beats):
        poses = _repair_joint_flow(poses, bpm, config.recovery_beats)
        if not _joint_flow_is_legal(hands, poses, bpm, config.recovery_beats):
            return None
    return hands, poses, "joint-cp-sat", bombs, walls


def _ensure_opposite_notes_at_hold_tails(
    notes: list[dict[str, Any]],
    arcs: list[dict[str, Any]],
    chains: list[dict[str, Any]],
    bpm: float,
    recovery_beats: float,
) -> None:
    """Place an opposite-hand note *during* a long hold so the tail stays the same saber.

    Skip the placement when no candidate clears same-hand flow. Never put the other
    color on the tail beat: that makes the arc look like it hands off to the other saber.
    """

    occupied = {
        (round(float(note.get("b", 0)), 6), int(note.get("x", -1)), int(note.get("y", -1)))
        for note in notes
    }
    for item in list(arcs) + list(chains):
        head = float(item["b"])
        tail = float(item["tb"])
        span = tail - head
        if span < 1.0 - 1e-9:
            continue
        mid = round(head + span * 0.5, 6)
        color = int(item["c"])
        other = 1 - color
        if any(head + 1e-6 < float(note.get("b", 0)) < tail - 1e-6 and note.get("c") == other for note in notes):
            continue
        hx, hy = int(item.get("x", 0)), int(item.get("y", 1))
        tx, ty = int(item.get("tx", 3 if color == 1 else 0)), int(item.get("ty", 1))
        previous = [
            note
            for note in notes
            if note.get("c") == other and float(note.get("b", 0)) < mid - 1e-6 and not note.get("_virtualTail")
        ]
        following = [
            note
            for note in notes
            if note.get("c") == other and float(note.get("b", 0)) > mid + 1e-6 and not note.get("_virtualTail")
        ]
        last = max(previous, key=lambda note: float(note["b"])) if previous else None
        nxt = min(following, key=lambda note: float(note["b"])) if following else None

        def reachable(x: int, y: int) -> bool:
            if last is not None and same_hand_after_hold_too_soon(mid - float(last["b"])):
                if math.hypot(x - int(last["x"]), y - int(last["y"])) > 2.5:
                    return False
            if nxt is not None and same_hand_after_hold_too_soon(float(nxt["b"]) - mid):
                if math.hypot(x - int(nxt["x"]), y - int(nxt["y"])) > 2.5:
                    return False
            return True

        raw: list[tuple[int, int]] = []
        if nxt is not None:
            raw.append((int(nxt["x"]), int(nxt["y"])))
        if last is not None:
            raw.append((int(last["x"]), int(last["y"])))
        raw.extend(
            (
                (0 if other == 0 else 3, 1),
                (1 if other == 0 else 2, 1),
                (0 if other == 0 else 3, 0),
            )
        )
        candidates = []
        for x, y in raw:
            if (x, y) in {(hx, hy), (tx, ty)}:
                x = min(3, max(0, x + (-1 if other == 0 else 1)))
            if reachable(x, y) and (x, y) not in candidates:
                candidates.append((x, y))
        placed = False
        for x, y in candidates:
            if (x, y) in {(hx, hy), (tx, ty)}:
                continue
            key = (mid, x, y)
            if key in occupied:
                continue
            for direction in (1, 0, 2, 3, 4, 5, 6, 7, 8):
                candidate = {"b": mid, "x": x, "y": y, "c": other, "d": direction, "a": 0}
                if last is not None and transition_finding(last, candidate, bpm, recovery_beats=recovery_beats):
                    continue
                if nxt is not None and transition_finding(candidate, nxt, bpm, recovery_beats=recovery_beats):
                    continue
                notes.append(candidate)
                occupied.add(key)
                placed = True
                break
            if placed:
                break


def realize(
    intents: list[dict[str, Any]],
    hands: list[int],
    difficulty: str,
    seed: int,
    bpm: float = 120.0,
    poses: list[dict[str, Any] | None] | None = None,
    pose_solver: str | None = None,
    joint_bombs: list[dict[str, Any]] | None = None,
    joint_walls: list[dict[str, Any]] | None = None,
    duration_seconds: float | None = None,
    lighting_accents: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = CONFIGS[difficulty]
    rng = random.Random(seed + difficulty_rank(difficulty) * 1009)
    states = {
        0: HandState(0, 0, 1, 1, 0),
        1: HandState(1, 3, 1, 1, 0),
    }
    notes: list[dict[str, Any]] = []
    arcs: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    reservations: list[dict[str, Any]] = []
    forbidden_post_hold_chords: dict[float, set[int]] = {}
    last_notes: dict[int, dict[str, Any] | None] = {0: None, 1: None}
    density_relaxations: list[dict[str, Any]] = []
    if poses is None or pose_solver is None:
        poses, pose_solver = _solve_poses(intents, hands, config, bpm)
    for hold_index, hold in enumerate(intents):
        if hold["kind"] not in {"arc", "chain"}:
            continue
        tail = _hold_tail_beat(hold)
        next_index = next(
            (later for later in range(hold_index + 1, len(intents)) if float(intents[later]["beat"]) > tail + 1e-9),
            None,
        )
        if next_index is not None:
            forbidden_post_hold_chords.setdefault(float(intents[next_index]["beat"]), set()).add(hands[hold_index])
    for index, (intent, color) in enumerate(zip(intents, hands)):
        beat = float(intent["beat"])
        pose = poses[index] if index < len(poses) else None
        if pose is None:
            density_relaxations.append(
                {
                    "beat": beat,
                    "color": color,
                    "reason": "no_joint_lane_row_direction_candidate_satisfied_same_hand_flow",
                }
            )
            continue
        state = states[color]
        if beat < state.occupied_until - 1e-9:
            raise RuntimeError(f"hand {color} is occupied at beat {beat}")
        head = pose["head"]
        x, y, direction = int(head["x"]), int(head["y"]), int(head["d"])
        notes.append(head)
        dx, dy = direction_vector(direction)
        state.x, state.y, state.direction = x, y, direction
        momentum = pose.get("momentum")
        if momentum:
            state.momentum_x, state.momentum_y = float(momentum[0]), float(momentum[1])
        else:
            state.momentum_x, state.momentum_y = float(dx), float(dy)
        state.body_lean = float(pose.get("bodyLean", (x - 1.5) * (1 if color == 0 else -1) * 0.15))
        state.parity = 1 - state.parity
        last_notes[color] = head
        kind = str(pose.get("kind") or intent["kind"])
        if kind == "note" and intent["kind"] in {"arc", "chain"}:
            density_relaxations.append(
                {
                    "beat": beat,
                    "color": color,
                    "reason": "hold_downgraded_to_note",
                    "downgradedFrom": intent["kind"],
                }
            )
        if kind == "arc":
            tail = pose.get("tail") or _default_tail(head, color, kind, float(intent["duration"]), index)
            tail_beat = float(tail["b"])
            tail_x, tail_y = int(tail["x"]), int(tail["y"])
            # The tail note cut direction must be a valid return cut for head direction
            head_dir = int(head["d"])
            if head_dir in (0, 4, 5):
                tail_direction = 1
            elif head_dir in (1, 6, 7):
                tail_direction = 0
            elif head_dir in (2, 3):
                tail_direction = 3 if head_dir == 2 else 2
            else:
                tail_direction = int(tail.get("d", 1 if tail_y == 2 else 0))
            control = min(2.5, max(0.7, (tail_beat - beat) * 0.4))
            arcs.append({"c": color, "b": beat, "x": x, "y": y, "d": direction, "mu": control, "tb": tail_beat, "tx": tail_x, "ty": tail_y, "tc": tail_direction, "tmu": control, "m": 0})
            tail_note = {"b": tail_beat, "x": tail_x, "y": tail_y, "c": color, "d": tail_direction, "a": 0}
            notes.append(tail_note)
            last_notes[color] = tail_note
            companion = pose.get("companion")
            if companion is not None:
                notes.append(dict(companion))
            state.x, state.y, state.direction = tail_x, tail_y, tail_direction
            tdx, tdy = direction_vector(tail_direction)
            state.momentum_x, state.momentum_y = float(tdx), float(tdy)
            state.exit_x, state.exit_y, state.exit_direction = tail_x, tail_y, tail_direction
            state.parity = 1 - state.parity
            state.occupied_until = occupied_until(float(tail["b"]), config.recovery_beats)
            state.recovery_until = state.occupied_until
            reservations.append({"color": color, "headBeat": beat, "tailBeat": tail_beat, "recoveryUntil": state.recovery_until})
        elif kind == "chain":
            tail = pose.get("tail") or _default_tail(head, color, kind, float(intent["duration"]), index)
            tail_beat = float(tail["b"])
            tail_x, tail_y = int(tail["x"]), int(tail["y"])
            sc = int(tail.get("slices", 4 if difficulty == "ExpertPlus" else 3))
            squish = float(tail.get("squish", 1.0))
            chains.append({
                "c": color,
                "b": beat,
                "x": x,
                "y": y,
                "d": direction,
                "tb": tail_beat,
                "tx": tail_x,
                "ty": tail_y,
                "sc": sc,
                "s": squish,
            })
            state.x, state.y = tail_x, tail_y
            state.exit_x, state.exit_y, state.exit_direction = tail_x, tail_y, direction
            state.occupied_until = occupied_until(float(tail["b"]), config.recovery_beats)
            state.recovery_until = state.occupied_until
            last_notes[color] = {
                "b": tail_beat,
                "x": tail_x,
                "y": tail_y,
                "c": color,
                "d": direction,
                "_virtualTail": "chain",
            }
            companion = pose.get("companion")
            if companion is not None:
                notes.append(dict(companion))
            reservations.append({"color": color, "headBeat": beat, "tailBeat": tail_beat, "recoveryUntil": state.recovery_until})
        else:
            state.exit_x, state.exit_y, state.exit_direction = x, y, direction
            state.recovery_until = beat + config.recovery_beats

    _ensure_opposite_notes_at_hold_tails(notes, arcs, chains, bpm, config.recovery_beats)
    notes.sort(key=lambda item: (item["b"], item["c"], item["x"], item["y"]))
    if joint_bombs is not None and joint_walls is not None:
        bombs = [
            bomb
            for bomb in joint_bombs
            if not _hazard_cell_blocked(notes, float(bomb["b"]), int(bomb["x"]), int(bomb["y"]))
        ]
        walls = [
            wall
            for wall in joint_walls
            if not _wall_forces_body_damage(wall)
            and not _wall_blocked(notes, float(wall["b"]), float(wall["d"]), int(wall["x"]), int(wall["w"]))
        ]
    else:
        bombs, walls = _solve_hazards(notes, intents, difficulty, config)
    song_end = None
    if duration_seconds and bpm > 0:
        song_end = float(duration_seconds) * float(bpm) / 60.0
    lighting = build_lighting(intents, end_beat=song_end, accents=lighting_accents)
    beatmap = {
        "version": "3.3.0",
        "bpmEvents": [],
        "rotationEvents": [],
        "colorNotes": notes,
        "bombNotes": bombs,
        "obstacles": walls,
        "sliders": sorted(arcs, key=lambda item: item["b"]),
        "burstSliders": sorted(chains, key=lambda item: item["b"]),
        "waypoints": [],
        **lighting,
        "basicEventTypesWithKeywords": {},
        "useNormalEventsAsCompatibleEvents": True,
    }
    trace = {
        "difficulty": difficulty,
        "reservations": reservations,
        "finalHandState": {str(color): asdict(state) for color, state in states.items()},
        "counts": {"notes": len(notes), "bombs": len(bombs), "walls": len(walls), "arcs": len(arcs), "chains": len(chains)},
        "densityRelaxations": density_relaxations,
        "poseSolver": pose_solver,
        "hazardSolver": "joint-cp-sat" if joint_bombs is not None else "post-note-cp-sat",
    }
    return beatmap, trace


def _hazard_cell_blocked(notes: list[dict[str, Any]], beat: float, x: int, y: int) -> bool:
    bomb = {"b": beat, "x": x, "y": y}
    return any(bomb_on_swing_path(bomb, note) for note in notes)


def _wall_blocked(notes: list[dict[str, Any]], start: float, duration: float, x: int, width: int) -> bool:
    end = start + duration
    columns = set(range(x, min(4, x + width)))
    for note in notes:
        beat = float(note["b"])
        if beat < start - 1e-9 or beat > end + 1e-9:
            continue
        if int(note["x"]) in columns:
            return True
        pose = swing_pose(note)
        if round(pose.contact_x) in columns or round(pose.exit_x) in columns:
            return True
    return False


def _wall_forces_body_damage(wall: dict[str, Any]) -> bool:
    return int(wall.get("x", 0)) <= 0 and int(wall.get("w", 0)) >= 4 and int(wall.get("y", 0)) <= 0 and int(wall.get("h", 0)) >= 5


def _bomb_occupies_wall(bomb: dict[str, Any], wall: dict[str, Any]) -> bool:
    beat = float(bomb["b"])
    start = float(wall["b"])
    end = start + float(wall["d"])
    if beat < start - 1e-9 or beat > end + 1e-9:
        return False
    columns = set(range(int(wall["x"]), min(4, int(wall["x"]) + int(wall["w"]))))
    return int(bomb["x"]) in columns


def _solve_hazards(
    notes: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    difficulty: str,
    config: DifficultyConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bomb_candidates = build_bomb_candidates(notes, intents, difficulty) if config.bombs else []
    wall_candidates = build_wall_candidates(notes, intents, difficulty) if config.walls else []
    if not bomb_candidates and not wall_candidates:
        return [], []
    try:
        cp_model = _cp_model_module()
    except ImportError:
        walls = [wall for wall in wall_candidates if not _wall_forces_body_damage(wall)]
        return bomb_candidates, walls[:6]
    model = cp_model.CpModel()
    bomb_vars = [model.new_bool_var(f"bomb_{index}") for index in range(len(bomb_candidates))]
    wall_vars = [model.new_bool_var(f"wall_{index}") for index in range(len(wall_candidates))]
    cap = {"Normal": 4, "Hard": 6, "Expert": 8, "ExpertPlus": 10}.get(difficulty, 0)
    if bomb_vars:
        model.add(sum(bomb_vars) <= cap)
    if wall_vars:
        model.add(sum(wall_vars) <= 6)
    for index, bomb in enumerate(bomb_candidates):
        if _hazard_cell_blocked(notes, float(bomb["b"]), int(bomb["x"]), int(bomb["y"])):
            model.add(bomb_vars[index] == 0)
    for index, wall in enumerate(wall_candidates):
        if _wall_forces_body_damage(wall) or _wall_blocked(
            notes, float(wall["b"]), float(wall["d"]), int(wall["x"]), int(wall["w"])
        ):
            model.add(wall_vars[index] == 0)
    for left_i, left in enumerate(bomb_candidates):
        for right_i, right in enumerate(bomb_candidates[left_i + 1 :], start=left_i + 1):
            if (
                round(float(left["b"]), 6) == round(float(right["b"]), 6)
                and int(left["x"]) == int(right["x"])
                and int(left["y"]) == int(right["y"])
            ):
                model.add(bomb_vars[left_i] + bomb_vars[right_i] <= 1)
    for bomb_i, bomb in enumerate(bomb_candidates):
        for wall_i, wall in enumerate(wall_candidates):
            if _bomb_occupies_wall(bomb, wall):
                model.add(bomb_vars[bomb_i] + wall_vars[wall_i] <= 1)
    model.maximize(sum(bomb_vars) * 2 + sum(wall_vars))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 1.0
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        walls = [wall for wall in wall_candidates if not _wall_forces_body_damage(wall)]
        return bomb_candidates[:cap], walls[:6]
    bombs = [item for item, variable in zip(bomb_candidates, bomb_vars) if solver.value(variable)]
    walls = [item for item, variable in zip(wall_candidates, wall_vars) if solver.value(variable)]
    return bombs, walls


def enumerate_bomb_candidates(intents: list[dict[str, Any]], difficulty: str) -> list[dict[str, Any]]:
    """Intent-timeline bomb slots. Occupancy vs poses is a SAT constraint, not a pre-filter."""

    bombs: list[dict[str, Any]] = []
    beats = [float(intent["beat"]) for intent in intents]
    cap = {"Normal": 4, "Hard": 6, "Expert": 8, "ExpertPlus": 10}.get(difficulty, 0)
    for index, (left, right) in enumerate(zip(beats, beats[1:])):
        if right - left < 2.0 or index % 9:
            continue
        x = 0 if len(bombs) % 2 == 0 else 3
        bombs.append({"b": round(left + 1.0, 6), "x": x, "y": 2})
        if len(bombs) >= cap:
            break
    return bombs


def enumerate_wall_candidates(intents: list[dict[str, Any]], difficulty: str) -> list[dict[str, Any]]:
    """Section-transition wall slots. Body-damage and note occupancy are SAT constraints."""

    transitions: list[float] = []
    previous = None
    for intent in intents:
        section = intent.get("section")
        if previous is not None and section != previous:
            transitions.append(float(intent["beat"]))
        previous = section
    duration = 1.5 if difficulty in {"Easy", "Normal"} else 1.0
    walls: list[dict[str, Any]] = []
    for index, beat in enumerate(transitions[:8]):
        x = 0 if index % 2 == 0 else 2
        walls.append({"b": round(beat + 0.5, 6), "d": duration, "x": x, "y": 0, "w": 2, "h": 5})
        if len(walls) >= 6:
            break
    return walls


def build_bomb_candidates(notes: list[dict[str, Any]], intents: list[dict[str, Any]], difficulty: str) -> list[dict[str, Any]]:
    return [
        bomb
        for bomb in enumerate_bomb_candidates(intents, difficulty)
        if not _hazard_cell_blocked(notes, float(bomb["b"]), int(bomb["x"]), int(bomb["y"]))
    ]


def build_wall_candidates(notes: list[dict[str, Any]], intents: list[dict[str, Any]], difficulty: str) -> list[dict[str, Any]]:
    return [
        wall
        for wall in enumerate_wall_candidates(intents, difficulty)
        if not _wall_forces_body_damage(wall)
        and not _wall_blocked(notes, float(wall["b"]), float(wall["d"]), int(wall["x"]), int(wall["w"]))
    ]


def build_bombs(notes: list[dict[str, Any]], intents: list[dict[str, Any]], difficulty: str, rng: random.Random) -> list[dict[str, Any]]:
    del rng
    return build_bomb_candidates(notes, intents, difficulty)


def build_walls(notes: list[dict[str, Any]], intents: list[dict[str, Any]], difficulty: str) -> list[dict[str, Any]]:
    return build_wall_candidates(notes, intents, difficulty)


def _light_channel(layer: str, index: int) -> int:
    name = str(layer or "").lower()
    if name in {"drums", "other"}:
        return 2 if index % 2 == 0 else 3
    if name == "bass":
        return 1
    if name == "vocals":
        return 0
    return 4


def build_lighting(
    intents: list[dict[str, Any]],
    end_beat: float | None = None,
    accents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Place a continuous v3 light clock, then flash from onsets and stems.

    Official Electronic Expert+ sits near 13 light objects per second; OST 8 / Daft Punk
    sit near 20–25. Accents come from audio onsets (including hits that did not become
    notes). Never copy official event lists.
    """

    events: list[dict[str, Any]] = []
    boost: list[dict[str, Any]] = []
    last_event_beat: dict[int, float] = {0: -math.inf, 1: -math.inf, 2: -math.inf, 3: -math.inf, 4: -math.inf}

    def emit_event(beat: float, event_type: int, value: int, float_val: float, min_gap: float | None = None) -> None:
        gap = 0.08 if event_type in (2, 3, 4) else 0.16
        if min_gap is not None:
            gap = min_gap
        if beat - last_event_beat.get(event_type, -math.inf) < gap - 1e-6:
            return
        events.append(
            {
                "b": round(float(beat), 6),
                "et": int(event_type),
                "i": int(value),
                "f": round(min(1.0, max(0.0, float(float_val))), 4),
            }
        )
        last_event_beat[event_type] = beat

    if not intents and not end_beat and not accents:
        return {
            "basicBeatmapEvents": [],
            "colorBoostBeatmapEvents": [],
            "lightColorEventBoxGroups": [],
            "lightRotationEventBoxGroups": [],
            "lightTranslationEventBoxGroups": [],
            "vfxEventBoxGroups": [],
        }

    start = 0.0
    intent_end = max((float(item["beat"]) + float(item.get("duration") or 0.0) for item in intents), default=0.0)
    end = max(intent_end, float(end_beat or 0.0))
    section_at: list[tuple[float, str, float]] = [
        (float(item["beat"]), str(item.get("section", "body")), float(item.get("intensity", 0.5)))
        for item in intents
    ]

    def section_state(beat: float) -> tuple[str, float]:
        if not section_at or beat < section_at[0][0] - 1e-9:
            return "intro", 0.32
        current = section_at[0]
        for item in section_at:
            if item[0] <= beat + 1e-9:
                current = item
            else:
                break
        return current[1], current[2]

    last_section = None
    boost_state = False
    beat = math.floor(start * 4.0) / 4.0
    while beat <= end + 1e-9:
        section, intensity = section_state(beat)
        if last_section is not None and section != last_section:
            boost_state = not boost_state
            boost.append({"b": round(beat, 6), "o": boost_state})
            emit_event(beat, 1, 6 if boost_state else 2, 1.0, min_gap=0.0)
        last_section = section
        section_lower = section.lower()
        is_peak = intensity >= 0.7 or any(token in section_lower for token in ("peak", "chorus", "drop"))
        is_breakdown = intensity < 0.35 or any(token in section_lower for token in ("intro", "outro", "bridge", "breakdown"))
        downbeat = abs(beat % 4.0) <= 1e-6
        halfbeat = abs(beat % 2.0) <= 1e-6
        quarter = abs(beat % 0.5) <= 1e-6
        if not is_peak and not quarter:
            beat = round(beat + 0.25, 6)
            continue
        measure = int(beat // 4.0)
        primary_val = 5 if measure % 2 == 0 else 1
        flash_val = 6 if primary_val == 5 else 2
        fade_val = 7 if primary_val == 5 else 3
        if is_breakdown:
            if downbeat:
                emit_event(beat, 0, primary_val, 0.45)
                emit_event(beat, 1, fade_val, 0.35)
        elif is_peak:
            if downbeat:
                emit_event(beat, 0, primary_val, 0.95)
                emit_event(beat, 1, flash_val, 1.0)
                emit_event(beat, 2, flash_val, 0.85)
                emit_event(beat, 3, flash_val, 0.85)
                emit_event(beat, 4, flash_val, 0.95)
            elif halfbeat:
                emit_event(beat, 1, fade_val, 0.75)
                emit_event(beat, 2, primary_val, 0.55)
                emit_event(beat, 3, primary_val, 0.55)
                emit_event(beat, 4, primary_val, 0.6)
            elif quarter:
                emit_event(beat, 2 if int(round(beat * 4)) % 2 == 0 else 3, flash_val, 0.55)
                emit_event(beat, 4, fade_val, 0.45)
            else:
                emit_event(beat, 2 if int(round(beat * 8)) % 2 == 0 else 3, fade_val, 0.35)
        else:
            if downbeat:
                emit_event(beat, 0, primary_val, 0.65)
                emit_event(beat, 1, fade_val, 0.55)
                emit_event(beat, 4, primary_val, 0.4)
            elif halfbeat:
                emit_event(beat, 1, fade_val, 0.4)
                emit_event(beat, 4, primary_val, 0.4)
            elif quarter:
                emit_event(beat, 2 if int(round(beat * 4)) % 2 == 0 else 3, fade_val, 0.32)
        beat = round(beat + 0.25, 6)

    for index, intent in enumerate(intents):
        when = float(intent["beat"])
        kind = str(intent.get("kind", "note"))
        strength = float(intent.get("strength", 0.0))
        intensity = float(intent.get("intensity", 0.5))
        hold = kind in {"arc", "chain"}
        if hold:
            channel = _light_channel(str(intent.get("layer") or ""), index)
            emit_event(when, channel, 6 if int(when // 4) % 2 == 0 else 2, 1.0, min_gap=0.0)
            tail = when + float(intent.get("duration") or 0.0)
            emit_event(tail, channel, 3 if int(when // 4) % 2 == 0 else 7, 0.7, min_gap=0.0)
        elif strength >= 2.5 or intensity >= 0.75:
            emit_event(
                when,
                _light_channel(str(intent.get("layer") or ""), index),
                5 if int(when // 4) % 2 == 0 else 1,
                0.9,
            )

    for index, accent in enumerate(accents or []):
        when = float(accent.get("snappedBeat", accent.get("beat", 0.0)))
        section, intensity = section_state(when)
        section_lower = section.lower()
        is_breakdown = intensity < 0.35 or any(token in section_lower for token in ("intro", "outro", "bridge", "breakdown"))
        strength = float(accent.get("strength") or 0.0)
        sustain = float(accent.get("sustainBeats") or accent.get("duration") or 0.0)
        layer = str(accent.get("layer") or "")
        if is_breakdown and strength < 4.0:
            continue
        if strength < 2.2:
            continue
        channel = _light_channel(layer, index)
        brightness = min(1.0, 0.45 + strength / 8.0)
        emit_event(when, channel, 6 if strength >= 3.5 else 2, brightness)
        if sustain >= 0.75:
            emit_event(when + min(sustain, 2.0), channel, 3, max(0.25, brightness - 0.3), min_gap=0.0)
        energies = accent.get("stemEnergy") if isinstance(accent.get("stemEnergy"), dict) else {}
        drums = float(energies.get("drums") or 0.0)
        if drums >= 0.35 and not is_breakdown:
            emit_event(when, 4, 5, min(1.0, drums), min_gap=0.08)

    seen_keys: set[tuple[float, int]] = set()
    unique_events: list[dict[str, Any]] = []
    for ev in sorted(events, key=lambda item: (item["b"], item["et"])):
        key = (ev["b"], ev["et"])
        if key not in seen_keys:
            seen_keys.add(key)
            unique_events.append(ev)

    color_groups: list[dict[str, Any]] = []
    rotation_groups: list[dict[str, Any]] = []
    translation_groups: list[dict[str, Any]] = []
    clock = start
    while clock <= end + 1e-9:
        section, intensity = section_state(clock)
        section_lower = section.lower()
        is_peak = intensity >= 0.7 or any(token in section_lower for token in ("peak", "chorus", "drop"))
        is_breakdown = intensity < 0.35 or any(token in section_lower for token in ("intro", "outro", "bridge", "breakdown"))
        downbeat = abs(clock % 4.0) <= 1e-6
        halfbeat = abs(clock % 2.0) <= 1e-6
        if is_peak:
            step = 0.25
        elif is_breakdown:
            step = 1.0
        else:
            step = 0.5
        if downbeat or is_peak or (not is_breakdown and halfbeat):
            color_id = 1 if int(clock // 4.0) % 2 else 0
            brightness = 1.0 if intensity >= 0.75 else 0.6
            for group in (0, 1, 2, 3, 4):
                color_groups.append(
                    {
                        "b": round(clock, 6),
                        "g": group,
                        "e": [
                            {
                                "f": {"e": 1, "l": 0, "wf": 1, "w": 0, "xf": 1, "x": 0, "s": 0, "t": 1, "i": 0, "b": 1},
                                "e": [{"b": 0.0, "c": color_id, "s": brightness, "i": 1 if intensity >= 0.7 else 0, "sb": 0, "st": 0}],
                            }
                        ],
                    }
                )
            if downbeat or (is_peak and halfbeat):
                rotation_groups.append(
                    {
                        "b": round(clock, 6),
                        "g": 4,
                        "e": [
                            {
                                "f": {"e": 1, "l": 0, "wf": 1, "w": 0, "xf": 1, "x": 0, "s": 0, "t": 1, "i": 0, "b": 1},
                                "l": [{"b": 0.0, "p": 0, "e": 2, "l": 30.0 if intensity >= 0.7 else 12.0, "n": 0, "d": int(clock // 4) % 2, "s": 1.0, "t": 0}],
                            }
                        ],
                    }
                )
                translation_groups.append(
                    {
                        "b": round(clock, 6),
                        "g": 4,
                        "e": [
                            {
                                "f": {"e": 1, "l": 0, "wf": 1, "w": 0, "xf": 1, "x": 0, "s": 0, "t": 1, "i": 0, "b": 1},
                                "t": [{"b": 0.0, "p": 0, "e": 2, "t": 0.15 if intensity >= 0.7 else 0.04, "d": 0}],
                            }
                        ],
                    }
                )
        clock = round(clock + step, 6)

    for intent in intents:
        if str(intent.get("kind")) not in {"arc", "chain"}:
            continue
        beat = float(intent["beat"])
        color_groups.append(
            {
                "b": round(beat, 6),
                "g": 2,
                "e": [
                    {
                        "f": {"e": 1, "l": 0, "wf": 1, "w": 0, "xf": 1, "x": 0, "s": 0, "t": 1, "i": 0, "b": 1},
                        "e": [{"b": 0.0, "c": 1, "s": 1.0, "i": 1, "sb": 0, "st": 0}],
                    }
                ],
            }
        )

    return {
        "basicBeatmapEvents": unique_events,
        "colorBoostBeatmapEvents": sorted(boost, key=lambda item: item["b"]),
        "lightColorEventBoxGroups": color_groups[:4096],
        "lightRotationEventBoxGroups": rotation_groups[:2048],
        "lightTranslationEventBoxGroups": translation_groups[:1024],
        "vfxEventBoxGroups": [],
    }


def lighting_object_count(lighting: dict[str, Any]) -> int:
    """Count serialized v3 light objects. Official Electronic Expert+ averages ~2.5 per note."""

    return sum(
        len(lighting.get(key) or [])
        for key in (
            "basicBeatmapEvents",
            "colorBoostBeatmapEvents",
            "lightColorEventBoxGroups",
            "lightRotationEventBoxGroups",
            "lightTranslationEventBoxGroups",
        )
    )


def retrieve_official_references(
    database_path: Path | None,
    difficulty: str,
    bpm: float,
    characteristic: str = "Standard",
    limit: int = 8,
    target_features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = database_path or default_cache_dir() / "official-corpus.sqlite3"
    if not path.exists():
        return {"status": "unavailable", "database": str(path), "references": [], "profile": {}}
    database = sqlite3.connect(path)
    database.row_factory = sqlite3.Row
    rows = database.execute(
        """
        SELECT b.pack_id,b.id bundle_id,l.title,l.artist,l.bpm,l.format,l.era_weight,d.features_json
        FROM difficulties d JOIN levels l ON l.id=d.level_id JOIN bundles b ON b.id=l.bundle_id
        WHERE b.status='indexed' AND d.characteristic=? AND d.difficulty=? AND l.bpm IS NOT NULL
        """,
        (characteristic, difficulty),
    ).fetchall()
    def distance(row: sqlite3.Row) -> float:
        tempo = abs(float(row["bpm"]) - bpm) / max(1.0, bpm)
        style = 0.0
        if target_features:
            features = json.loads(row["features_json"])
            for name in ("nps", "medianSameHandGap", "p95Reach", "centerVisionRate"):
                target = float(target_features.get(name, 0.0))
                style += abs(float(features.get(name, 0.0)) - target) / max(0.25, abs(target))
            style /= 4.0
        recency = 1.0 - float(row["era_weight"])
        return tempo * 3.0 + style + recency * 0.5

    ranked = sorted(rows, key=distance)[:limit]
    references = [
        {
            "bundleId": row["bundle_id"],
            "pack": row["pack_id"],
            "title": row["title"],
            "artist": row["artist"],
            "bpm": row["bpm"],
            "format": row["format"],
            "eraWeight": row["era_weight"],
        }
        for row in ranked
    ]
    samples = [(float(row["era_weight"]), json.loads(row["features_json"])) for row in ranked]
    profile: dict[str, float] = {}
    if samples:
        total = sum(weight for weight, _ in samples)
        for name in ("nps", "medianSameHandGap", "p10SameHandGap", "p95Reach", "parityViolationRate", "rapidSameHandRate", "centerVisionRate"):
            profile[name] = sum(weight * float(features.get(name, 0.0)) for weight, features in samples) / total
    return {"status": "ready", "database": str(path), "references": references, "profile": profile}


def score_candidate(database_path: Path | None, features: dict[str, Any]) -> dict[str, Any]:
    path = database_path or default_cache_dir() / "official-corpus.sqlite3"
    if not path.exists():
        return {"status": "unavailable"}
    database = sqlite3.connect(path)
    row = database.execute("SELECT value FROM corpus_meta WHERE key='quality_ranker'").fetchone()
    if not row:
        return {"status": "unavailable"}
    ranker = json.loads(row[0])
    components: dict[str, float] = {}
    for name, parameters in ranker.get("features", {}).items():
        value = float(features.get(name, 0.0))
        components[name] = abs(value - float(parameters["mean"])) / max(1e-6, float(parameters["scale"]))
    distance = sum(components.values()) / max(1, len(components))
    return {
        "status": "ranked",
        "score": round(100.0 / (1.0 + distance), 6),
        "normalizedDistance": round(distance, 6),
        "components": components,
        "hardConstraintsOverride": False,
    }


def generate_all(
    analysis: dict[str, Any],
    sections: dict[str, Any],
    seed: int,
    corpus_database: Path | None = None,
    difficulties: Sequence[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    generated: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {"solver": {}, "difficulties": {}, "officialReferences": {}}
    chosen = tuple(name for name in DIFFICULTIES if name in set(difficulties or DIFFICULTIES)) or DIFFICULTIES
    for difficulty in chosen:
        emit_progress("choreography", f"selecting {difficulty} intents from onsets and sections")
        intents = select_intents(analysis, sections, difficulty)
        joint = None
        try:
            emit_progress("choreography", f"solving joint CP-SAT for {difficulty} ({len(intents)} events)")
            joint = _solve_joint_assignment(intents, CONFIGS[difficulty], float(analysis["bpm"]), difficulty=difficulty)
        except (RuntimeError, ValueError, AttributeError):
            joint = None
        if joint is not None:
            hands, poses, solver_label, joint_bombs, joint_walls = joint
            beatmap, trace = realize(
                intents,
                hands,
                difficulty,
                seed,
                float(analysis["bpm"]),
                poses=poses,
                pose_solver=solver_label,
                joint_bombs=joint_bombs,
                joint_walls=joint_walls,
                duration_seconds=float(analysis.get("durationSeconds") or 0.0) or None,
                lighting_accents=list(analysis.get("events") or []),
            )
            report["solver"][difficulty] = {"hands": solver_label, "poses": solver_label, "hazards": "joint-cp-sat"}
        else:
            hands, solver = solve_hands(intents, CONFIGS[difficulty])
            beatmap, trace = realize(
                intents,
                hands,
                difficulty,
                seed,
                float(analysis["bpm"]),
                duration_seconds=float(analysis.get("durationSeconds") or 0.0) or None,
                lighting_accents=list(analysis.get("events") or []),
            )
            report["solver"][difficulty] = {"hands": solver, "poses": trace.get("poseSolver")}
        normalized_events, _light_count = normalize_beatmap(beatmap)
        candidate_features = map_features(normalized_events, float(analysis["bpm"]))
        generated[difficulty] = beatmap
        report["difficulties"][difficulty] = trace
        report["officialReferences"][difficulty] = retrieve_official_references(
            corpus_database,
            difficulty,
            float(analysis["bpm"]),
            target_features=candidate_features,
        )
        report["difficulties"][difficulty]["corpusRank"] = score_candidate(corpus_database, candidate_features)
    return generated, report
