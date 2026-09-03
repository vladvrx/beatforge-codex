#!/usr/bin/env python3
"""Single occupancy and recovery contract for generation, realize, and QA.

Pacific Coast Highway findings are regression evidence, not song-specific rules.
Every input map must use these functions. Do not re-literal quarter-beat gaps,
chain minimums, or recovery tables in SAT, realize, or validate_map.
"""

from __future__ import annotations

from typing import Any

# Quarter-beat same-hand lock after a hold tail, with a float epsilon so
# gap == 0.25 is still illegal (validate_map RAPID_SAME_HAND_AFTER_HOLD).
POST_HOLD_SAME_HAND_BEATS = 0.250001

# Serialized chain tails are at least one quarter-beat long even when the
# musical sustain was shorter. SAT occupancy must use this span, not the
# raw intent duration, or realize will reject a "legal" SAT solution.
MIN_CHAIN_DURATION_BEATS = 0.25

RECOVERY_BEATS = {
    "Easy": 0.75,
    "Normal": 0.55,
    "Hard": 0.40,
    "Expert": 0.30,
    "ExpertPlus": 0.22,
}


def hold_span_beats(kind: str, duration: float) -> float:
    span = float(duration or 0.0)
    if kind == "chain":
        return max(span, MIN_CHAIN_DURATION_BEATS)
    return span


def hold_tail_beat(head_beat: float, kind: str, duration: float = 0.0) -> float:
    return float(head_beat) + hold_span_beats(kind, duration)


def occupied_until(tail_beat: float, recovery_beats: float) -> float:
    """Earliest beat the same saber may strike after a hold tail."""

    return float(tail_beat) + max(float(recovery_beats), POST_HOLD_SAME_HAND_BEATS)


def occupied_until_from_intent(intent: dict[str, Any], recovery_beats: float) -> float:
    return occupied_until(
        hold_tail_beat(float(intent["beat"]), str(intent.get("kind") or "note"), float(intent.get("duration") or 0.0)),
        recovery_beats,
    )


def same_hand_after_hold_too_soon(gap_beats: float) -> bool:
    return float(gap_beats) <= POST_HOLD_SAME_HAND_BEATS


def intent_tail_beat(intent: dict[str, Any]) -> float:
    return hold_tail_beat(float(intent["beat"]), str(intent.get("kind") or "note"), float(intent.get("duration") or 0.0))
