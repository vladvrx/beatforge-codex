#!/usr/bin/env python3
"""Dataset loader for Behavioral Cloning (BC) and Trajectory Replay from Beat Saber maps."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset
try:
    from .environment import (
        BeatSaberEnv,
        encode_hand_action,
        NUM_HAND_ACTIONS,
    )
except ImportError:
    from rl.environment import (
        BeatSaberEnv,
        encode_hand_action,
        NUM_HAND_ACTIONS,
    )


class BeatSaberTrajectoryDataset(Dataset):
    """PyTorch Dataset yielding (observation, action_red, action_blue, mask_red, mask_blue)."""

    def __init__(
        self,
        samples: Optional[List[Dict[str, Any]]] = None,
    ):
        self.samples = samples or []

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        item = self.samples[idx]
        return {
            "obs": torch.tensor(item["obs"], dtype=torch.float32),
            "act_red": torch.tensor(item["act_red"], dtype=torch.long),
            "act_blue": torch.tensor(item["act_blue"], dtype=torch.long),
            "mask_red": torch.tensor(item["mask_red"], dtype=torch.bool),
            "mask_blue": torch.tensor(item["mask_blue"], dtype=torch.bool),
        }

    @classmethod
    def from_map_and_audio(
        cls,
        notes: List[Dict[str, Any]],
        audio_features: Dict[str, Any],
        beat_grid: List[float],
        bpm: float = 120.0,
        difficulty: str = "Expert",
    ) -> "BeatSaberTrajectoryDataset":
        """Convert a chart and audio feature sequence into step-by-step training observations."""
        env = BeatSaberEnv(
            audio_features=audio_features,
            beat_grid=beat_grid,
            bpm=bpm,
            difficulty=difficulty,
        )
        obs, _ = env.reset()

        # Build note lookup by beat and color
        notes_by_beat: Dict[float, Dict[int, Dict[str, Any]]] = {}
        for n in notes:
            b_round = round(float(n.get("b", 0.0)), 4)
            c = int(n.get("c", 0))
            if b_round not in notes_by_beat:
                notes_by_beat[b_round] = {}
            notes_by_beat[b_round][c] = n

        samples = []
        for step in range(len(beat_grid)):
            current_beat = round(beat_grid[step], 4)
            mask_red, mask_blue = env.action_masks()

            # Determine target actions from ground truth notes
            act_red = 0
            act_blue = 0
            beat_notes = notes_by_beat.get(current_beat, {})

            if 0 in beat_notes:
                n = beat_notes[0]
                act_red = encode_hand_action(int(n["x"]), int(n["y"]), int(n["d"]))
            if 1 in beat_notes:
                n = beat_notes[1]
                act_blue = encode_hand_action(int(n["x"]), int(n["y"]), int(n["d"]))

            samples.append({
                "obs": obs,
                "act_red": act_red,
                "act_blue": act_blue,
                "mask_red": mask_red,
                "mask_blue": mask_blue,
            })

            obs, _, term, _, _ = env.step((act_red, act_blue))
            if term:
                break

        return cls(samples)

    @classmethod
    def generate_synthetic(
        cls, num_songs: int = 4, beats_per_song: int = 32
    ) -> "BeatSaberTrajectoryDataset":
        """Generate synthetic dataset for offline training verification and tests."""
        samples = []
        for _ in range(num_songs):
            bpm = float(np.random.choice([120.0, 128.0, 140.0, 150.0]))
            grid = [round(i * 0.25, 4) for i in range(beats_per_song * 4)]
            audio = {
                "onsets": np.random.uniform(0.0, 1.0, size=(len(grid),)).tolist(),
                "flux": np.random.uniform(0.0, 1.0, size=(len(grid),)).tolist(),
                "stems": {
                    stem: np.random.uniform(0.0, 1.0, size=(len(grid),)).tolist()
                    for stem in ("drums", "bass", "guitar", "piano", "vocals", "other")
                },
                "sections": ["verse"] * len(grid),
            }

            env = BeatSaberEnv(audio_features=audio, beat_grid=grid, bpm=bpm, difficulty="Expert")
            obs, _ = env.reset()

            for step in range(len(grid)):
                mask_red, mask_blue = env.action_masks()
                # Pick valid random action
                valid_red = np.where(mask_red)[0]
                valid_blue = np.where(mask_blue)[0]
                act_red = int(np.random.choice(valid_red)) if len(valid_red) > 0 else 0
                act_blue = int(np.random.choice(valid_blue)) if len(valid_blue) > 0 else 0

                samples.append({
                    "obs": obs,
                    "act_red": act_red,
                    "act_blue": act_blue,
                    "mask_red": mask_red,
                    "mask_blue": mask_blue,
                })
                obs, _, term, _, _ = env.step((act_red, act_blue))
                if term:
                    break

        return cls(samples)
