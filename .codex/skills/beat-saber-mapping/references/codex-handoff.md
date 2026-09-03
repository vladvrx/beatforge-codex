# Codex — BeatForge mapping-pipeline handoff

Use this handoff with the Codex skill when working on BeatForge. You are an engineering agent on BeatForge, a Beat Saber custom-level generator. You are not a DJ. You do not invent beats from a waveform screenshot.

---

## Role

You implement and fix the Beat Saber mapping skill and the host studio that calls it. Canonical mapping code lives in `skills/beat-saber-mapping/`. After editing that tree, run `python tools/sync_portable_skill.py --write` so `.codex/skills/` stays byte-identical and `skill-lock.json` updates. Host API: `src/beatforge/premium.py` + `src/beatforge/api.py`. Studio UI: `web/index.html`. Tests: `tests/` for the app, `skills/beat-saber-mapping/tests/` for the skill (`PYTHONPATH` must include `skills/beat-saber-mapping/scripts`).

Python: repo `.venv`. Studio: `http://127.0.0.1:8001/` (uvicorn `beatforge.api:app`). Restart the server after Python/API changes.

## What “good” means

A generated Standard pack that:

- uses a monotonic integer-sample grid at 44_100 Hz
- independently realizes each requested difficulty (not thinned Expert+)
- passes joint occupancy, flow, vision, bombs, walls, palette gates
- reports honest provenance (`joint-cp-sat` only when joint SAT actually kept holds)
- is `playtest_candidate` at most until a human records headset + sight-read

Never claim VR playtests you did not do. Never stamp `release_candidate` in the local studio; that label is only for `BEATFORGE_AI_RELEASE_ROUTE=1`. Never loosen 10 ms median / 20 ms p95 timing gates. Never clone official `basicBeatmapEvents` or note lists. Never commit audio, jobs, corpus sqlite, secrets, `BeatSaberVersion.txt`, or `data/imports`. Never force-push. Never overwrite the operator’s protected CustomLevels folders.

## Playtest song is not the product test

The operator currently playtests with Pacific Coast Highway in the headset. That is **one** human check. It is not:

- the only pytest fixture
- a mapping template
- a reason to special-case title, artist, BPM, or NJS in generator code

Failures found while they played that song become **song-agnostic rules** (occupancy clocks, SAT window freeze, lighting event floor, Expert+ density knobs). Reproduce the class of bug on synthetics, a second licensed track, or official `Magic` (local parse gold only; do not redistribute).

Optional measured human fixture: `--expect-pch-v5` on an installed Version 5 folder. Locked counts: 42 unmatched arc tails, 40 same-color-after-hold, Expert+ rapid same-hand 11, total rapid 12, `CROSS_HAND_PATH_COLLISION` **8** (not 22). Version 5 BPM in Info is **117.454**, Expert+ NJS **18**, duration ~344.267 s, artist string Version5. BeatForge gens of the same OGG often author **120** BPM because the mix ensemble failed (p95 / drift) and the spine locked 120 — that is not why Expert+ “felt Easy.” Sparse intents and NJS/config are.

Protected folders: `Pacific_Coast_Highway` (V5, never overwrite) and keep V8 if the operator still wants it. New installs go to a new versioned CustomLevels name.

## Pipeline the studio actually runs

`POST /api/generate` → `premium.py` → `generate_map.py --profile official-premium` plus repeated `--difficulty`. Website must not call `beatforge.place.generate_chart`.

`--continue-unconfirmed`: operator clicked **Download anyway**. Generate from unconfirmed grid. Provenance unconfirmed. Not `timing_verified`. Not `release_candidate`. Palette fallback allowed only here.

Difficulties: `parse_difficulties` in `beatforge_core.py`. Studio toggles Easy–Expert+ (`Expert+` → `ExpertPlus`). At least one required. Empty form field may be dropped by Starlette — do not treat that as “all five” by accident if the operator meant a subset; reject unknown names.

Progress: stderr `BEATFORGE_PROGRESS`, tab, stage, tab, detail, optional tab percent. Stream Popen with `PYTHONUNBUFFERED=1`. Decode % from FFmpeg `-progress pipe:1`. UI elapsed time = job `startedAt` + client interval.

## Safety (already landed — do not regress)

Single clocks in `scripts/safety_contract.py`: `POST_HOLD_SAME_HAND_BEATS = 0.250001`, `MIN_CHAIN_DURATION_BEATS = 0.25`. `validate_map.py` and `choreography.py` import those helpers. Do not retype the constants.

Joint SAT: `_solve_joint_model` / `_solve_joint_assignment`, `JOINT_WINDOW=64`, `JOINT_OVERLAP=10`. Frozen overlap poses made later windows infeasible and fallbacks stripped chains. Retry **unfrozen** overlap with prefix exits and prefix same-beat heads; repair illegal flow by dropping poses. Chain SAT occupancy must use `max(duration, 0.25)` because serialized tails do.

Ranked failure modes: `references/build-mistakes.md`. Open debts: `references/open-problems.md`.

## Remaining engineering (do these next)

1. **Expert+ feel vs human maps** — keep walls, approved palettes, and *rare* reset dots. Raise density via intent quantiles, min_gap, recovery, NJS. Opening pulse from beat 0. Combo-first poses: alternate hands within a beat, one parity family per hit, cardinal 180 returns, no 135° ping-pong. Expert stays between Hard 8ths and Expert+ 16ths. Do not copy a human note list.

2. **Reactive lighting** — onset/stem flashes exist on top of the section clock. Remaining: continuous envelopes and environment-specific boxes. Do not paste official event sequences.

3. **Timing UX** — keep 10/20 ms. Propose-and-verify anchors locally. Cloud models (including you) are not the beat clock. Auto-timing often fails → `needs_anchors` is correct.

4. **SAT on long maps** — window 2+ infeasibility is a class of bug. Tests must be parametric over duration, not one song’s beat 598.5.

5. **Honest provenance** — `poseSolver: joint-cp-sat` only when holds requested actually survived. Tests: chains remain on dense Expert+ intents, not only `errors == []`.

6. **Studio** — The Codex timing and palette actions remain read-only and approval-gated. Keys stay in Credential Manager. Do not add a fake “Codex said it’s perfect” panel.

7. **Corpus** — sync from a local Beat Saber install. Incomplete corpus blocks `official-premium`. Never copy the sqlite into git.

## Tests to run

```text
PYTHONPATH=src;tools   pytest tests -p no:cacheprovider
PYTHONPATH=skills/beat-saber-mapping/scripts   pytest skills/beat-saber-mapping/tests -p no:cacheprovider
```

On Windows PowerShell set `$env:PYTHONPATH` accordingly. Playwright Chromium may skip.

## Hard no

- Codex as the source of BPM or beat times
- Lowering timing gates so one playtest generates
- Faking cross-hand counts
- Claiming the map is ranked-ready or subjectively perfect
- Committing operator audio or CustomLevels dumps
