---
name: beat-saber-mapping
description: Creates, regenerates, edits, audits, and validates Beat Saber custom levels with sample-accurate timing, official-map corpus retrieval, stateful hand choreography, arcs, chains, bombs, walls, lighting, full difficulty spreads, and release gates. Use for any Beat Saber map, beatmap, custom song, difficulty, pattern, lightshow, or playable-level request. Do not apply to other rhythm games.
---

# Beat Saber Mapping

Use the bundled pipeline for playable map work. Do not improvise note placement from prose, timestamps, a waveform screenshot, or an unverified BPM.

## Route the request

- For a new or regenerated map, require local audio, sync the official corpus, analyze timing, generate the requested Standard difficulties (default: all five), validate it, and report the playtest gate. The operator’s current headset song is only a playtest, never the only fixture.
- For an existing map, run `validate_map.py` before editing. Inspect `Info.dat`, every referenced gameplay/lightshow file, audio analysis, and provenance.
- For critique, run the validator and cite exact files and beats. Separate detected defects from subjective suggestions.
- For planning without audio, provide a mapping brief only. Do not claim beat-level timing or a playable package.
- Default to broad-compatible vanilla v3.3 gameplay, v3 lighting, Standard, and Easy through Expert+. Preserve existing formats and characteristics when editing unless the user requests conversion.

Read [references/premium-pipeline.md](references/premium-pipeline.md) before generating or regenerating a map. Read [references/official-corpus.md](references/official-corpus.md) before corpus sync, corpus interpretation, or official-style comparison. Read [references/formats-and-tools.md](references/formats-and-tools.md) for schemas or compatibility, [references/mapping-craft.md](references/mapping-craft.md) for manual review, [references/release-and-qa.md](references/release-and-qa.md) for release or ranking work, [references/playtest-vs-regression.md](references/playtest-vs-regression.md) before treating any CustomLevels folder as “the” test, and [references/open-problems.md](references/open-problems.md) before claiming the pipeline is finished.

A named playtest song is the operator’s headset check, not a mapping template and not the only test. Encode every failure as a song-agnostic rule. Occupancy, chain minimums, and post-hold locks live only in [scripts/safety_contract.py](scripts/safety_contract.py). Ranked failure modes: [references/build-mistakes.md](references/build-mistakes.md). The most common build mistake is **split occupancy** (SAT, pose tails, realize, and QA using different “until” times). Use [references/codex-handoff.md](references/codex-handoff.md) for a focused engineering pass.

Use [references/documentation-manifest.json](references/documentation-manifest.json) as the pinned source inventory. Check the live source before changing object semantics or serialization. Record a new access date and purpose when a source changes.

## Bootstrap once

Run the pinned core setup:

```bash
python scripts/bootstrap.py --tier core
```

For automatic model consensus and source separation, install the optional models:

```bash
python scripts/bootstrap.py --tier models
```

The model bootstrap pins BeatNet+ source and its bundled general-purpose weights to a reviewed commit in the private model cache. Pass `--beatnet-weights` only to override that default. If a model or weight is unavailable, continue with the remaining analyzers and require human anchors. Never lower the timing gate to compensate.

## Mandatory pipeline

### 1. Synchronize the official corpus

```bash
python scripts/official_corpus.py sync --game-root "PATH/TO/Beat Saber"
python scripts/official_corpus.py report
```

The importer must account for every discovered first-party candidate as indexed, excluded with a reason, or failed. Any failure blocks `official-premium` generation. The generator auto-detects common Oculus and Steam installations and refreshes changed content by hash.

Never copy the corpus database, raw official bundle content, audio, or exact official event sequences into the skill directory, output ZIP, or response. They remain in the private local cache.

### 2. Analyze audio in integer samples

```bash
python scripts/analyze_audio.py "song.mp3" --out "analysis"
```

The analyzer decodes MP3, OGG, or WAV to canonical 44.1 kHz PCM. **Beat This** is the mix clock and authors the beat grid. **BeatNet+** is the challenger (same 10 ms / 20 ms gates). **All-In-One** supplies section labels, not BPM. Optional Demucs **`htdemucs_6s`** tags mix onsets with `stemEnergy` / `layer`. After an AI timing review, both clocks are checked against the adopted grid. Then it writes:

- `analysis.json`
- `beat_grid.json`
- `sections.json`
- `audio_features.npz`
- `click_track.wav`

Automatic verification requires Beat This and BeatNet+ to agree (median pairwise residual at most 10 ms, 95th-percentile residual at most 20 ms, matching beat counts) on the Beat This grid. Otherwise status is `needs_anchors`, but the click track still follows Beat This when that model ran.

