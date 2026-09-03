#!/usr/bin/env python3
"""Beat Saber Map Generator powered by Trained RL Policy with v3/v4 Format Realization."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch

try:
    from .environment import (
        BeatSaberEnv,
        decode_hand_action,
        NUM_HAND_ACTIONS,
    )
    from .models import ActorCriticPolicy
except ImportError:
    from rl.environment import (
        BeatSaberEnv,
        decode_hand_action,
        NUM_HAND_ACTIONS,
    )
    from rl.models import ActorCriticPolicy
from kinematics import bomb_on_swing_path, swing_pose


class RLMapGenerator:
    """Generates standard five-difficulty Beat Saber maps using an RL policy."""

    def __init__(
        self,
        policy: Optional[ActorCriticPolicy] = None,
        model_path: Optional[Path] = None,
        device: str = "cpu",
    ):
        self.device = device
        if policy is not None:
            self.policy = policy
        elif model_path is not None and model_path.exists():
            self.policy = ActorCriticPolicy()
            self.policy.load_state_dict(torch.load(model_path, map_location=device))
        else:
            self.policy = ActorCriticPolicy()

        self.policy.to(device)
        self.policy.eval()

    def generate_difficulty(
        self,
        audio_features: Dict[str, Any],
        beat_grid: List[float],
        bpm: float,
        difficulty: str = "Expert",
        deterministic: bool = True,
    ) -> Dict[str, Any]:
        """Generate a complete v3.3.0 difficulty map using the RL policy."""
        env = BeatSaberEnv(
            audio_features=audio_features,
            beat_grid=beat_grid,
            bpm=bpm,
            difficulty=difficulty,
        )
        obs, _ = env.reset()

        color_notes: List[Dict[str, Any]] = []
        bomb_notes: List[Dict[str, Any]] = []
        obstacles: List[Dict[str, Any]] = []
        sliders: List[Dict[str, Any]] = []
        burst_sliders: List[Dict[str, Any]] = []

        total_steps = len(beat_grid)
        for step in range(total_steps):
            mask_red, mask_blue = env.action_masks()
            t_obs = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
            t_mask_r = torch.tensor(mask_red, dtype=torch.bool, device=self.device).unsqueeze(0)
            t_mask_b = torch.tensor(mask_blue, dtype=torch.bool, device=self.device).unsqueeze(0)

            act_red, act_blue = self.policy.predict(
                t_obs, mask_red=t_mask_r, mask_blue=t_mask_b, deterministic=deterministic
            )

            current_beat = round(beat_grid[step], 4)
            red_dec = decode_hand_action(act_red)
            blue_dec = decode_hand_action(act_blue)

            if red_dec is not None:
                rx, ry, rd = red_dec
                note_dict = {
                    "b": current_beat,
                    "c": 0,
                    "x": rx,
                    "y": ry,
                    "d": rd,
                    "a": 0,
                }
                color_notes.append(note_dict)

            if blue_dec is not None:
                bx, by, bd = blue_dec
                note_dict = {
                    "b": current_beat,
                    "c": 1,
                    "x": bx,
                    "y": by,
                    "d": bd,
                    "a": 0,
                }
                color_notes.append(note_dict)

            obs, _, term, _, _ = env.step((act_red, act_blue))
            if term:
                break

        # Generate Arcs (Sliders) connecting smooth swing trajectories (Post-2022 v3 feature)
        sliders = self._generate_arcs(color_notes)

        # Generate Chains (Burst Sliders) on sustained vibration onsets (Post-2022 v3 feature)
        burst_sliders = self._generate_chains(color_notes, audio_features, beat_grid)

        # Generate framing walls & bombs
        obstacles, bomb_notes = self._generate_obstacles_and_bombs(
            color_notes, beat_grid, audio_features, difficulty
        )

        # Generate v3 lightshow events
        basic_events, boost_events = self._generate_lighting(beat_grid, audio_features)

        # Sort notes deterministically by beat
        color_notes.sort(key=lambda n: (float(n["b"]), int(n["x"]), int(n["y"]), int(n["c"])))
        bomb_notes.sort(key=lambda b: (float(b["b"]), int(b["x"]), int(b["y"])))
        obstacles.sort(key=lambda o: (float(o["b"]), int(o["x"]), int(o["y"])))

        return {
            "version": "3.3.0",
            "colorNotes": color_notes,
            "bombNotes": bomb_notes,
            "obstacles": obstacles,
            "sliders": sliders,
            "burstSliders": burst_sliders,
            "basicBeatmapEvents": basic_events,
            "colorBoostBeatmapEvents": boost_events,
            "bpmEvents": [],
            "rotationEvents": [],
            "customData": {
                "_generator": "BeatForge-RL-v1",
                "_provenance": {
                    "poseSolver": "rl-policy-v1",
                    "format": "v3.3.0",
                },
            },
        }

    def _generate_arcs(self, color_notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate rich v3 Arcs / Sliders connecting held phrases and melodic transition swings."""
        arcs = []
        by_color: Dict[int, List[Dict[str, Any]]] = {0: [], 1: []}
        for n in color_notes:
            by_color[int(n["c"])].append(n)

        for c, notes in by_color.items():
            for i in range(len(notes) - 1):
                head = notes[i]
                tail = notes[i + 1]
                gap = float(tail["b"]) - float(head["b"])

                # Connect notes spaced 0.5 to 3.5 beats apart on melodic transitions
                if 0.5 <= gap <= 3.5 and head["d"] != 8 and tail["d"] != 8 and (i % 2 == 0):
                    control = min(2.0, max(0.8, gap * 0.45))
                    arcs.append({
                        "b": float(head["b"]),
                        "c": int(c),
                        "x": int(head["x"]),
                        "y": int(head["y"]),
                        "d": int(head["d"]),
                        "mu": round(control, 2),
                        "tb": float(tail["b"]),
                        "tx": int(tail["x"]),
                        "ty": int(tail["y"]),
                        "tc": int(tail["d"]),
                        "tmu": round(control, 2),
                        "m": 0,
                    })
        return arcs

    def _generate_chains(
        self,
        color_notes: List[Dict[str, Any]],
        audio_features: Dict[str, Any],
        beat_grid: List[float],
    ) -> List[Dict[str, Any]]:
        """Generate v3 burst sliders with rich official-grade variety (stutters, vertical sweeps, horizontal whips, diagonal slashes)."""
        chains = []
        onsets = audio_features.get("onsets", [])
        stems = audio_features.get("stems", {})
        drums = stems.get("drums", [])
        vocals = stems.get("vocals", [])
        guitar = stems.get("guitar", [])

        # Direction delta mapping: 0=Up, 1=Down, 2=Left, 3=Right, 4=Up-Left, 5=Up-Right, 6=Down-Left, 7=Down-Right, 8=Dot
        dir_deltas = {
            0: [(0, 1), (0, 2), (0, 0)],
            1: [(0, -1), (0, -2), (0, 0)],
            2: [(-1, 0), (-2, 0), (-1, 1), (-1, -1), (0, 0)],
            3: [(1, 0), (2, 0), (1, 1), (1, -1), (0, 0)],
            4: [(-1, 1), (-1, 2), (-2, 1), (0, 1), (0, 0)],
            5: [(1, 1), (1, 2), (2, 1), (0, 1), (0, 0)],
            6: [(-1, -1), (-1, -2), (-2, -1), (0, -1), (0, 0)],
            7: [(1, -1), (1, -2), (2, -1), (0, -1), (0, 0)],
            8: [(0, 1), (0, -1), (1, 0), (-1, 0), (0, 0)],
        }

        last_chain_beat = -999.0
        for i, note in enumerate(color_notes):
            b = float(note["b"])
            step_idx = min(int(b * 4), len(onsets) - 1)
            
            drum_val = float(drums[step_idx]) if step_idx >= 0 and step_idx < len(drums) and isinstance(drums, list) else 0.0
            vocal_val = float(vocals[step_idx]) if step_idx >= 0 and step_idx < len(vocals) and isinstance(vocals, list) else 0.0
            guitar_val = float(guitar[step_idx]) if step_idx >= 0 and step_idx < len(guitar) and isinstance(guitar, list) else 0.0
            
            # Lockout of 0.5 - 1.0 beats during dense sections, 1.5 beats during calm sections
            is_accent = (drum_val > 0.50 or vocal_val > 0.50 or guitar_val > 0.50)
            lockout = 0.5 if is_accent else 1.5
            
            if b - last_chain_beat >= lockout and is_accent:
                hx, hy = int(note["x"]), int(note["y"])
                c = int(note["c"])
                d = int(note["d"])
                
                # Pick next delta vector
                cand_deltas = dir_deltas.get(d, [(0, 0)])
                # Deterministic selection based on beat and index
                chosen_dx, chosen_dy = cand_deltas[(i + int(b * 2)) % len(cand_deltas)]
                
                # If color note is on boundary, clamp delta safely
                if c == 0 and chosen_dx > 0 and hx + chosen_dx > 2:
                    chosen_dx = 0
                elif c == 1 and chosen_dx < 0 and hx + chosen_dx < 1:
                    chosen_dx = 0
                    
                tx = min(3, max(0, hx + chosen_dx))
                ty = min(2, max(0, hy + chosen_dy))
                
                # Determine duration and slice count
                dt = 0.0625 if (tx == hx and ty == hy) else (0.125 if abs(chosen_dx) + abs(chosen_dy) <= 1 else 0.25)
                sc = 3 if dt <= 0.0625 else (4 if dt <= 0.125 else 5)
                
                chains.append({
                    "b": b,
                    "c": c,
                    "x": hx,
                    "y": hy,
                    "d": d,
                    "tb": round(b + dt, 4),
                    "tx": tx,
                    "ty": ty,
                    "sc": sc,
                    "s": 1.0,
                })
                last_chain_beat = b
        return chains

    def _generate_obstacles_and_bombs(
        self,
        color_notes: List[Dict[str, Any]],
        beat_grid: List[float],
        audio_features: Dict[str, Any],
        difficulty: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Generate musical, physically safe, and immersive post-2022 v3 obstacles and framing walls."""
        obstacles: List[Dict[str, Any]] = []
        bombs: List[Dict[str, Any]] = []

        if not beat_grid or len(beat_grid) < 16:
            return obstacles, bombs

        # Index color notes by beat intervals for fast collision / clearance checking
        notes_by_beat: Dict[float, List[Dict[str, Any]]] = {}
        for n in color_notes:
            b_val = round(float(n["b"]), 4)
            notes_by_beat.setdefault(b_val, []).append(n)

        def notes_in_window(start_b: float, end_b: float) -> List[Dict[str, Any]]:
            res = []
            for b_val, n_list in notes_by_beat.items():
                if start_b - 0.05 <= b_val <= end_b + 0.05:
                    res.extend(n_list)
            return res

        onsets = audio_features.get("onsets", [])
        stems = audio_features.get("stems", {})
        bass_energy = stems.get("bass", onsets) if isinstance(stems, dict) else onsets

        total_beats = beat_grid[-1] if beat_grid else 0.0
        last_wall_end = -8.0
        min_wall_gap = 12.0 if difficulty in ("Easy", "Normal") else 6.0

        # Iterate through downbeats (every 2 to 4 beats)
        for i, beat in enumerate(beat_grid):
            # Only consider on-beat musical divisions (e.g. 2-beat or 4-beat measure markers)
            if abs(beat - round(beat)) > 1e-4 or int(round(beat)) % 2 != 0:
                continue
            if beat < 4.0 or beat > total_beats - 4.0:
                continue
            if beat < last_wall_end + min_wall_gap:
                continue

            step_idx = min(int(beat * 4), len(onsets) - 1)
            intensity = onsets[step_idx] if 0 <= step_idx < len(onsets) else 0.5
            bass_val = bass_energy[step_idx] if 0 <= step_idx < len(bass_energy) else 0.5

            # 1. PERIMETER FRAMING WALLS (Side rails for atmosphere & tunnel speed)
            # Available across all difficulties. Placed on outer lanes (x=0 or x=3).
            if intensity > 0.45:
                # Check left rail (x=0, w=1, y=0, h=3)
                window_notes = notes_in_window(beat, beat + 2.0)
                left_blocked = any(int(n["x"]) == 0 for n in window_notes)
                right_blocked = any(int(n["x"]) == 3 for n in window_notes)

                if not left_blocked and not right_blocked:
                    # Dual side rail frame
                    duration = 2.0 if difficulty in ("Easy", "Normal") else 1.0
                    obstacles.append({
                        "b": float(beat),
                        "x": 0,
                        "y": 0,
                        "d": duration,
                        "w": 1,
                        "h": 3,
                    })
                    obstacles.append({
                        "b": float(beat),
                        "x": 3,
                        "y": 0,
                        "d": duration,
                        "w": 1,
                        "h": 3,
                    })
                    last_wall_end = beat + duration
                    continue
                elif not left_blocked:
                    obstacles.append({
                        "b": float(beat),
                        "x": 0,
                        "y": 0,
                        "d": 2.0,
                        "w": 1,
                        "h": 3,
                    })
                    last_wall_end = beat + 2.0
                    continue
                elif not right_blocked:
                    obstacles.append({
                        "b": float(beat),
                        "x": 3,
                        "y": 0,
                        "d": 2.0,
                        "w": 1,
                        "h": 3,
                    })
                    last_wall_end = beat + 2.0
                    continue

            # 2. CROUCH / DUCK WALLS (Ceiling shelf across center: x=1, w=2, y=2, h=2)
            # Active in Hard, Expert, ExpertPlus during heavy bass drops or breakdowns
            if difficulty in ("Hard", "Expert", "ExpertPlus") and bass_val > 0.65:
                crouch_notes = notes_in_window(beat - 0.25, beat + 2.25)
                # Strict safety: No notes in top rows (y >= 1) during the crouch tunnel
                high_notes = any(int(n["y"]) >= 1 for n in crouch_notes)
                if not high_notes:
                    duration = 2.0
                    obstacles.append({
                        "b": float(beat),
                        "x": 1,
                        "y": 2,
                        "d": duration,
                        "w": 2,
                        "h": 2,
                    })
                    last_wall_end = beat + duration
                    continue

            # 3. SIDE-STEP / LEAN DODGE WALLS (Expert / ExpertPlus only)
            # Left Dodge (x=0, w=2) forcing right, or Right Dodge (x=2, w=2) forcing left
            if difficulty in ("Expert", "ExpertPlus") and intensity > 0.6:
                dodge_notes = notes_in_window(beat - 0.25, beat + 1.75)
                # Check if all notes in the window are purely on the right (x in 2, 3)
                all_right = dodge_notes and all(int(n["x"]) in (2, 3) for n in dodge_notes)
                # Check if all notes in the window are purely on the left (x in 0, 1)
                all_left = dodge_notes and all(int(n["x"]) in (0, 1) for n in dodge_notes)

                if all_right:
                    # Place Left Dodge wall (covers x=0, 1)
                    obstacles.append({
                        "b": float(beat),
                        "x": 0,
                        "y": 0,
                        "d": 1.5,
                        "w": 2,
                        "h": 4,
                    })
                    last_wall_end = beat + 1.5
                    continue
                elif all_left:
                    # Place Right Dodge wall (covers x=2, 3)
                    obstacles.append({
                        "b": float(beat),
                        "x": 2,
                        "y": 0,
                        "d": 1.5,
                        "w": 2,
                        "h": 4,
                    })
                    last_wall_end = beat + 1.5
                    continue

        return obstacles, bombs

    def _generate_lighting(
        self,
        beat_grid: List[float],
        audio_features: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Generate rhythmic basic and boost lighting events."""
        basic_events = []
        boost_events = []

        onsets = audio_features.get("onsets", [])

        for i, beat in enumerate(beat_grid):
            # Downbeat back lasers / ring lights (Type 0, 1)
            if round(beat * 4) % 16 == 0:
                basic_events.append({
                    "b": float(beat),
                    "et": 0,  # Back Lasers
                    "i": 1,   # Blue On
                    "f": 1.0,
                })
                basic_events.append({
                    "b": float(beat),
                    "et": 1,  # Ring Lights
                    "i": 5,   # Red On
                    "f": 1.0,
                })
            # Boost events on intense drops
            if i < len(onsets) and onsets[i] > 0.8:
                boost_events.append({
                    "b": float(beat),
                    "o": True,
                })

        return basic_events, boost_events
