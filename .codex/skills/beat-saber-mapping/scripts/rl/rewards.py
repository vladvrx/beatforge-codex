#!/usr/bin/env python3
"""Multi-objective reward functions for Beat Saber Reinforcement Learning.

Encodes biomechanics, parity kinematics, musical alignment, and official
post-2022 mapping standards into modular reward signals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from beatforge_core import (
    circular_direction_distance,
    direction_vector,
)
from kinematics import (
    CONTROLLER_CLEARANCE,
    cross_hand_finding,
    direction_family,
    double_is_safe,
    saber_paths_intersect,
    swing_pose,
    transition_finding,
    vision_blocking_double,
)
from safety_contract import (
    POST_HOLD_SAME_HAND_BEATS,
    RECOVERY_BEATS,
    hold_span_beats,
    occupied_until,
    same_hand_after_hold_too_soon,
)


@dataclass
class RewardBreakdown:
    flow: float = 0.0
    musicality: float = 0.0
    style: float = 0.0
    density: float = 0.0
    penalties: float = 0.0
    total: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "flow": round(self.flow, 4),
            "musicality": round(self.musicality, 4),
            "style": round(self.style, 4),
            "density": round(self.density, 4),
            "penalties": round(self.penalties, 4),
            "total": round(self.total, 4),
        }


class KinematicReward:
    """Evaluates biomechanical feasibility, swing momentum, and parity flow."""

    def __init__(self, difficulty: str = "Expert", bpm: float = 120.0):
        self.difficulty = difficulty
        self.bpm = max(1.0, bpm)
        self.recovery_beats = RECOVERY_BEATS.get(difficulty, 0.30)

    def evaluate_transition(
        self,
        prev_note: Optional[Dict[str, Any]],
        curr_note: Dict[str, Any],
    ) -> Tuple[float, float]:
        """Compute flow reward and kinematic penalty for a single hand transition."""
        if prev_note is None:
            return 0.2, 0.0  # Initial note placement

        gap_beats = float(curr_note["b"]) - float(prev_note["b"])
        if gap_beats <= 1e-7:
            # Same hand at exact same beat is illegal
            return 0.0, -10.0

        # Check hard transition finding from kinematics
        finding = transition_finding(
            prev_note,
            curr_note,
            self.bpm,
            recovery_beats=self.recovery_beats,
        )
        if finding is not None:
            constraint = finding.get("constraint", "")
            if constraint == "impossible_rotational_reversal":
                return 0.0, -4.0
            if constraint == "repeated_cut_without_reset":
                return 0.0, -5.0
            if constraint == "parity_family_repeat":
                return 0.0, -3.5
            if constraint == "recovery_speed":
                return 0.0, -3.0
            return 0.0, -5.0

        # Compute continuous momentum alignment
        prev_pose = swing_pose(prev_note)
        curr_pose = swing_pose(curr_note)

        flow_reward = 0.0
        if prev_pose.direction != 8 and curr_pose.direction != 8:
            dx_prev, dy_prev = direction_vector(prev_pose.direction)
            dx_curr, dy_curr = direction_vector(curr_pose.direction)
            len_prev = math.hypot(dx_prev, dy_prev)
            len_curr = math.hypot(dx_curr, dy_curr)

            if len_prev > 1e-6 and len_curr > 1e-6:
                # Dot product between exit momentum and entry momentum
                # Opposite cut directions (e.g. Down followed by Up) have dot = -1.0
                # which yields maximum natural swing reversal (-dot = 1.0)
                dot = (dx_prev * dx_curr + dy_prev * dy_curr) / (len_prev * len_curr)
                flow_reward = max(0.0, -dot) * 1.0

                # Circular / perpendicular flow (dot == 0) also feels natural on sweeps
                if abs(dot) < 0.3:
                    flow_reward += 0.5
        else:
            # Dot note is neutral
            flow_reward = 0.3

        # Home lane preference: reward hand resting on its home side
        color = int(curr_note.get("c", 0))
        x = int(curr_note.get("x", 0))
        if color == 0 and x in (0, 1):
            flow_reward += 0.2
        elif color == 1 and x in (2, 3):
            flow_reward += 0.2
        elif (color == 0 and x == 3) or (color == 1 and x == 0):
            # Extreme crossover
            if gap_beats < 0.5:
                flow_reward -= 0.5

        return flow_reward, 0.0

    def evaluate_simultaneous(
        self,
        red_note: Optional[Dict[str, Any]],
        blue_note: Optional[Dict[str, Any]],
    ) -> Tuple[float, float]:
        """Evaluate two hands acting at the same timestamp."""
        if red_note is None or blue_note is None:
            return 0.0, 0.0

        # Exact same coordinate collision
        if int(red_note["x"]) == int(blue_note["x"]) and int(red_note["y"]) == int(blue_note["y"]):
            return 0.0, -10.0

        # Cross-hand collision / handclap finding
        finding = cross_hand_finding(red_note, blue_note)
        if finding is not None:
            constraint = finding.get("constraint", "")
            if constraint == "inward_facing_handclap":
                return 0.0, -6.0
            if constraint == "saber_path_intersection":
                return 0.0, -8.0
            if constraint == "controller_clearance":
                return 0.0, -4.0
            return 0.0, -5.0

        # Vision block check
        if vision_blocking_double(red_note, blue_note):
            return 0.0, -4.0

        # Safe, coordinated double hit bonus
        return 0.8, 0.0


class MusicalReward:
    """Evaluates rhythmic alignment, downbeat accents, and energy tracking."""

    def __init__(self, target_nps: float = 4.5):
        self.target_nps = target_nps

    def evaluate(
        self,
        beat: float,
        has_note: bool,
        is_double: bool,
        onset_strength: float,
        stem_energy: Dict[str, float],
        is_downbeat: bool,
        section_type: str = "verse",
    ) -> Tuple[float, float]:
        """Compute musical alignment reward and silence placement penalty."""
        if not has_note:
            # Idle at high transient: slight penalty if prominent downbeat missed
            if is_downbeat and onset_strength > 0.8:
                return 0.0, -0.2
            return 0.0, 0.0

        reward = 0.0
        penalty = 0.0

        # Onset alignment
        if onset_strength > 0.3:
            reward += min(2.0, onset_strength * 1.5)
        elif onset_strength < 0.05:
            # Placed note on complete silence / no transient
            penalty -= 0.8

        # Stem energy alignment (drums / bass emphasis)
        drums_energy = stem_energy.get("drums", 0.0)
        if drums_energy > 0.5:
            reward += 0.5

        # Downbeat / Drop accents with double notes
        if is_double:
            if is_downbeat or section_type in ("drop", "chorus"):
                reward += 1.0
            else:
                # Unmotivated double on weak offbeat
                penalty -= 0.3

        return reward, penalty


class StyleReward:
    """Evaluates adherence to official post-2022 mapping conventions."""

    def __init__(self, difficulty: str = "Expert"):
        self.difficulty = difficulty

    def evaluate_pattern(
        self,
        recent_notes: List[Dict[str, Any]],
        current_nps: float,
        target_nps: float,
    ) -> Tuple[float, float]:
        """Evaluate rolling pattern statistics against official corpus distributions."""
        reward = 0.0
        penalty = 0.0

        if not recent_notes:
            return 0.0, 0.0

        # Density consistency: penalize extreme deviations from target NPS
        nps_diff = abs(current_nps - target_nps)
        if nps_diff > 3.0:
            penalty -= 1.0
        elif nps_diff < 1.0:
            reward += 0.5

        # Avoid same-cell repeats (stacking notes on identical x,y rapidly)
        if len(recent_notes) >= 2:
            last = recent_notes[-1]
            prev = recent_notes[-2]
            if last["c"] == prev["c"] and last["x"] == prev["x"] and last["y"] == prev["y"]:
                if (float(last["b"]) - float(prev["b"])) < 0.5:
                    penalty -= 2.0

        return reward, penalty


class CompositeReward:
    """Combines all reward channels into a unified scalar return."""

    def __init__(
        self,
        difficulty: str = "Expert",
        bpm: float = 120.0,
        target_nps: float = 4.5,
        w_flow: float = 1.0,
        w_musical: float = 1.0,
        w_style: float = 0.6,
    ):
        self.kinematic = KinematicReward(difficulty, bpm)
        self.musical = MusicalReward(target_nps)
        self.style = StyleReward(difficulty)
        self.w_flow = w_flow
        self.w_musical = w_musical
        self.w_style = w_style

    def compute_step_reward(
        self,
        beat: float,
        red_prev: Optional[Dict[str, Any]],
        red_curr: Optional[Dict[str, Any]],
        blue_prev: Optional[Dict[str, Any]],
        blue_curr: Optional[Dict[str, Any]],
        onset_strength: float,
        stem_energy: Dict[str, float],
        is_downbeat: bool,
        section_type: str,
        recent_notes: List[Dict[str, Any]],
        current_nps: float,
        target_nps: float,
    ) -> RewardBreakdown:
        breakdown = RewardBreakdown()

        # 1. Left hand (red) kinematic transition
        if red_curr is not None:
            r_flow, r_pen = self.kinematic.evaluate_transition(red_prev, red_curr)
            breakdown.flow += r_flow
            breakdown.penalties += r_pen

        # 2. Right hand (blue) kinematic transition
        if blue_curr is not None:
            b_flow, b_pen = self.kinematic.evaluate_transition(blue_prev, blue_curr)
            breakdown.flow += b_flow
            breakdown.penalties += b_pen

        # 3. Simultaneous hand interaction
        if red_curr is not None and blue_curr is not None:
            sim_flow, sim_pen = self.kinematic.evaluate_simultaneous(red_curr, blue_curr)
            breakdown.flow += sim_flow
            breakdown.penalties += sim_pen

        # 4. Musicality evaluation
        has_note = red_curr is not None or blue_curr is not None
        is_double = red_curr is not None and blue_curr is not None
        mus_rew, mus_pen = self.musical.evaluate(
            beat,
            has_note,
            is_double,
            onset_strength,
            stem_energy,
            is_downbeat,
            section_type,
        )
        breakdown.musicality += mus_rew
        breakdown.penalties += mus_pen

        # 5. Style pattern evaluation
        if has_note:
            sty_rew, sty_pen = self.style.evaluate_pattern(
                recent_notes, current_nps, target_nps
            )
            breakdown.style += sty_rew
            breakdown.penalties += sty_pen

        # Total combined reward
        breakdown.total = (
            self.w_flow * breakdown.flow
            + self.w_musical * breakdown.musicality
            + self.w_style * breakdown.style
            + breakdown.penalties
        )

        return breakdown