Listen to the click track at the beginning, middle, and end. Automatic timing is propose-and-verify: trackers propose a piecewise grid; an independent residual plus human click checks verify it. A cloud audio model is not the beat clock. When anchors are requested, create `anchors.json` with two or more strictly increasing entries:

```json
{
  "anchors": [
    {"beat": 0, "timeSeconds": 0.137, "kind": "downbeat"},
    {"beat": 256, "sample": 5668001, "kind": "downbeat"},
    {"beat": 512, "timeSeconds": 240.081, "kind": "beat"}
  ]
}
```

Refit with `--anchors anchors.json`. Use sample positions when known. Add anchors around pickups, tempo changes, odd-meter boundaries, live-drum drift, and ambiguous half/double-time passages. Do not generate gameplay while status is `needs_anchors` unless the operator explicitly passed `--continue-unconfirmed` (studio **Download anyway**). That path must keep provenance unconfirmed, must not set `timing_verified`, and must not set `release_candidate`. Palette fallback is allowed only on that explicit path.

### 3. Generate difficulties

```bash
python scripts/generate_map.py "song.mp3" --out "Map Folder" --profile official-premium --full-spread --title "Title" --artist "Artist" --mapper "Mapper"
python scripts/generate_map.py "song.mp3" --out "Map Folder" --profile official-premium --difficulty Expert --difficulty ExpertPlus
```

Repeat `--difficulty` for a subset (`Easy`, `Normal`, `Hard`, `Expert`, `ExpertPlus` or `Expert+`). `Info.dat` must list only generated Standard maps. Generation must use the shared musical intent graph and independently realize each requested difficulty. It must not create lower difficulties by deleting every other Expert+ note. Host studio (`POST /api/generate`) must call this script, never a thin `place.generate_chart` path. Progress lines on stderr start with `BEATFORGE_PROGRESS` then tab-separated stage, detail, and optional percent.

The choreography engine tracks each color's position, direction, parity, momentum, occupied interval, recovery deadline, expected exit pose, and lean state. CP-SAT is preferred; bounded beam search is the deterministic fallback. A long arc or chain owns its color until its tail and recovery complete. Arc duration follows the chosen sustain, not a truncated half-beat grid. The arc head and tail are the same saber; the idle hand may play *during* a long hold, never as the tail destination. The first same-hand hit after the tail must clear recovery. If a proposed hold makes two-hand scheduling impossible, downgrade that object to a note and record the relaxation; never emit an occupancy conflict. Joint SAT windows (`JOINT_WINDOW` 64, overlap 10) must retry unfrozen overlap with prefix exits and prefix same-beat heads before stripping holds. Chain occupancy uses the same `max(duration, 0.25)` tail as serialization.

Treat every consecutive red event and every consecutive blue event as one continuous saber path. Calculate pre-swing, contact, follow-through, recovery, and the next pre-swing. Opposing arrows are valid only when they form a reachable forehand/backhand return. Reject simultaneous same-color notes, repeated cut families without a readable reset, unreachable follow-through-to-entry motion, and every `SAME_HAND_FLOW_CONFLICT`. Arc and chain tails are real exit poses and retain head color ownership.

Check the hands independently from same-color flow. Reject inward-facing simultaneous red/right and blue/left cuts, handclaps, intersecting saber paths, controller clearance failures, bomb-path collisions, and wall/body conflicts. Density may be reduced when the joint color, lane, row, direction, lifecycle, and recovery constraints have no safe solution. No fallback may relax a hard constraint.

Derive the map color scheme from approved artwork. Extract embedded art first, then accept only a high-confidence Official MusicBrainz release with approved front art. A proposed pair must pass DeltaE2000 30, protanopia DeltaE2000 20, deuteranopia DeltaE2000 20, luminance, and saturation gates. Luminance and chroma may be adjusted while preserving hue. If no pair passes, stop at `needs_palette` except on the explicit `--continue-unconfirmed` path, which may use a documented fallback palette. Never invent colors silently on the verified path.

Add arcs, chains, bombs, walls, and lighting only for a detected sound, section transition, sustained gesture, texture, or deliberate body phrase. A generated object is not justified merely because the format supports it. Pre-AI intent selection must still put notes in the first seconds of audible audio: if onsets miss a fade-in, pulse the beat grid starting one second in so the first block can be read. Prefer arrow combos over dotted resets. Adjacent hits within a beat should alternate hands; same-hand returns should be 180° on the swing, not 135° diagonal inverts. Two-color hits on the same beat should usually share a cut so they line up as a combo; mirrored facing is a phrase accent, not the default. Hard maps quarter-note streams, not eighth-note walls. Expert stays above Hard with eighths; 16ths stay Expert+. If beat clocks disagree and the pulse is above ~155 BPM, fold double-time to the groove and re-snap onsets before intents.

