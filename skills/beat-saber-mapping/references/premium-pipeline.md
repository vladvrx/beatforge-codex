# Premium pipeline reference

## Stable commands

```text
official_corpus.py sync --game-root PATH [--database PATH]
official_corpus.py report [--database PATH]
analyze_audio.py AUDIO --out PATH [--anchors PATH] [--bpm BPM] [--offset-seconds S]
generate_map.py AUDIO --out PATH --profile official-premium --full-spread
generate_map.py AUDIO --out PATH --profile official-premium --difficulty ExpertPlus [--difficulty Expert ...]
generate_map.py AUDIO --out PATH --profile official-premium --continue-unconfirmed
validate_map.py MAP [--json PATH] [--vr-playtest-passed] [--fresh-sight-read-passed] [--expect-pch-v5]
```

All time in the analysis layer is an integer sample index at 44,100 Hz. Beat values in map files are derived views of a monotonic sample grid. The pipeline keeps source hashes, model results, anchors, grid source, deterministic seed, official-reference identifiers, solver choice, hold relaxations, structural results, and playtest gates in `_beatforge`.

Host studio streams generator stderr. Progress lines are `BEATFORGE_PROGRESS`, then tab, then stage, then tab, then detail, then optional tab and a percent. Decode percent comes from FFmpeg `-progress pipe:1`. Elapsed time is job `startedAt` plus client clock, not a guessed remaining ETA.

`--continue-unconfirmed` is an explicit operator override after **Download anyway**. It generates from the unconfirmed grid and may use fallback colors. It never writes `timing_verified` or `release_candidate`.

`--difficulty` is repeatable. Default is all five Standard maps. Info.dat lists only maps that were generated. Independent realization still applies to whatever subset was requested.

## Timing decisions

The internal onset/autocorrelation tracks are diagnostic and provide anchor suggestions. They cannot independently pass automatic timing. **Beat This** authors the mix grid. Automatic timing passes only after Beat This and BeatNet+ agree within the configured 10 ms median and 20 ms 95th-percentile thresholds with consistent beat counts, and onsets fit that grid. All-In-One is not a third beat vote; it supplies section boundaries. After an AI timing review, both clocks are checked against the adopted grid. A user-confirmed BPM/offset or monotonic anchor set may verify timing explicitly.

All-In-One's section boundaries replace the internal 16-beat energy segmentation when available. Demucs is applied in-process from the pinned Facebook package (`scripts/demucs_stems.py`, not the CLI karaoke two-stem mode). Prefer `htdemucs_6s` (drums, bass, guitar, piano, vocals, other); fall back to four-stem `htdemucs`. PCM16 is written with the stdlib wave module (not `torchaudio.save` / TorchCodec). Mix onsets are tagged with per-stem energy before the temp WAVs are deleted. Expert / Expert+ may keep instrument-layer onsets that a mix-only quantile would drop. Stem beat trackers still cannot pass the 10 ms / 20 ms mix gate. Demucs cannot isolate kick/snare/hats or every layered synth.

If trackers disagree, inspect the click track and anchor the opening, middle, ending, and every suspected tempo or meter boundary. A constant grid must not be forced over live drift. Authored anchors may verify timing for generation; the Codex review must still audit those anchors against start/middle/end click-track samples and cannot pass a misaligned grid.

## Choreography stages

1. Deduplicate onsets on the verified grid and attach section/intensity plus optional Demucs `layer` context.
2. Build a separate intent list for each difficulty using its own density and recovery limits.
3. Propose sustained arcs and short chains from sustain/texture evidence.
4. Build a joint color, lane, row, direction, lifecycle, and recovery candidate domain. Enforce active-hold ownership. Keep arc/chain tails on the same saber; idle-hand notes belong in the middle of a long hold, not on the tail.
5. Solve every same-color transition against explicit pre-swing, contact, follow-through, recovery, and next-entry geometry. Reject inward simultaneous red/blue cuts and unsafe saber-path intersections.
6. Realize arc/chain geometry, bombs, walls, and lighting while updating the same hand state used by the validator. Occupancy, chain minimum tail length, and the quarter-beat same-hand lock are defined only in `scripts/safety_contract.py`.
7. Validate the serialized result. Hard errors cannot be traded for a higher corpus-style score. A fallback may lower density but may not relax safety.

A generated package with zero hard errors is a playtest candidate, not a substitute for authored iteration. Use validator findings, practice mode, headset play, and sight-read feedback to edit intent or constraints and regenerate deterministically. Lighting follows a beat-grid clock (half notes in verses, extra laser sixteenths in peaks) plus intent accents; it is not a clone of official event lists. Improve from audio features and section intensity. Expert+ density and NJS are difficulty config, not the song title. Info.dat BPM is the authored spine; a human map of the same audio may use a different BPM.

Open engineering debts: [open-problems.md](open-problems.md). Playtest vs fixtures: [playtest-vs-regression.md](playtest-vs-regression.md).

## Status contract

- `needs_anchors`: timing is ambiguous; verified-path generation is blocked. `--continue-unconfirmed` may still pack a map with unconfirmed provenance.
- `needs_palette`: artwork is unapproved, missing, or cannot produce two readable colors; gameplay packaging is blocked.
- `timing_verified`: model thresholds or explicit human anchors passed.
- `structurally_valid`: schema and hard invariants passed for an inspected artifact.
- `playtest_candidate`: a generated package passed structural validation.
- `studio_reviewed`: local hard gates and the approved Codex review passed with no unresolved findings.
- `release_candidate`: disabled in the local studio. It is only available when maps are routed through the independent AI review path (`BEATFORGE_AI_RELEASE_ROUTE=1`), after structural validation plus recorded full-speed VR and fresh sight-read passes.
- `invalid` or `corpus_incomplete`: do not ship.

## Upstream analyzers

- Beat This: https://github.com/CPJKU/beat_this
- BeatNet+: https://github.com/mjhydri/BeatNet-Plus
- All-In-One research implementation: https://github.com/mir-aidj/all-in-one
- Windows-compatible All-In-One inference distribution: https://github.com/openmirlab/all-in-one-infer
- Demucs: https://github.com/facebookresearch/demucs
- OR-Tools CP-SAT: https://developers.google.com/optimization/cp

Use pinned releases from the requirement files. The model bootstrap downloads BeatNet+ source and bundled weights at the commit pinned in `bootstrap.py`; its inference package is not available as a normal PyPI dependency.
