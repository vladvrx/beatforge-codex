"""RL checks in the same constant-BPM coordinates used by the package validator."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import numpy as np

from kinematics import transition_finding
from safety_contract import MIN_CHAIN_DURATION_BEATS, RECOVERY_BEATS, occupied_until, same_hand_after_hold_too_soon


class ValidationClock:
    def __init__(self, audio: dict[str, Any], grid: list[float], bpm: float):
        self.grid = grid
        self.bpm = bpm
        times = audio.get("timesSeconds")
        if times is None:
            self.exported = list(grid)
        else:
            if len(times) != len(grid) or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
                raise ValueError("RL time positions must match the grid and increase monotonically")
            self.exported = [round(float(time) * bpm / 60.0, 8) for time in times]

    def beat(self, beat: float) -> float:
        return float(np.interp(beat, self.grid, self.exported))

    def note(self, note: dict[str, Any] | None) -> dict[str, Any] | None:
        return {**note, "b": self.beat(float(note["b"]))} if note is not None else None


def transition_is_safe(previous: dict[str, Any], current: dict[str, Any], bpm: float, difficulty: str) -> bool:
    if transition_finding(previous, current, bpm, recovery_beats=RECOVERY_BEATS[difficulty]) is not None:
        return False
    # validate_v3 also enforces this reach cap independently of the swing model.
    # Keep validator-based adversarial tests to detect any future contract drift.
    gap = float(current["b"]) - float(previous["b"])
    distance = math.hypot(int(current["x"]) - int(previous["x"]), int(current["y"]) - int(previous["y"]))
    return not (gap <= .25 and distance > 2.5)


def safe_hold_candidates(notes, arcs, chains, audio, grid, bpm, difficulty):
    """Keep only decorations compatible with the already selected hand paths.

    The policy currently selects notes, not holds. Decoration must therefore
    respect those notes and must never rewrite a valid swing into an unsafe exit.
    """
    clock = ValidationClock(audio, grid, bpm)
    hands = {color: sorted((clock.note(note) for note in notes if note["c"] == color), key=lambda note: note["b"])
             for color in (0, 1)}
    accepted = {"arc": [], "chain": []}
    busy = {0: -math.inf, 1: -math.inf}
    rejected = Counter()
    proposals = [("arc", item) for item in arcs] + [("chain", item) for item in chains]
    for kind, item in sorted(proposals, key=lambda proposal: (proposal[1]["b"], proposal[0])):
        color = item["c"]
        start, tail = clock.beat(item["b"]), clock.beat(item["tb"])
        if item["tb"] > grid[-1] or tail <= start:
            rejected["outside_grid"] += 1
            continue
        if kind == "chain" and tail - start < MIN_CHAIN_DURATION_BEATS - 1e-8:
            rejected["chain_too_short"] += 1
            continue
        if start < busy[color]:
            rejected["hold_occupancy"] += 1
            continue
        hand = hands[color]
        inside = [note for note in hand if start + 1e-6 < note["b"] < tail - 1e-6]
        if inside or (kind == "chain" and any(abs(note["b"] - tail) <= 1e-6 for note in hand)):
            rejected["note_occupancy"] += 1
            continue
        next_note = next((note for note in hand if note["b"] > tail + 1e-6), None)
        exit_pose = {"b": tail, "c": color, "x": item["tx"], "y": item["ty"], "d": item.get("tc", item["d"])}
        if next_note is not None:
            gap = next_note["b"] - tail
            if same_hand_after_hold_too_soon(gap) or next_note["b"] < occupied_until(tail, RECOVERY_BEATS[difficulty]):
                rejected["post_hold_recovery"] += 1
                continue
            if not transition_is_safe(exit_pose, next_note, bpm, difficulty):
                rejected["tail_exit_motion"] += 1
                continue
        accepted[kind].append(item)
        busy[color] = occupied_until(tail, RECOVERY_BEATS[difficulty])
    return accepted["arc"], accepted["chain"], dict(rejected)