### 4. Validate and gate

```bash
python scripts/validate_map.py "Map Folder" --json "Map Folder/_beatforge/qa_report.json"
```

Zero errors are required for `playtest_candidate`. Hard failures include invalid schema values, wrong-color or missing hold heads, conflicting hand occupancy, overlapping holds, a same-saber continuation that is too close after a hold tail, rapid same-hand post-hold hits, same-hand flow conflicts, simultaneous same-color notes, cross-hand path collisions, unreachable transitions, bomb/note collisions, invalid color schemes, and forced wall damage.

Do not assign `release_candidate` in this studio. Headset flags still record evidence. `release_candidate` exists only when the map is routed through the independent AI review path (`BEATFORGE_AI_RELEASE_ROUTE=1`). Until then the ceiling is `playtest_candidate` / `studio_reviewed`.

```bash
python scripts/validate_map.py "Map Folder" --vr-playtest-passed --fresh-sight-read-passed
```

Those flags record human tests; they do not stamp a release. Perform slow practice, full-speed VR, and a fresh sight-read when you later enable the AI route.

## Codex review

The Codex connector is a read-only release review, not a chat. It uses this mandate:

1. Read `analysis.json`, `beat_grid.json`, `clickTrackEvidence`, `dualClockCheck`, and any authored `anchors`.
2. Quote integer sample checkpoints at the start, middle, and end of the click track.
3. Treat Beat This as the mix clock. After the review, BeatForge re-checks that grid with both Beat This and BeatNet+. All-In-One is not a BPM vote.
4. Decide whether the piecewise grid still lines up with those samples. Model disagreement is not a new BPM.
5. If click-track alignment is wrong, set `timingAudit.alignment` to `misaligned` and verdict to `changes_required`. A pass is invalid when timing is misaligned.
6. Local hard failures stay blocking. Reviewers cannot dismiss them.

When automatic consensus fails, a studio or mapping agent may author sample-accurate anchors and refit. Those anchors remain subject to this review. They do not make the map a `release_candidate`.

## Object invariants

- Red is the left hand; blue is the right. Arc and chain heads, paths, tails, and exit state retain the same color.
- An arc represents a sustained gesture, not a free period for the same hand. Do not schedule same-hand events inside its occupied interval.
- A chain has a real head note, valid slice geometry, positive duration, and a playable exit.
- Bombs must avoid head notes, pre-swings, follow-through, arc paths, and natural resets.
- Walls must be visible early and leave a survivable lean or crouch path. Never fill all standing space.
- Direction, parity, reach, recovery, vision, and escape are phrase-level constraints, not isolated-note checks.
- Never place an inward-facing simultaneous red cut with a simultaneous inward-facing blue cut. This is a handclap even when the note cells differ.
- NPS is descriptive. Difficulty also depends on effective BPM, swing angle/distance, hand use, resets, vision, body movement, NJS, and spawn offset.

## Regression fixtures vs playtest

Code must stay green on synthetics, property tests (BPM, duration, density), and official `Magic` Expert+ as the local v4 parser gold. Never redistribute `Magic`.

One optional human CustomLevels folder may be measured with `--expect-pch-v5` when that install still exists. Those counts describe that file only:

```bash
python scripts/validate_map.py "PATH/TO/Pacific_Coast_Highway" --expect-pch-v5
```

Expected detections on that fixture: 42 unmatched arc tails, 40 same-color-after-hold, Expert+ rapid same-hand 11, total rapid 12, `CROSS_HAND_PATH_COLLISION` 8 (not 22). Do not treat that folder’s BPM, NJS, artist, or note list as a generation target. Do not overwrite protected playtest folders; install new gens into a new versioned CustomLevels directory.

Headset comments on whatever the operator is playing (density feels Easy, lights are dead, keep walls/dots) are product feedback for the *generator*. Reproduce the class of issue on another track or a synthetic before locking a fix.

## Deliverables

Return the map folder or upload-ready root ZIP plus `_beatforge` reports. Summarize format, characteristic, generated difficulties, NJS/offsets, object counts, timing source, corpus coverage, solver/seed, validation status, and human playtest status. Say `playtest_candidate` until VR and fresh sight-read evidence exists; never promise subjective perfection.
