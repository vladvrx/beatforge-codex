#!/usr/bin/env python3
"""Gymnasium-compatible Beat Saber Mapping RL Environment (BeatSaberEnv).

Enables training RL agents on audio stems, kinematic state, and physical flow
constraints with invalid-action masking and multi-objective rewards.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from kinematics import (
    CONTROLLER_CLEARANCE,
    cross_hand_finding,
    saber_paths_intersect,
    swing_pose,
    transition_finding,
    vision_blocking_double,
)
from safety_contract import RECOVERY_BEATS, occupied_until
from .safety import ValidationClock, transition_is_safe
try:
    from .rewards import CompositeReward, RewardBreakdown
except ImportError:
    from rl.rewards import CompositeReward, RewardBreakdown

NUM_GRID_X = 4
NUM_GRID_Y = 3
NUM_DIRECTIONS = 9  # 0..7 (cuts), 8 (dot)
NUM_NOTE_POSES = NUM_GRID_X * NUM_GRID_Y * NUM_DIRECTIONS  # 108
ACTION_IDLE = 0
NUM_HAND_ACTIONS = 1 + NUM_NOTE_POSES  # 109 (0=Idle, 1..108=Note)

DIFFICULTIES = ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
TARGET_NPS_MAP = {
    "Easy": 1.59,
    "Normal": 2.50,
    "Hard": 3.27,
    "Expert": 4.25,
    "ExpertPlus": 5.47,
}


def encode_hand_action(x: int, y: int, direction: int) -> int:
    """Encode (x, y, direction) into action integer 1..108."""
    return 1 + (x * (NUM_GRID_Y * NUM_DIRECTIONS) + y * NUM_DIRECTIONS + direction)


def decode_hand_action(action_id: int) -> Optional[Tuple[int, int, int]]:
    """Decode action integer 0..108 into None (if 0) or (x, y, direction)."""
    if action_id == ACTION_IDLE:
        return None
    idx = action_id - 1
    direction = idx % NUM_DIRECTIONS
    idx //= NUM_DIRECTIONS
    y = idx % NUM_GRID_Y
    x = idx // NUM_GRID_Y
    return x, y, direction


class BeatSaberEnv:
    """RL Environment for Step-by-Step Beat Saber Choreography."""

    def __init__(
        self,
        audio_features: Optional[Dict[str, Any]] = None,
        beat_grid: Optional[List[float]] = None,
        bpm: float = 120.0,
        difficulty: str = "Expert",
        lookahead_steps: int = 4,
    ):
        self.bpm = float(bpm)
        if not math.isfinite(self.bpm) or self.bpm <= 0:
            raise ValueError("RL BPM must be finite and positive")
        self.base_bpm = self.bpm
        self.difficulty = difficulty if difficulty in DIFFICULTIES else "Expert"
        self.target_nps = TARGET_NPS_MAP.get(self.difficulty, 4.25)
        self.lookahead_steps = max(1, lookahead_steps)
        self.rng = np.random.default_rng(0)

        self.reward_engine = CompositeReward(
            difficulty=self.difficulty,
            bpm=self.bpm,
            target_nps=self.target_nps,
        )

        # Internal state buffers
        self.beat_grid = self._normalize_beat_grid(beat_grid) if beat_grid is not None else self._generate_synthetic_grid(64, self.bpm)
        self.audio_features = audio_features if audio_features is not None else self._generate_synthetic_audio(len(self.beat_grid))
        self.current_step = 0
        self.total_steps = len(self.beat_grid)
        self.validation_clock = ValidationClock(self.audio_features, self.beat_grid, self.base_bpm)

        # Kinematic hand states
        self.red_prev: Optional[Dict[str, Any]] = None
        self.blue_prev: Optional[Dict[str, Any]] = None
        self.placed_notes: List[Dict[str, Any]] = []
        self.history_window = 16

        # Define Observation & Action dimension constants
        # Audio feature per step: 6 stems + 1 onset + 1 flux + 1 phase + 1 downbeat = 10
        # Context window: (1 + lookahead_steps) * 10 = (1 + 4) * 10 = 50
        # Kinematic state: 2 hands * 6 features = 12
        # Target conditioning: 5 diff one-hot + 1 bpm + 1 target_nps = 7
        self.obs_dim = (1 + self.lookahead_steps) * 10 + 12 + 7
        self.action_dim = NUM_HAND_ACTIONS  # 109 per hand

    def _normalize_beat_grid(self, beat_grid: Any) -> List[float]:
        if beat_grid is None:
            return self._generate_synthetic_grid(64, self.bpm)
        if isinstance(beat_grid, dict):
            raise ValueError("Use rl.features.load_analysis_features to project an analysis grid")
        result = [float(b) for b in beat_grid]
        if not result or not np.isfinite(result).all() or np.any(np.diff(result) <= 0):
            raise ValueError("Environment beat grid must be nonempty, finite, and strictly increasing")
        return result

    def _generate_synthetic_grid(self, num_beats: int, bpm: float) -> List[float]:
        # Quarter-beat subdivision
        return [round(i * 0.25, 4) for i in range(num_beats * 4)]

    def _generate_synthetic_audio(self, length: int) -> Dict[str, Any]:
        stems = ("drums", "bass", "guitar", "piano", "vocals", "other")
        return {
            "onsets": [1.0 if i % 4 == 0 else 0.0 for i in range(length)],
            "flux": [0.0] * length,
            "stems": {stem: [0.0] * length for stem in stems},
            "stemAvailability": {stem: False for stem in stems},
            "sections": ["unknown"] * length,
            "provenance": {"source": "synthetic-test-pulse", "timingStatus": "synthetic"},
        }

    def reset(
        self,
        seed: Optional[int] = None,
        audio_features: Optional[Dict[str, Any]] = None,
        beat_grid: Optional[Any] = None,
        bpm: Optional[float] = None,
        difficulty: Optional[str] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        if seed is not None:
            self.rng = np.random.default_rng(seed)

        if bpm is not None:
            if not math.isfinite(float(bpm)) or float(bpm) <= 0:
                raise ValueError("RL BPM must be finite and positive")
            self.bpm = self.base_bpm = float(bpm)
        if difficulty is not None and difficulty in DIFFICULTIES:
            self.difficulty = difficulty
            self.target_nps = TARGET_NPS_MAP.get(self.difficulty, 4.25)
        self.reward_engine = CompositeReward(
            difficulty=self.difficulty, bpm=self.bpm, target_nps=self.target_nps,
        )

        if beat_grid is not None:
            self.beat_grid = self._normalize_beat_grid(beat_grid)
        elif audio_features is not None:
            self.beat_grid = self._normalize_beat_grid([i * 0.25 for i in range(len(audio_features.get("onsets", [])))])

        if audio_features is not None:
            self.audio_features = audio_features

        self.current_step = 0
        self.total_steps = len(self.beat_grid)
        self.validation_clock = ValidationClock(self.audio_features, self.beat_grid, self.base_bpm)
        self.red_prev = None
        self.blue_prev = None
        self.placed_notes = []
        self._update_step_tempo()

        obs = self._get_observation()
        info = {"current_step": 0, "beat": self.beat_grid[0] if self.beat_grid else 0.0}
        return obs, info

    def action_masks(self) -> Tuple[np.ndarray, np.ndarray]:
        """Compute binary action masks for Left (Red) and Right (Blue) hands."""
        self._update_step_tempo()
        mask_red = np.ones(NUM_HAND_ACTIONS, dtype=bool)
        mask_blue = np.ones(NUM_HAND_ACTIONS, dtype=bool)

        current_beat = self.beat_grid[self.current_step] if self.current_step < self.total_steps else 0.0
        validation_beat = self.validation_clock.beat(current_beat)
        recovery_beats = RECOVERY_BEATS.get(self.difficulty, 0.30)

        # Rhythmic grid subdivision gating
        step_subdiv = round(current_beat * 4)
        if self.difficulty == "Easy" and (step_subdiv % 4) != 0:
            mask_red[1:] = False
            mask_blue[1:] = False
            return mask_red, mask_blue
        elif self.difficulty == "Normal" and (step_subdiv % 2) != 0:
            mask_red[1:] = False
            mask_blue[1:] = False
            return mask_red, mask_blue

        # 1. Mask Left Hand (Red, Color 0)
        if self.red_prev is not None and (validation_beat - self.validation_clock.beat(float(self.red_prev["b"]))) < recovery_beats:
            mask_red[1:] = False
        else:
            for act in range(1, NUM_HAND_ACTIONS):
                decoded = decode_hand_action(act)
                if decoded is None:
                    continue
                x, y, d = decoded
                candidate = {"b": current_beat, "c": 0, "x": x, "y": y, "d": d}

                if self.red_prev is not None:
                    if not transition_is_safe(self.validation_clock.note(self.red_prev), self.validation_clock.note(candidate), self.base_bpm, self.difficulty):
                        mask_red[act] = False

        # 2. Mask Right Hand (Blue, Color 1)
        if self.blue_prev is not None and (validation_beat - self.validation_clock.beat(float(self.blue_prev["b"]))) < recovery_beats:
            mask_blue[1:] = False
        else:
            for act in range(1, NUM_HAND_ACTIONS):
                decoded = decode_hand_action(act)
                if decoded is None:
                    continue
                x, y, d = decoded
                candidate = {"b": current_beat, "c": 1, "x": x, "y": y, "d": d}

                if self.blue_prev is not None:
                    if not transition_is_safe(self.validation_clock.note(self.blue_prev), self.validation_clock.note(candidate), self.base_bpm, self.difficulty):
                        mask_blue[act] = False

        return mask_red, mask_blue

    def blue_action_mask(self, red_action: int, base_mask: Optional[np.ndarray] = None) -> np.ndarray:
        """Condition the second hand on the first hand's actual selected action."""
        mask = (base_mask if base_mask is not None else self.action_masks()[1]).copy()
        decoded = decode_hand_action(red_action)
        if decoded is None or self.current_step >= self.total_steps:
            return mask
        beat = self.beat_grid[self.current_step]
        red = {"b": beat, "c": 0, "x": decoded[0], "y": decoded[1], "d": decoded[2]}
        for action in np.flatnonzero(mask[1:]) + 1:
            x, y, direction = decode_hand_action(int(action))
            blue = {"b": beat, "c": 1, "x": x, "y": y, "d": direction}
            if cross_hand_finding(red, blue) is not None or vision_blocking_double(red, blue):
                mask[action] = False
        mask[ACTION_IDLE] = True
        return mask

    def step(
        self, action: Tuple[int, int]
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """Execute one step in the mapping sequence."""
        if self.current_step >= self.total_steps:
            obs = self._get_observation()
            return obs, 0.0, True, False, {}

        act_red, act_blue = action
        current_beat = self.beat_grid[self.current_step]
        self._update_step_tempo()

        # Decode actions
        red_decoded = decode_hand_action(act_red)
        blue_decoded = decode_hand_action(act_blue)

        red_curr = None
        if red_decoded is not None:
            rx, ry, rd = red_decoded
            red_curr = {"b": current_beat, "c": 0, "x": rx, "y": ry, "d": rd}

        blue_curr = None
        if blue_decoded is not None:
            bx, by, bd = blue_decoded
            blue_curr = {"b": current_beat, "c": 1, "x": bx, "y": by, "d": bd}

        # Extract audio features at current step
        onset = self._get_audio_field("onsets", self.current_step, 0.0)
        stem_energy = {
            stem: self._get_stem_energy(stem, self.current_step)
            for stem in ("drums", "bass", "guitar", "piano", "vocals", "other")
        }
        is_downbeat = (round(current_beat * 4) % 16) == 0
        section_type = self._get_audio_field("sections", self.current_step, "verse")

        # Rolling NPS calculation
        current_nps = self._compute_rolling_nps()

        # Compute Step Reward
        breakdown: RewardBreakdown = self.reward_engine.compute_step_reward(
            beat=self.validation_clock.beat(current_beat),
            red_prev=self.validation_clock.note(self.red_prev),
            red_curr=self.validation_clock.note(red_curr),
            blue_prev=self.validation_clock.note(self.blue_prev),
            blue_curr=self.validation_clock.note(blue_curr),
            onset_strength=onset,
            stem_energy=stem_energy,
            is_downbeat=is_downbeat,
            section_type=section_type,
            recent_notes=[self.validation_clock.note(note) for note in self.placed_notes[-self.history_window:]],
            current_nps=current_nps,
            target_nps=self.target_nps,
        )

        # Update note history & hand states
        if red_curr is not None:
            self.placed_notes.append(red_curr)
            self.red_prev = red_curr
        if blue_curr is not None:
            self.placed_notes.append(blue_curr)
            self.blue_prev = blue_curr

        self.current_step += 1
        self._update_step_tempo()
        terminated = self.current_step >= self.total_steps
        truncated = False

        obs = self._get_observation()
        info = {
            "breakdown": breakdown.to_dict(),
            "notes_placed": len(self.placed_notes),
            "beat": current_beat,
        }

        return obs, float(breakdown.total), terminated, truncated, info

    def _get_audio_field(self, field: str, idx: int, default: Any) -> Any:
        arr = self.audio_features.get(field)
        if arr is not None and idx < len(arr):
            return arr[idx]
        return default

    def _update_step_tempo(self) -> None:
        if not self.total_steps:
            return
        value = self._get_audio_field("stepBpms", min(self.current_step, self.total_steps - 1), self.bpm)
        self.bpm = float(value)
        self.reward_engine.kinematic.bpm = self.base_bpm

    def _get_stem_energy(self, stem: str, idx: int) -> float:
        stems = self.audio_features.get("stems", {})
        arr = stems.get(stem)
        if arr is not None and idx < len(arr):
            return float(arr[idx])
        return 0.0

    def _compute_rolling_nps(self) -> float:
        if not self.placed_notes or len(self.placed_notes) < 2:
            return 0.0
        recent = self.placed_notes[-8:]
        span_beats = float(recent[-1]["b"]) - float(recent[0]["b"])
        if span_beats <= 1e-6:
            return 0.0
        times = self.audio_features.get("timesSeconds")
        if times is not None and len(times) == self.total_steps:
            span_seconds = float(np.interp(float(recent[-1]["b"]), self.beat_grid, times) - np.interp(float(recent[0]["b"]), self.beat_grid, times))
        else:
            span_seconds = span_beats * 60.0 / self.bpm
        return len(recent) / max(0.1, span_seconds)

    def _get_observation(self) -> np.ndarray:
        """Construct state feature vector for Actor-Critic."""
        features = []

        # 1. Audio context & lookahead features
        for step_offset in range(1 + self.lookahead_steps):
            target_step = min(self.current_step + step_offset, self.total_steps - 1)
            # 6 Stems
            for stem in ("drums", "bass", "guitar", "piano", "vocals", "other"):
                features.append(self._get_stem_energy(stem, target_step))
            # Onset & Flux
            features.append(float(self._get_audio_field("onsets", target_step, 0.0)))
            features.append(float(self._get_audio_field("flux", target_step, 0.0)))
            # Phase & Downbeat
            beat_val = self.beat_grid[target_step] if target_step < self.total_steps else 0.0
            phase = (beat_val % 1.0)
            is_downbeat = 1.0 if (round(beat_val * 4) % 16) == 0 else 0.0
            features.append(float(phase))
            features.append(float(is_downbeat))

        # 2. Kinematic state for Red and Blue
        for hand_prev in (self.red_prev, self.blue_prev):
            if hand_prev is not None:
                current_beat = self.beat_grid[min(self.current_step, self.total_steps - 1)]
                gap = current_beat - float(hand_prev["b"])
                features.extend([
                    float(hand_prev["x"]) / 3.0,
                    float(hand_prev["y"]) / 2.0,
                    float(hand_prev["d"]) / 8.0,
                    min(10.0, gap) / 10.0,
                    1.0,  # active flag
                    1.0 if hand_prev["d"] != 8 else 0.0,
                ])
            else:
                features.extend([0.5, 0.0, 1.0, 1.0, 0.0, 0.0])

        # 3. Target Difficulty & Conditioning
        diff_one_hot = [1.0 if d == self.difficulty else 0.0 for d in DIFFICULTIES]
        features.extend(diff_one_hot)
        features.append(self.bpm / 200.0)
        features.append(self.target_nps / 10.0)

        obs = np.array(features, dtype=np.float32)
        return obs
