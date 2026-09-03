#!/usr/bin/env python3
"""Deterministic Beat Saber swing geometry used by generation and QA."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from beatforge_core import circular_direction_distance, direction_vector


PRE_SWING_DISTANCE = 0.72
FOLLOW_THROUGH_DISTANCE = 0.88
CONTROLLER_CLEARANCE = 0.34


@dataclass(frozen=True)
class SwingPose:
    beat: float
    color: int
    x: int
    y: int
    direction: int
    entry_x: float
    entry_y: float
    contact_x: float
    contact_y: float
    exit_x: float
    exit_y: float


def _unit(direction: int) -> tuple[float, float]:
    dx, dy = direction_vector(direction)
    length = math.hypot(dx, dy)
    if not length:
        return 0.0, 0.0
    return dx / length, dy / length


def swing_pose(note: dict[str, Any]) -> SwingPose:
    """Return pre-swing, contact, and follow-through points for one note."""

    x = int(note["x"])
    y = int(note["y"])
    direction = int(note["d"])
    dx, dy = _unit(direction)
    contact_x = float(x)
    contact_y = float(y)
    if direction == 8:
        return SwingPose(
            float(note["b"]),
            int(note["c"]),
            x,
            y,
            direction,
            contact_x,
            contact_y,
            contact_x,
            contact_y,
            contact_x,
            contact_y,
        )
    return SwingPose(
        float(note["b"]),
        int(note["c"]),
        x,
        y,
        direction,
        contact_x - dx * PRE_SWING_DISTANCE,
        contact_y - dy * PRE_SWING_DISTANCE,
        contact_x,
        contact_y,
        contact_x + dx * FOLLOW_THROUGH_DISTANCE,
        contact_y + dy * FOLLOW_THROUGH_DISTANCE,
    )


def transition_finding(
    previous: dict[str, Any],
    current: dict[str, Any],
    bpm: float,
    *,
    recovery_beats: float,
) -> dict[str, Any] | None:
    """Return a release-blocking same-hand flow finding, if one exists."""

    left = swing_pose(previous)
    right = swing_pose(current)
    gap_beats = right.beat - left.beat
    seconds = max(0.0, gap_beats * 60.0 / max(1e-6, bpm))
    context = {
        "previousBeat": left.beat,
        "currentBeat": right.beat,
        "color": left.color,
        "previousPosition": {"x": left.x, "y": left.y},
        "currentPosition": {"x": right.x, "y": right.y},
        "previousDirection": left.direction,
        "currentDirection": right.direction,
        "availableRecoveryBeats": gap_beats,
        "requiredRecoveryBeats": recovery_beats,
        "availableSeconds": seconds,
        "recoverySeconds": seconds,
    }
    if gap_beats <= 1e-7:
        return {
            **context,
            "constraint": "simultaneous_same_color",
            "failedConstraint": "simultaneous_same_color",
            "message": "one saber cannot strike two notes at the same beat",
        }
    if left.direction == 8 or right.direction == 8:
        return None

    reposition = math.hypot(right.entry_x - left.exit_x, right.entry_y - left.exit_y)
    available = max(0.0, seconds - 0.035)
    required_speed = reposition / max(0.025, available)
    prev_lean = (left.x - 1.5) * (1.0 if left.color == 0 else -1.0)
    next_lean = (right.x - 1.5) * (1.0 if right.color == 0 else -1.0)
    follow_dx, follow_dy = _unit(left.direction)
    travel_dx = right.entry_x - left.exit_x
    travel_dy = right.entry_y - left.exit_y
    travel_len = math.hypot(travel_dx, travel_dy)
    momentum_dot = 0.0 if travel_len <= 1e-9 else (follow_dx * travel_dx + follow_dy * travel_dy) / travel_len
    same_direction = left.direction == right.direction
    same_family = direction_family(left.direction) == direction_family(right.direction)

    speed_fields = {
        "requiredGridUnitsPerSecond": round(required_speed, 4),
        "repositionDistance": round(reposition, 4),
    }
    if gap_beats <= recovery_beats + 1e-7 and same_direction:
        return {
            **context,
            **speed_fields,
            "constraint": "repeated_cut_without_reset",
            "failedConstraint": "repeated_cut_without_reset",
            "message": "the same cut direction repeats before the saber can reset",
        }
    if gap_beats <= max(0.5, recovery_beats) + 1e-7 and same_family:
        return {
            **context,
            **speed_fields,
            "constraint": "parity_family_repeat",
            "failedConstraint": "parity_family_repeat",
            "message": "consecutive cuts stay in one parity family without a readable reset",
        }
    # 135-degree cut changes are a wrist snap, not a return. Keep them off combo spacing.
    twist = circular_direction_distance(left.direction, right.direction)
    if twist == 3 and gap_beats <= max(0.75, recovery_beats) + 1e-7:
        return {
            **context,
            **speed_fields,
            "constraint": "impossible_rotational_reversal",
            "failedConstraint": "impossible_rotational_reversal",
            "message": "the saber cannot reverse 135 degrees before the next pre-swing",
            "maximumGridUnitsPerSecond": 13.0,
        }
    if required_speed > 13.0 and gap_beats <= 0.75 + 1e-7:
        return {
            **context,
            **speed_fields,
            "constraint": "recovery_speed",
            "failedConstraint": "recovery_speed",
            "message": "the prior follow-through cannot reach the next pre-swing in time",
            "maximumGridUnitsPerSecond": 13.0,
        }
    if (
        gap_beats <= max(recovery_beats, 0.5) + 1e-7
        and abs(prev_lean) >= 1.4
        and abs(next_lean) >= 1.4
        and prev_lean * next_lean < 0.0
    ):
        return {
            **context,
            **speed_fields,
            "constraint": "body_lean_reversal",
            "failedConstraint": "body_lean_reversal",
            "message": "the torso cannot reverse a far-lane lean before the next same-hand hit",
            "previousLean": round(prev_lean * 0.15, 4),
            "currentLean": round(next_lean * 0.15, 4),
        }
    if gap_beats <= recovery_beats + 1e-7 and travel_len >= 2.0 and momentum_dot < -0.7:
        return {
            **context,
            **speed_fields,
            "constraint": "rotational_momentum",
            "failedConstraint": "rotational_momentum",
            "message": "follow-through momentum points away from the next pre-swing",
            "momentumDot": round(momentum_dot, 4),
        }
    return None


def direction_family(direction: int) -> str:
    if direction in {1, 6, 7}:
        return "down"
    if direction in {0, 4, 5}:
        return "up"
    if direction == 2:
        return "left"
    if direction == 3:
        return "right"
    return "free"


def _orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_segment_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-12:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    amount = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / denominator,
        ),
    )
    projection = (start[0] + amount * dx, start[1] + amount * dy)
    return math.hypot(point[0] - projection[0], point[1] - projection[1])


def segment_distance(
    a0: tuple[float, float],
    a1: tuple[float, float],
    b0: tuple[float, float],
    b1: tuple[float, float],
) -> float:
    if _proper_intersect(a0, a1, b0, b1):
        return 0.0
    return min(
        _point_segment_distance(a0, b0, b1),
        _point_segment_distance(a1, b0, b1),
        _point_segment_distance(b0, a0, a1),
        _point_segment_distance(b1, a0, a1),
    )


def _proper_intersect(
    a0: tuple[float, float],
    a1: tuple[float, float],
    b0: tuple[float, float],
    b1: tuple[float, float],
) -> bool:
    """True only for a strict crossing. Collinear overlap is not a crossing."""

    o1 = _orientation(a0, a1, b0)
    o2 = _orientation(a0, a1, b1)
    o3 = _orientation(b0, b1, a0)
    o4 = _orientation(b0, b1, a1)
    return o1 * o2 < 0.0 and o3 * o4 < 0.0


def _swing_segments(pose: SwingPose) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    entry = (pose.entry_x, pose.entry_y)
    contact = (pose.contact_x, pose.contact_y)
    exit_point = (pose.exit_x, pose.exit_y)
    return [(entry, contact), (contact, exit_point), (entry, exit_point)]


def saber_paths_intersect(red: dict[str, Any], blue: dict[str, Any]) -> bool:
    left = swing_pose(red)
    right = swing_pose(blue)
    if left.direction == 8 or right.direction == 8:
        return False
    for a0, a1 in _swing_segments(left):
        for b0, b1 in _swing_segments(right):
            if _proper_intersect(a0, a1, b0, b1):
                return True
    return False


def cross_hand_finding(
    red: dict[str, Any], blue: dict[str, Any]
) -> dict[str, Any] | None:
    """Detect simultaneous saber sweeps that intersect or create a handclap."""

    left = swing_pose(red)
    right = swing_pose(blue)
    red_dx, _red_dy = _unit(left.direction)
    blue_dx, _blue_dy = _unit(right.direction)
    if red_dx > 0.25 and blue_dx < -0.25:
        return {
            "constraint": "inward_facing_handclap",
            "failedConstraint": "inward_facing_handclap",
            "minimumDistance": 0.0,
            "requiredClearance": CONTROLLER_CLEARANCE,
        }
    if left.direction == 8 or right.direction == 8:
        contact_distance = math.hypot(left.contact_x - right.contact_x, left.contact_y - right.contact_y)
        if contact_distance < CONTROLLER_CLEARANCE:
            return {
                "constraint": "controller_clearance",
                "failedConstraint": "controller_clearance",
                "minimumDistance": round(contact_distance, 4),
                "requiredClearance": CONTROLLER_CLEARANCE,
            }
        return None
    contact_distance = math.hypot(
        left.contact_x - right.contact_x, left.contact_y - right.contact_y
    )
    if contact_distance <= CONTROLLER_CLEARANCE:
        return {
            "constraint": "controller_clearance",
            "failedConstraint": "controller_clearance",
            "minimumDistance": round(contact_distance, 4),
            "requiredClearance": CONTROLLER_CLEARANCE,
        }
    if saber_paths_intersect(red, blue):
        return {
            "constraint": "saber_path_intersection",
            "failedConstraint": "saber_path_intersection",
            "minimumDistance": 0.0,
            "requiredClearance": CONTROLLER_CLEARANCE,
        }
    return None


def vision_blocking_double(red: dict[str, Any], blue: dict[str, Any]) -> bool:
    return (
        int(red.get("x", -1)) in (1, 2)
        and int(blue.get("x", -1)) in (1, 2)
        and int(red.get("y", 0)) >= 1
        and int(blue.get("y", 0)) >= 1
    )


def double_is_safe(red: dict[str, Any], blue: dict[str, Any]) -> bool:
    return cross_hand_finding(red, blue) is None and not vision_blocking_double(red, blue)


def bomb_on_swing_path(bomb: dict[str, Any], note: dict[str, Any], *, window_beats: float = 0.75) -> bool:
    """True when a bomb sits on a note cell or its pre-swing, contact, or follow-through."""

    if abs(float(bomb.get("b", 0)) - float(note.get("b", 0))) > window_beats:
        return False
    bx, by = int(bomb.get("x", -1)), int(bomb.get("y", -1))
    if int(note.get("x", -99)) == bx and int(note.get("y", -99)) == by:
        return True
    pose = swing_pose(note)
    cells = {
        (int(round(pose.entry_x)), int(round(pose.entry_y))),
        (int(round(pose.contact_x)), int(round(pose.contact_y))),
        (int(round(pose.exit_x)), int(round(pose.exit_y))),
    }
    return (bx, by) in cells
