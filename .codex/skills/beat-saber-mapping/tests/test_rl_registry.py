"""Promotion requires the same benchmark, measured progress, and no regressions."""

import copy
import json

import pytest

from rl.features import FEATURE_SCHEMA
from rl.registry import active_checkpoint, compare_evaluations, register_checkpoint, rollback, registry_status


def evaluation(checkpoint_hash, onset=.5):
    return {
        "schemaVersion": 1, "kind": "offline-held-out-checkpoint-evaluation",
        "featureSchema": FEATURE_SCHEMA, "heldOutVerifiedAgainstRecordedTraining": True,
        "split": "validation", "difficulty": "Expert", "seed": 0, "deterministic": True,
        "manifestSha256": "d" * 64, "runtime": {"torch": "test", "numpy": "test"},
        "evaluatorSha256": {"evaluate.py": "e" * 64},
        "results": [{"checkpoint": {"checkpointSha256": checkpoint_hash, "trainingSourceSha256": ["a" * 64]},
                     "tracks": [{"id": "held-out", "features": {
                         "sourceSha256": "b" * 64, "timingStatus": "timing_verified",
                         "artifactSha256": {"beat_grid.json": "c" * 64},
                     }, "metrics": {"hardErrors": 0, "notes": 100, "nps": 4.25,
                                      "handImbalance": .1, "mostRepeatedTransitionFraction": .1,
                                      "warnings": 0, "meanOnsetStrengthAtNotes": onset}}]}],
    }


def test_promotion_requires_primary_gain_and_preserves_other_metrics():
    result = compare_evaluations(evaluation("before"), "before", evaluation("after", .6), "after")
    assert result["meanGain"] == pytest.approx(.1)
    assert result["humanPreferenceEstablished"] is False


@pytest.mark.parametrize("key,value", [
    ("hardErrors", 1), ("notes", 0), ("handImbalance", .2),
    ("mostRepeatedTransitionFraction", .3), ("warnings", 1), ("nps", 7),
    ("meanOnsetStrengthAtNotes", .5), ("meanOnsetStrengthAtNotes", float("nan")),
])
def test_regression_or_no_improvement_cannot_promote(key, value):
    candidate = evaluation("after", .7)
    candidate["results"][0]["tracks"][0]["metrics"][key] = value
    with pytest.raises(ValueError):
        compare_evaluations(evaluation("before"), "before", candidate, "after")


@pytest.mark.parametrize("field,value", [("manifestSha256", "different"), ("seed", 4),
                                         ("difficulty", "Hard"), ("evaluatorSha256", {})])
def test_changed_benchmark_cannot_promote(field, value):
    candidate = evaluation("after", .7)
    candidate[field] = value
    with pytest.raises(ValueError, match="Benchmark identity"):
        compare_evaluations(evaluation("before"), "before", candidate, "after")


def test_one_improved_track_cannot_hide_a_regression_elsewhere():
    baseline = evaluation("before")
    second = copy.deepcopy(baseline["results"][0]["tracks"][0])
    second["id"] = "second"
    second["features"]["sourceSha256"] = "f" * 64
    baseline["results"][0]["tracks"].append(second)
    candidate = copy.deepcopy(baseline)
    candidate["results"][0]["checkpoint"]["checkpointSha256"] = "after"
    candidate["results"][0]["tracks"][0]["metrics"]["meanOnsetStrengthAtNotes"] = 1
    candidate["results"][0]["tracks"][1]["metrics"]["meanOnsetStrengthAtNotes"] = .4
    with pytest.raises(ValueError, match="Regression in onset alignment"):
        compare_evaluations(baseline, "before", candidate, "after")


def test_registry_promotion_and_rollback_preserve_checkpoint_files(tmp_path):
    torch = pytest.importorskip("torch")
    from rl.checkpoints import load_checkpoint, save_checkpoint
    from rl.models import ActorCriticPolicy
    paths = []
    for seed in (1, 2, 3):
        torch.manual_seed(seed)
        path = tmp_path / f"policy-{seed}.pt"
        save_checkpoint(ActorCriticPolicy(hidden_dim=16), path, {"trainingSourceSha256": ["a" * 64]})
        _, metadata = load_checkpoint(path)
        report_path = tmp_path / f"report-{seed}.json"
        report_path.write_text(json.dumps(evaluation(metadata["checkpointSha256"], .4 + seed / 10)))
        paths.append((path, report_path))
    registry = tmp_path / "registry"
    baseline = register_checkpoint(registry, *paths[0], initialize=True)
    previous_bytes = active_checkpoint(registry).read_bytes()
    promoted = register_checkpoint(registry, *paths[1])
    assert promoted["previous"] == baseline["active"]
    assert active_checkpoint(registry).read_bytes() == paths[1][0].read_bytes()
    status = registry_status(registry)
    assert status["status"] == "ready"
    assert status["modelCount"] == 2
    assert status["automaticTraining"] is False
    assert (registry / "checkpoints" / f"{baseline['active']}.pt").read_bytes() == previous_bytes
    reverted = rollback(registry)
    assert reverted["active"] == baseline["active"]
    assert active_checkpoint(registry).read_bytes() == previous_bytes
    assert len(list((registry / "checkpoints").glob("*.pt"))) == 2
    # A rejected promotion must not change active registry state.
    bad = evaluation(load_checkpoint(paths[2][0])[1]["checkpointSha256"], .9)
    bad["results"][0]["tracks"][0]["metrics"]["hardErrors"] = 1
    paths[2][1].write_text(json.dumps(bad))
    state_before = (registry / "registry.json").read_bytes()
    with pytest.raises(ValueError, match="hard validation"):
        register_checkpoint(registry, *paths[2])
    assert (registry / "registry.json").read_bytes() == state_before


def test_registry_rejects_tampered_active_artifact(tmp_path):
    registry = tmp_path / "registry"
    (registry / "checkpoints").mkdir(parents=True)
    digest = "a" * 64
    (registry / "registry.json").write_text(json.dumps({"active": digest}))
    (registry / "checkpoints" / f"{digest}.pt").write_bytes(b"wrong content")
    with pytest.raises(ValueError, match="content hash"):
        active_checkpoint(registry)
    assert registry_status(registry)["status"] == "invalid"


def test_uninitialized_registry_has_honest_status(tmp_path):
    assert registry_status(tmp_path / "missing") == {
        "status": "empty", "active": None, "modelCount": 0, "automaticTraining": False,
    }
