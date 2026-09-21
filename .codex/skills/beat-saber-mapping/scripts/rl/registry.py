"""Local checkpoint promotion with measured regression gates and rollback.

Promotion never trains, downloads, installs maps, or asserts human preference.
Stored checkpoints and reports remain immutable by content hash.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any

from rl.environment import TARGET_NPS_MAP
from rl.features import FEATURE_SCHEMA, file_sha256

PRIMARY_METRIC = "meanOnsetStrengthAtNotes"
MIN_PRIMARY_GAIN = 0.001


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _finite(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Evaluation is missing a finite metric: {key}")
    return float(value)


def _result(report: dict[str, Any], checkpoint_hash: str) -> dict[str, Any]:
    if (report.get("kind") != "offline-held-out-checkpoint-evaluation"
            or report.get("schemaVersion") != 1
            or report.get("featureSchema") != FEATURE_SCHEMA
            or report.get("heldOutVerifiedAgainstRecordedTraining") is not True
            or report.get("split") not in {"validation", "test"}
            or report.get("deterministic") is not True):
        raise ValueError("Promotion requires a verified held-out evaluation with the current schema")
    matches = [item for item in report.get("results", [])
               if item.get("checkpoint", {}).get("checkpointSha256") == checkpoint_hash]
    if len(matches) != 1:
        raise ValueError("Evaluation must identify the exact candidate checkpoint once")
    selected = matches[0]
    training = selected.get("checkpoint", {}).get("trainingSourceSha256")
    if not training or not selected.get("tracks"):
        raise ValueError("Evaluation must record training hashes and nonempty held-out tracks")
    seen: set[str] = set()
    for track in selected["tracks"]:
        if track["id"] in seen:
            raise ValueError("Evaluation repeats a held-out track ID")
        seen.add(track["id"])
        features = track.get("features", {})
        source = features.get("sourceSha256")
        if not source or source in training or not features.get("artifactSha256"):
            raise ValueError("Evaluation contains training overlap or lacks sample artifact identity")
        if features.get("timingStatus") != "timing_verified":
            raise ValueError("Promotion requires verified timing on every held-out track")
        metrics = track["metrics"]
        if _finite(metrics, "hardErrors") != 0:
            raise ValueError(f"Candidate has hard validation errors on {track['id']}")
        if _finite(metrics, "notes") <= 0:
            raise ValueError(f"Candidate has an empty chart on {track['id']}")
        for key in (PRIMARY_METRIC, "handImbalance", "mostRepeatedTransitionFraction", "nps", "warnings"):
            if _finite(metrics, key) < 0:
                raise ValueError(f"Metric cannot be negative: {key}")
    return selected


def compare_evaluations(
    baseline: dict[str, Any], baseline_hash: str,
    candidate: dict[str, Any], candidate_hash: str,
) -> dict[str, Any]:
    """Reject benchmark changes, regressions, and gains below the fixed threshold."""
    before = _result(baseline, baseline_hash)
    after = _result(candidate, candidate_hash)
    identity = ("manifestSha256", "featureSchema", "split", "difficulty", "seed", "deterministic", "runtime", "evaluatorSha256")
    if any(key not in baseline or key not in candidate or baseline[key] != candidate[key] for key in identity):
        raise ValueError("Benchmark identity differs; re-evaluate both checkpoints on the same benchmark")
    old = {track["id"]: track for track in before["tracks"]}
    new = {track["id"]: track for track in after["tracks"]}
    if old.keys() != new.keys():
        raise ValueError("Held-out track sets differ")
    target = TARGET_NPS_MAP.get(candidate["difficulty"])
    if target is None:
        raise ValueError("Unknown evaluation difficulty")
    gains = []
    for identity, current in new.items():
        previous = old[identity]
        if current["features"] != previous["features"]:
            raise ValueError(f"Held-out audio analysis changed: {identity}")
        a, b = previous["metrics"], current["metrics"]
        for key in ("handImbalance", "mostRepeatedTransitionFraction", "warnings"):
            if _finite(b, key) > _finite(a, key) + 1e-9:
                raise ValueError(f"Regression in {key} on {identity}")
        if abs(_finite(b, "nps") - target) > abs(_finite(a, "nps") - target) + 1e-9:
            raise ValueError(f"Regression in target density on {identity}")
        gain = _finite(b, PRIMARY_METRIC) - _finite(a, PRIMARY_METRIC)
        if gain < -1e-9:
            raise ValueError(f"Regression in onset alignment on {identity}")
        gains.append(gain)
    mean_gain = sum(gains) / len(gains)
    if mean_gain < MIN_PRIMARY_GAIN:
        raise ValueError(f"Primary metric must improve by at least {MIN_PRIMARY_GAIN}")
    return {"primaryMetric": PRIMARY_METRIC, "meanGain": mean_gain,
            "minimumGain": MIN_PRIMARY_GAIN, "tracksCompared": len(gains),
            "humanPreferenceEstablished": False}


@contextmanager
def _locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".registry.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("Registry is locked by another operation; retry after it finishes") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def _save_registry(root: Path, state: dict[str, Any]) -> None:
    temporary = root / "registry.json.tmp"
    temporary.write_text(json.dumps(state, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(root / "registry.json")


def _store_artifact(source: Path, destination: Path, expected: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if file_sha256(destination) != expected:
            raise ValueError("Stored registry artifact failed its content hash")
        return
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    if file_sha256(temporary) != expected:
        temporary.unlink()
        raise ValueError("Artifact changed during registry import")
    temporary.replace(destination)


def register_checkpoint(root: Path, checkpoint: Path, evaluation: Path, *, initialize: bool = False) -> dict[str, Any]:
    """Install a baseline explicitly, or promote a measured improvement atomically."""
    from rl.checkpoints import load_checkpoint

    root, checkpoint, evaluation = Path(root), Path(checkpoint), Path(evaluation)
    _, metadata = load_checkpoint(checkpoint)
    digest = metadata["checkpointSha256"]
    report = _read(evaluation)
    report_hash = file_sha256(evaluation)
    selected = _result(report, digest)
    if selected["checkpoint"].get("trainingSourceSha256") != metadata.get("trainingSourceSha256"):
        raise ValueError("Evaluation training lineage does not match checkpoint metadata")
    with _locked(root):
        state = _read(root / "registry.json") if (root / "registry.json").exists() else {"schemaVersion": 1, "active": None, "models": {}, "history": []}
        active = state["active"]
        if initialize:
            if active is not None:
                raise ValueError("Registry already has a baseline; use promote")
            decision = {"baselineInitialized": True, "humanPreferenceEstablished": False}
        else:
            if active is None:
                raise ValueError("Initialize an evaluated baseline before promotion")
            if active == digest:
                raise ValueError("Candidate is already the active checkpoint")
            previous = state["models"][active]
            baseline_path = root / previous["evaluation"]
            if file_sha256(baseline_path) != previous["evaluationSha256"]:
                raise ValueError("Stored baseline evaluation changed")
            decision = compare_evaluations(_read(baseline_path), active, report, digest)
        checkpoint_name = f"checkpoints/{digest}.pt"
        evaluation_name = f"evaluations/{report_hash}.json"
        _store_artifact(checkpoint, root / checkpoint_name, digest)
        _store_artifact(evaluation, root / evaluation_name, report_hash)
        state["models"][digest] = {"checkpoint": checkpoint_name, "evaluation": evaluation_name,
                                   "evaluationSha256": report_hash, "metadata": metadata}
        state["history"].append({"action": "initialize" if initialize else "promote", "from": active, "to": digest,
                                 "at": datetime.now(timezone.utc).isoformat(), "decision": decision})
        state["active"] = digest
        _save_registry(root, state)
    return {"active": digest, "previous": active, "checkpoint": str((root / checkpoint_name).resolve()), "decision": decision}


def active_checkpoint(root: Path) -> Path:
    root = Path(root)
    state = _read(root / "registry.json")
    digest = state.get("active")
    if not digest:
        raise ValueError("Registry has no active checkpoint")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Registry contains an invalid checkpoint identity")
    path = root / "checkpoints" / f"{digest}.pt"
    if file_sha256(path) != digest:
        raise ValueError("Active checkpoint failed its recorded content hash")
    return path


def registry_status(root: Path) -> dict[str, Any]:
    """Compact read-only studio status; does not import PyTorch or expose paths."""
    root = Path(root)
    if not (root / "registry.json").is_file():
        return {"status": "empty", "active": None, "modelCount": 0, "automaticTraining": False}
    try:
        state = _read(root / "registry.json")
        active_checkpoint(root)
        model = state["models"][state["active"]]
        metadata = model.get("metadata", {})
        history = state.get("history", [])
        return {
            "status": "ready", "active": state["active"], "modelCount": len(state["models"]),
            "featureSchema": metadata.get("featureSchema"),
            "trainingTrackCount": len(metadata.get("trainingSourceSha256", [])),
            "lastAction": history[-1] if history else None,
            "automaticTraining": False, "humanPreferenceEstablished": False,
        }
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {"status": "invalid", "active": None, "automaticTraining": False,
                "error": "Registry or stored checkpoint integrity check failed"}


def rollback(root: Path) -> dict[str, Any]:
    root = Path(root)
    with _locked(root):
        state = _read(root / "registry.json")
        active = state["active"]
        previous = next((entry["from"] for entry in reversed(state["history"])
                         if entry["to"] == active and entry["from"] is not None), None)
        if previous is None:
            raise ValueError("No previous active checkpoint exists")
        path = root / "checkpoints" / f"{previous}.pt"
        if file_sha256(path) != previous:
            raise ValueError("Rollback checkpoint failed its recorded content hash")
        state["active"] = previous
        state["history"].append({"action": "rollback", "from": active, "to": previous,
                                 "at": datetime.now(timezone.utc).isoformat()})
        _save_registry(root, state)
    return {"active": previous, "previous": active, "checkpoint": str(path.resolve())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("initialize", "promote"):
        sub = commands.add_parser(command)
        sub.add_argument("--checkpoint", type=Path, required=True)
        sub.add_argument("--evaluation", type=Path, required=True)
    commands.add_parser("rollback")
    commands.add_parser("active")
    args = parser.parse_args()
    try:
        if args.command == "active":
            result = {"checkpoint": str(active_checkpoint(args.registry).resolve())}
        elif args.command == "rollback":
            result = rollback(args.registry)
        else:
            result = register_checkpoint(args.registry, args.checkpoint, args.evaluation, initialize=args.command == "initialize")
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
