#!/usr/bin/env python3
"""Offline held-out checkpoint evaluation. No downloads, training, or installation."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from beatforge_core import ValidationReport
from validate_map import validate_v3
from timing_export import project_map_timing
from rl.features import AnalysisFeatures, FEATURE_SCHEMA, file_sha256
from rl.library import load_library


def chart_metrics(chart: dict[str, Any], bundle: AnalysisFeatures, difficulty: str) -> dict[str, Any]:
    """Report measurable chart properties, without assigning a subjective score."""
    report = ValidationReport()
    exported = project_map_timing(chart, bundle.analysis)
    structural = validate_v3(exported, f"{difficulty}Standard.dat", report, bpm=bundle.bpm, difficulty=difficulty)
    notes = sorted(chart.get("colorNotes", []), key=lambda note: (note["b"], note["c"]))
    if not notes:
        report.add("error", "EMPTY_CHART", "A playable chart must contain color notes")
    duration = bundle.provenance["durationSamples"] / bundle.provenance["sampleRate"]
    hand_counts = Counter(int(note["c"]) for note in notes)
    transitions = []
    for color in (0, 1):
        hand = [note for note in notes if note["c"] == color]
        transitions.extend((left["x"], left["y"], left["d"], right["x"], right["y"], right["d"])
                           for left, right in zip(hand, hand[1:]))
    pattern_counts = Counter(transitions)
    labels = bundle.audio_features["sections"]
    times = bundle.audio_features["timesSeconds"]
    note_onsets = []
    section_counts: Counter[str] = Counter()
    for note in notes:
        index = int(np.clip(np.searchsorted(bundle.beat_grid, note["b"]), 0, len(labels) - 1))
        section_counts[labels[index]] += 1
        note_onsets.append(bundle.audio_features["onsets"][index])
    section_seconds: Counter[str] = Counter()
    for index, label in enumerate(labels):
        end = times[index + 1] if index + 1 < len(times) else duration
        section_seconds[label] += max(0.0, end - times[index])
    return {
        "notes": len(notes), "nps": len(notes) / duration,
        "redNotes": hand_counts[0], "blueNotes": hand_counts[1],
        "handImbalance": abs(hand_counts[0] - hand_counts[1]) / max(1, len(notes)),
        "mostRepeatedTransitionFraction": max(pattern_counts.values(), default=0) / max(1, len(transitions)),
        "meanOnsetStrengthAtNotes": float(np.mean(note_onsets)) if note_onsets else 0.0,
        "sectionNps": {label: section_counts[label] / seconds for label, seconds in section_seconds.items() if seconds > 0},
        "hardErrors": len(report.errors), "warnings": len(report.warnings),
        "errorCodes": dict(Counter(issue.code for issue in report.errors)),
        "structural": structural,
        "headsetPlaytest": "not_performed",
    }


def evaluate_checkpoints(
    manifest: Path, checkpoints: list[Path], *, split: str = "validation",
    difficulty: str = "Expert", seed: int = 0,
) -> dict[str, Any]:
    from rl.policy_generator import RLMapGenerator
    import torch

    if split not in {"validation", "test"}:
        raise ValueError("Evaluation must use a held-out validation or test split")
    library = load_library(manifest)
    held_out = [(track, bundle) for track, bundle in library if track["split"] == split]
    if not held_out:
        raise ValueError(f"Manifest has no {split} tracks")
    results = []
    for checkpoint in checkpoints:
        generator = RLMapGenerator(model_path=Path(checkpoint))
        training = generator.checkpoint_metadata.get("trainingSourceSha256")
        if not training:
            raise ValueError("Checkpoint has no training audio hashes; held-out independence cannot be verified")
        if generator.checkpoint_metadata.get("featureSchema") != FEATURE_SCHEMA:
            raise ValueError("Held-out comparison requires the current shared feature schema")
        if set(training) & {bundle.provenance["sourceSha256"] for _, bundle in held_out}:
            raise ValueError("Held-out split contains audio used to train this checkpoint")
        tracks = []
        for track, bundle in held_out:
            chart = generator.generate_difficulty(bundle.audio_features, bundle.beat_grid, bundle.bpm,
                                                  difficulty=difficulty, deterministic=True, seed=seed)
            tracks.append({"id": track["id"], "features": bundle.provenance,
                           "chartSha256": hashlib.sha256(json.dumps(chart, sort_keys=True).encode()).hexdigest(),
                           "metrics": chart_metrics(chart, bundle, difficulty)})
        results.append({
            "checkpoint": generator.checkpoint_metadata, "tracks": tracks,
            "summary": {"tracks": len(tracks), "hardErrors": sum(t["metrics"]["hardErrors"] for t in tracks),
                        "meanNps": float(np.mean([t["metrics"]["nps"] for t in tracks])),
                        "tracksWithoutHardErrors": sum(t["metrics"]["hardErrors"] == 0 for t in tracks)},
        })
    return {
        "schemaVersion": 1, "kind": "offline-held-out-checkpoint-evaluation",
        "createdAt": datetime.now(timezone.utc).isoformat(), "seed": seed,
        "deterministic": True, "split": split, "difficulty": difficulty,
        "manifestSha256": file_sha256(manifest), "featureSchema": FEATURE_SCHEMA,
        "runtime": {"torch": str(torch.__version__), "numpy": str(np.__version__)},
        "evaluatorSha256": {
            name: file_sha256(Path(__file__).parent / name)
            for name in ("evaluate.py", "features.py", "environment.py", "policy_generator.py", "models.py", "rewards.py", "safety.py")
        } | {
            name: file_sha256(Path(__file__).parent.parent / name)
            for name in ("validate_map.py", "kinematics.py", "safety_contract.py", "timing_export.py", "beatforge_core.py")
        },
        "heldOutVerifiedAgainstRecordedTraining": True,
        "limitations": ["Recorded hashes establish data separation; they do not prove prior undocumented training history.",
                        "Chart validation does not replace a complete packaged-map or headset playtest.",
                        "Musical preference and manual correction counts require human evaluation."],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, action="append", required=True)
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--difficulty", choices=("Easy", "Normal", "Hard", "Expert", "ExpertPlus"), default="Expert")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = evaluate_checkpoints(args.manifest, args.checkpoint, split=args.split, difficulty=args.difficulty, seed=args.seed)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Saved {len(result['results'])} checkpoint evaluation(s) to {args.out}")
    return 2 if any(item["summary"]["hardErrors"] for item in result["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
