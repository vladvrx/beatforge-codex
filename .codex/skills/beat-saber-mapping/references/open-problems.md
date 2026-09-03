# Open pipeline problems

These are engineering debts, not reasons to loosen gates. Pacific Coast Highway is the operator’s current headset song. Reproduce every defect on synthetic audio, a second licensed track, or official `Magic` before calling a fix done.

## Timing

- Mix clocks are Beat This (primary) and BeatNet+ (challenger). All-In-One must not veto the grid. 10 ms median / 20 ms p95 / count-ratio / drift still apply to that pair. Status stays `needs_anchors` when they disagree.
- Auto-timing should be propose-and-verify: drums + change-points propose piecewise `Anchor(beat, sample)`, an independent residual plus click start/mid/end verifies, then write anchors only on pass. Cloud audio models are not the beat clock.
- Info.dat BPM is an authored spine. It can differ from a human map of the same song (example: one playtest OGG used 120 in BeatForge and 117.454 in a human Expert+). Tempo field is not “too easy.” Sparse intents and NJS are.
- FFmpeg decode/encode now emit `BEATFORGE_PROGRESS` percent. Keep duration parsing honest if FFmpeg progress units change.
- Stem split must be Demucs **`htdemucs_6s`** (`demucs==4.0.1`), not karaoke two-stem. Record the loaded model in analysis/provenance. Four-stem `htdemucs` is fallback only.
- Mix onsets now get `layer` / `stemEnergy` from those WAVs. Stem beat clocks still must not replace mix consensus. Kick vs snare vs hats remains out of scope.

## Density and Expert+

- Human Expert+ on a dense electronic track can sit near NJS 18 with far more notes than the generator’s Expert+ (`min_gap` 0.25, onset quantile ~0.20). Raising density must still pass joint SAT, occupancy, and kinematics.
- Do not clone official or human event sequences. Retrieve corpus *features*, not note lists.
- Keep walls and dots when the operator likes them; do not strip them to raise NPS.

## Lighting

- `build_lighting` uses a section-aware beat clock plus onset/stem accents (including hits that did not become notes). Intro/breakdown still stays sparse. Remaining gap: continuous envelopes rather than discrete onsets, and environment-specific event boxes. Do not paste official `basicBeatmapEvents`.

## SAT / occupancy

- Joint SAT uses windows (`JOINT_WINDOW` 64, overlap 10). Frozen overlap can make the next window infeasible. Retry unfrozen overlap with prefix exits and prefix same-beat heads; repair illegal flow by dropping poses. Do not strip every chain as the first fallback.
- Occupancy for chains is `max(intent duration, 0.25)` because serialized tails use that minimum. All clocks live in `safety_contract.py`.
- Window 2+ failures on long maps are a class of bug, not a song name.

## Studio / packaging

- `--continue-unconfirmed` / Download anyway generates from the unconfirmed grid and may use fallback colors. Provenance must say unconfirmed. Never `timing_verified`, never `release_candidate`.
- Difficulty subset: `--difficulty` repeatable; studio toggles Easy–Expert+. `Info.dat` must list only generated Standard maps. Playtest evidence required for a release is the selected set, not always five.
- Independent AI *map review* UI was removed. Timing/palette Ask-provider buttons remain. Keys stay in Credential Manager.
- Do not assign `release_candidate` in the local studio. Headset evidence may still be recorded. That stamp is only for the AI-routed review path (`BEATFORGE_AI_RELEASE_ROUTE=1`).

## Tests and fixtures

- Property tests over BPM, duration, and density. Do not hardcode one playtest song’s beats.
- Official `Magic` Expert+ is the v4 parser gold (local install only).
- One human CustomLevels folder may be a measured regression (`--expect-pch-v5` today). Measured counts: 42 unmatched arc tails, 40 same-color-after-hold, Expert+ rapid same-hand 11, total rapid 12, `CROSS_HAND_PATH_COLLISION` 8 (not 22).
- Never overwrite the operator’s protected CustomLevels folders (Pacific Coast Highway Version 5, and Version 8 if they still want it kept). New gens go to versioned folders.

## Repo hygiene

Canonical git: operator’s BeatForge repo. Submission branch is `main`. Never commit `data/jobs`, `data/imports`, corpus sqlite, audio, secrets, `BeatSaberVersion.txt`. Never force-push. Python: repo `.venv`. Studio: `http://127.0.0.1:8001/` (restart uvicorn after API changes).
