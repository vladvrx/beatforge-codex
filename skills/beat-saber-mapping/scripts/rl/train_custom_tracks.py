#!/usr/bin/env python3
"""Train RL Policy on User Tracks: Daft Punk R.A.M and Ninajirachi."""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import torch

try:
    from .environment import BeatSaberEnv
    from .models import ActorCriticPolicy
    from .train_ppo import train_ppo
except ImportError:
    from rl.environment import BeatSaberEnv
    from rl.models import ActorCriticPolicy
    from rl.train_ppo import train_ppo

from beatforge_core import AudioBuffer, frame_features, load_audio


def extract_track_features(audio_path: Path) -> Tuple[Dict[str, Any], List[float], float]:
    """Extract audio features, beat grid, and BPM for RL environment training."""
    print(f"Extracting features from {audio_path.name}...", flush=True)
    audio = load_audio(audio_path)
    features = frame_features(audio.samples)
    hop = int(features["hop"][0])
    sr = audio.sample_rate

    flux = features["flux"]
    bpm_est = 120.0  # default fallback

    # Compute rough tempo / beat grid
    length = len(flux)
    grid_len = max(32, int(audio.duration_samples / (sr * 0.5)))
    beat_grid = [round(i * 0.25, 4) for i in range(grid_len)]

    # Interpolate flux into onsets
    onsets = np.interp(np.linspace(0, len(flux), grid_len), np.arange(len(flux)), flux)
    onsets = (onsets - np.min(onsets)) / (np.max(onsets) - np.min(onsets) + 1e-6)

    # Simulated stem profiles based on transient frequency bands
    audio_features = {
        "onsets": onsets.tolist(),
        "flux": onsets.tolist(),
        "stems": {
            "drums": (onsets * 0.8 + np.random.uniform(0.0, 0.2, grid_len)).tolist(),
            "bass": (onsets * 0.6 + np.random.uniform(0.0, 0.4, grid_len)).tolist(),
            "vocals": (onsets * 0.5 + np.random.uniform(0.0, 0.3, grid_len)).tolist(),
            "guitar": (onsets * 0.4 + np.random.uniform(0.0, 0.2, grid_len)).tolist(),
            "piano": (onsets * 0.3 + np.random.uniform(0.0, 0.2, grid_len)).tolist(),
            "other": (onsets * 0.3 + np.random.uniform(0.0, 0.2, grid_len)).tolist(),
        },
        "sections": ["verse"] * grid_len,
    }

    return audio_features, beat_grid, bpm_est


def train_on_custom_library(
    tracks: List[Path],
    timesteps_per_track: int = 16384,
    epochs: int = 4,
    lr: float = 2e-4,
    model_path: Path = Path("data/models/ppo_policy.pt"),
) -> None:
    policy = ActorCriticPolicy()
    if model_path.is_file():
        try:
            policy.load_state_dict(torch.load(model_path, map_location="cpu"))
            print(f"Resumed existing weights from {model_path}", flush=True)
        except Exception as e:
            print(f"Could not load checkpoint: {e}", flush=True)

    print(f"\n=======================================================", flush=True)
    print(f" Starting Continuous RL Training on {len(tracks)} User Tracks", flush=True)
    print(f"=======================================================\n", flush=True)

    for round_idx in range(1, 100):
        print(f"\n--- Training Round {round_idx} Across Library ---", flush=True)
        for idx, track_path in enumerate(tracks, 1):
            if not track_path.is_file():
                continue
            print(f"\n[{idx}/{len(tracks)}] Processing: {track_path.name}", flush=True)
            try:
                audio_feats, beat_grid, bpm = extract_track_features(track_path)
                env = BeatSaberEnv(
                    audio_features=audio_feats,
                    beat_grid=beat_grid,
                    bpm=bpm,
                    difficulty="Expert",
                )
                train_ppo(
                    policy=policy,
                    env=env,
                    total_timesteps=timesteps_per_track,
                    rollout_steps=512,
                    ppo_epochs=epochs,
                    lr=lr,
                    save_path=model_path,
                )
            except Exception as e:
                print(f"Error training on {track_path.name}: {e}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train RL policy on specific tracks")
    parser.add_argument("--ram-dir", type=Path, default=Path(r"C:\Users\user\Downloads\R.A.M"))
    parser.add_argument("--ninajirachi-dir", type=Path, default=Path(r"data\downloads\ninajirachi"))
    parser.add_argument("--spotify-dir", type=Path, default=Path(r"data\downloads\spotify_july"))
    parser.add_argument("--timesteps-per-track", type=int, default=16384)
    parser.add_argument("--out", type=Path, default=Path("data/models/ppo_policy.pt"))
    args = parser.parse_args()

    tracks: List[Path] = []
    if args.ram_dir.is_dir():
        for ext in ("*.mp3", "*.ogg", "*.wav", "*.m4a"):
            tracks.extend(sorted(args.ram_dir.glob(ext)))
    
    downloads_root = Path("data/downloads")
    if downloads_root.is_dir():
        for ext in ("**/*.mp3", "**/*.ogg", "**/*.wav", "**/*.webm", "**/*.m4a"):
            tracks.extend(sorted(downloads_root.glob(ext)))

    # Deduplicate paths
    unique_tracks = []
    seen = set()
    for p in tracks:
        res = p.resolve()
        if res not in seen and p.is_file():
            seen.add(res)
            unique_tracks.append(p)
    tracks = unique_tracks

    print(f"Found {len(tracks)} training tracks:")
    for t in tracks:
        print(f" - {t.name}")

    if not tracks:
        print("No audio tracks found!")
        return 1

    train_on_custom_library(
        tracks=tracks,
        timesteps_per_track=args.timesteps_per_track,
        model_path=args.out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
