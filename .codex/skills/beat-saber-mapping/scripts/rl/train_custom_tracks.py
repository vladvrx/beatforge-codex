#!/usr/bin/env python3
"""Train on verified local analyzer artifacts using the inference feature contract."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import torch

from rl.checkpoints import load_checkpoint
from rl.environment import BeatSaberEnv
from rl.features import file_sha256, load_analysis_features
from rl.library import load_library
from rl.models import ActorCriticPolicy
from rl.train_ppo import train_ppo


def extract_track_features(audio_path: Path, analysis_dir: Path | None = None) -> tuple[dict[str, Any], list[float], float]:
    """Load existing analysis; raw audio must match its recorded source hash.

    A directory can be supplied directly. For an audio file, the default artifact
    directory is its sibling ``<stem>.analysis``. Analysis and human timing review
    run before training, so no background model downloads or fake clocks occur.
    """
    audio_path = Path(audio_path)
    directory = analysis_dir or (audio_path if audio_path.is_dir() else audio_path.with_suffix(".analysis"))
    bundle = load_analysis_features(directory)
    if audio_path.is_file() and file_sha256(audio_path) != bundle.provenance.get("sourceSha256"):
        raise ValueError(f"Analysis does not belong to audio file {audio_path.name}")
    if audio_path.is_file():
        bundle.provenance["sourceAudioVerified"] = True
    return bundle.audio_features, bundle.beat_grid, bundle.bpm


def train_on_custom_library(
    tracks: list[Path],
    timesteps_per_track: int = 16384,
    epochs: int = 4,
    lr: float = 2e-4,
    model_path: Path = Path("data/models/ppo_policy.pt"),
    *,
    rounds: int = 1,
    seed: int = 0,
    resume: bool = True,
    held_out_hashes: set[str] | None = None,
) -> dict[str, Any]:
    if not tracks or rounds <= 0:
        raise ValueError("Training requires tracks and a positive number of rounds")
    # Validate the entire library before updating any weights.
    prepared = [(Path(track), extract_track_features(Path(track))) for track in tracks]
    training_hashes: set[str] = set()
    for track, (features, _, _) in prepared:
        source = features["provenance"].get("sourceSha256")
        if not isinstance(source, str) or len(source) != 64:
            raise ValueError(f"Training analysis requires the audio source SHA-256: {track}")
        training_hashes.add(source)
    held_out_hashes = held_out_hashes or set()
    torch.manual_seed(seed)
    policy = ActorCriticPolicy()
    prior: dict[str, Any] = {}
    if resume and model_path.is_file():
        policy, prior = load_checkpoint(model_path)
        if not prior.get("trainingSourceSha256"):
            raise ValueError("Checkpoint has no training-library provenance. Use --no-resume for a traceable run.")
        training_hashes.update(prior["trainingSourceSha256"])
    if training_hashes & held_out_hashes:
        raise ValueError("Held-out audio appears in the checkpoint or requested training library")

    runs = []
    for round_index in range(rounds):
        for index, (track, (features, grid, bpm)) in enumerate(prepared):
            run_seed = seed + round_index * len(prepared) + index
            print(f"Round {round_index + 1}/{rounds}, track {index + 1}/{len(prepared)}: {track.name}", flush=True)
            env = BeatSaberEnv(features, grid, bpm, difficulty="Expert")
            metrics = train_ppo(
                policy, env, total_timesteps=timesteps_per_track,
                rollout_steps=min(512, timesteps_per_track), ppo_epochs=epochs,
                lr=lr, save_path=model_path, seed=run_seed,
                checkpoint_metadata={
                    "trainingSourceSha256": sorted(training_hashes),
                    "heldOutSourceSha256": sorted(held_out_hashes),
                    "librarySeed": seed, "round": round_index + 1,
                    "resumedCheckpointSha256": prior.get("checkpointSha256"),
                },
            )
            runs.append({"sourceSha256": features["provenance"]["sourceSha256"], **metrics})
    return {"seed": seed, "rounds": rounds, "runs": runs, "checkpoint": str(model_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--analysis-dir", action="append", type=Path, help="Repeat for verified analysis directories")
    source.add_argument("--manifest", type=Path, help="JSON song library with explicit train/validation/test splits")
    parser.add_argument("--timesteps-per-track", type=int, default=16384)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--out", type=Path, default=Path("data/models/ppo_policy.pt"))
    args = parser.parse_args()
    try:
        held_out: set[str] = set()
        tracks = args.analysis_dir or []
        if args.manifest:
            library = load_library(args.manifest)
            tracks = [Path(track["analysisDir"]) for track, _ in library if track["split"] == "train"]
            held_out = {bundle.provenance["sourceSha256"] for track, bundle in library if track["split"] != "train"}
        train_on_custom_library(
            tracks, args.timesteps_per_track, args.epochs, args.lr, args.out,
            rounds=args.rounds, seed=args.seed, resume=args.resume, held_out_hashes=held_out,
        )
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
