# RL training, evaluation, and checkpoint promotion

RL remains experimental. The studio's constraint solver is the default. A saved
PPO reward is not proof of musical quality, and generated maps still require the
normal package validator and human headset checks.

Set `PYTHONPATH` to `skills/beat-saber-mapping/scripts` and use the project's Python
environment. PyTorch is an optional dependency. These commands operate on local
files and do not download models, analyze new audio, or install songs.

## Prepare a song library

Run `analyze_audio.py` and resolve the normal timing gate first. Every song needs
`analysis.json`, `beat_grid.json`, `sections.json`, and `audio_features.npz`.
Training and evaluation require `timing_verified`. The studio's explicit
unconfirmed-generation override is the only caller that disables this check,
and its feature provenance continues to say `needs_anchors`.

Use a manifest outside the repository for your private audio library:

```json
{
  "schemaVersion": 1,
  "tracks": [
    {"id": "training-song", "split": "train", "analysisDir": "analysis/training-song"},
    {"id": "validation-song", "split": "validation", "analysisDir": "analysis/validation-song"},
    {"id": "final-test-song", "split": "test", "analysisDir": "analysis/final-test-song"}
  ]
}
```

Paths resolve relative to the manifest. IDs and analyzed audio SHA-256 hashes
must be unique across the entire library. Use varied songs and keep the final
test split untouched while selecting models. Audio, analysis, maps, checkpoints,
and evaluation outputs stay out of version control.

## Train with the same inputs used at generation

```text
python -m rl.train_custom_tracks --manifest library.json --rounds 1 --timesteps-per-track 16384 --seed 42 --out data/models/candidate.pt
python -m rl.train_ppo --analysis-dir analysis/song --timesteps 16384 --seed 42 --out data/models/single-song.pt
```

`--analysis-dir` can be repeated for library training without a manifest.
`--no-resume` explicitly starts fresh. Resuming a real-song run from a checkpoint
without recorded training hashes is rejected because held-out independence
cannot be established. Old `--ram-dir`, `--ninajirachi-dir`, and `--spotify-dir`
discovery was replaced by explicit artifact directories or a manifest.

The training loop uses the requested number of transitions exactly. Full song
duration stays in the environment; a short requested training budget may still
cover only part of a song. The default is one library round. It no longer silently
performs 99 rounds. Seeds cover initialization, policy sampling, and batch order.
Repeatability applies to the same device and runtime versions.

The second hand's action mask uses the first hand's selected note. Simultaneous
handclaps, intersecting saber paths, and center vision doubles are masked during
PPO collection and inference. PPO stores that conditional mask for replay, so
the action probability used during optimization matches collection. This mask
does not replace validation of holds, walls, bombs, and the final package.

For a software smoke test, use the explicit `--synthetic` option on `train_ppo`.
That pulse has no separated stems or verified song timing and cannot establish
held-out song performance.

`rl.features.load_analysis_features(path)` returns `AnalysisFeatures` with
`audio_features`, `beat_grid`, `bpm`, `provenance`, and the original `analysis`.
The schema is `beatforge-rl-sample-grid-v2`. Quarter-beat decision samples come
from the monotonic integer sample grid, including offsets and tempo changes.
Frame interpolation uses saved sample positions, seconds, or `hopSamples`.
`sections.json.sections` supplies interval labels. Sample and second boundaries
take precedence over derived beat fields. Real Demucs event `stemEnergy`
annotations enter their nearest decision step. Missing stems remain zero and
`stemAvailability` records which stems actually exist. Missing spectral flux is
zero with `fluxAvailable: false`; the mix onset envelope is never relabeled as a
separated instrument. The 69-value policy observation layout remains compatible;
availability flags and provenance are metadata, while section labels influence
the musical reward.

Checkpoints contain `state_dict` and JSON-compatible `metadata`, including the
feature schema, seed, training audio hashes, and analysis provenance. Saves use
an atomic replacement. Inference uses `weights_only=True`, checks tensor shapes
and finite values, and records the exact checkpoint SHA-256. Legacy raw state
dicts can still be loaded explicitly for generation with
`featureSchema: legacy-unrecorded`. They cannot enter a verified held-out
comparison. A missing or incompatible checkpoint never falls back to a random
policy. Callers supplying a policy instance directly are marked
`source: supplied-policy`, for tests or explicit application integration.

## Compare on held-out songs

```text
python -m rl.evaluate --manifest library.json --checkpoint data/models/baseline.pt --checkpoint data/models/candidate.pt --split validation --difficulty Expert --seed 42 --out data/evaluations/comparison.json
```

The command checks checkpoint training hashes against held-out audio hashes,
generates each chart deterministically, projects it onto the exact audio clock,
and runs the shared chart validator. It reports hard errors, warning codes,
NPS, hand imbalance, repeated transitions, section density, and the mean onset
strength at note positions. Empty charts count as failures. The report includes
sample artifact hashes, checkpoint hashes, evaluator code hashes, runtime
versions, chart hashes, and the seed. Exit status 2 means a generated chart has
hard errors; the JSON report is still saved.

These measurements do not replace packaged-map checks or human evaluation.
Blind preferences, manual corrections, and headset playtests are separate
evidence. No generated report claims those checks happened.

## Promote and roll back

```text
python -m rl.registry --registry data/model_registry initialize --checkpoint data/models/baseline.pt --evaluation data/evaluations/comparison.json
python -m rl.registry --registry data/model_registry promote --checkpoint data/models/candidate.pt --evaluation data/evaluations/comparison.json
python -m rl.registry --registry data/model_registry active
python -m rl.registry --registry data/model_registry rollback
```

Initialization explicitly selects a validated, nonempty baseline. Promotion
requires identical manifest, split, song analysis hashes, difficulty, seed,
evaluator code, feature schema, and runtime versions. Each candidate chart must
have zero hard errors and no increase in warnings, hand imbalance, repeated
transition fraction, or distance from the difficulty's target NPS. No held-out
song may regress in onset alignment, and the mean onset-strength gain must be
at least 0.001. This is a measured candidate improvement, not proof that humans
prefer it. If the benchmark or evaluator changes, evaluate both models again
and explicitly establish a new registry baseline.

The registry copies checkpoints and evaluations into folders named by their
content hashes. `registry.json` contains `schemaVersion`, `active`, `models`,
and an append-only `history` of selections. Promotion atomically switches the
active reference and retains prior files for rollback. It does not delete or
overwrite the source checkpoints. Concurrent writes fail with a lock error.
`active_checkpoint(registry_root)` verifies the active file's hash before
returning its path. A feedback-triggered local job can call
`register_checkpoint(root, checkpoint, evaluation)` after an explicitly run
training/evaluation cycle; this module starts no background work.

## Focused checks

```text
python -m pytest skills/beat-saber-mapping/tests/test_rl_features.py skills/beat-saber-mapping/tests/test_rl_training_correctness.py skills/beat-saber-mapping/tests/test_rl_registry.py skills/beat-saber-mapping/tests/test_rl_environment.py
```

Tests cover offset and tempo-change sampling, localized real stems, missing
inputs, section intervals, train/eval content leakage, checkpoint corruption,
terminal PPO bootstrap boundaries, exact budgets, seeds, promotion rejection,
and artifact-preserving rollback. They use small synthetic fixtures rather than
training on or redistributing private song audio.
