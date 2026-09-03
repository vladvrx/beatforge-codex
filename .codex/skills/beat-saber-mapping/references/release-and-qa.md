# Release and QA

Use this reference for audits, playtesting, BeatSaver packaging, or ranked submissions.

## Structural pass

Run `scripts/inspect_map.py` on the folder or zip. Then use the chosen editor's checker or a current schema-aware validator.

Confirm:

- `Info.dat` parses and every referenced audio, cover, audio-data, beatmap, and lightshow file exists with matching case.
- Every difficulty opens in the target editor and game.
- Object arrays, v4 metadata indices, durations, link counts, and beat ordering are valid.
- Counts before and after conversion or bulk edits are explainable.
- Declared mod requirements match the custom data actually used.
- The zip has map files at its root and excludes autosaves, test renders, unrelated media, and secrets.

## Mapping pass

Check the whole song at reduced speed, then at full speed.

- Timing: BPM, offset, downbeats, tempo changes, drift, hot start, and outro.
- Representation: every object has a sound or a clear setup purpose; emphasis follows the arrangement.
- Motion: parity, resets, wrist angle, reach, handclaps, pre-swings, follow-through, setup, escape, momentum, and stamina.
- Readability: face notes, center-lane sequences, simultaneous objects, arc clutter, wall occlusion, crouched eye height, NJS, and spawn offset.
- Bombs: visible, purposeful, outside unintended saber paths, and not hidden by walls.
- Walls: a safe head position always exists; dodge and crouch timing fits the target player.
- Arcs and chains: paths match the intended swing, endpoints make sense, links are readable, and object use is not constant visual noise.
- Lighting: hazards remain visible; flashes, darkness, and contrast changes have been checked in headset.
- Difficulty spread: each lower difficulty is a coherent remap for its audience.

## Test status

Track four statuses separately:

1. Structural inspection passed.
2. Editor checker passed.
3. In-game VR playtest passed at slow and full speed.
4. Fresh external sight read passed.

Do not collapse these into "tested." A generated map can pass JSON checks and still be unpleasant, unsafe, off-time, or unreadable.

Ask external testers for their target skill, grip or play style when relevant, first-read failures, misses caused by vision, physical discomfort, and timestamps. Fix the underlying communication problem instead of teaching the tester the solution.

The [BSMG playtesting guide](https://bsmg.wiki/mapping/how-to-testplay.html) and [mapping hub](https://bsmg.wiki/mapping/) list current community testing practices and checker tools.

## Ranked intent

Ranking rules change. Before giving ranked-specific advice or claiming compliance, open the current criteria for the target leaderboard and record the access date.

- [ScoreSaber ranking criteria](https://wiki.scoresaber.com/ranking/criteria/)
- [ScoreSaber mapping criteria](https://wiki.scoresaber.com/ranking/criteria/mapping-criteria.html)
- [ScoreSaber technical limitations](https://wiki.scoresaber.com/ranking/criteria/technical-limitations-criteria.html)
- [BeatLeader ranking criteria repository](https://github.com/BeatLeader/Ranking-Criteria)

Separate hard criteria from general mapping advice. A creative unranked map may use techniques a leaderboard rejects. A rankable map must meet the current format, object, metadata, lighting, timing, difficulty-spread, and technical rules of that leaderboard.

Do not copy remembered claims about v4, chains, arc connection, wall geometry, angle offsets, intro or outro duration, or mod support. Verify them live because these rules have changed before.

## Release handoff

Provide a manifest containing:

- Song, artist, mapper and lighter credits.
- Schema version, characteristic, difficulties, NJS, spawn offset, and environment.
- Note, bomb, wall, arc, and chain counts by difficulty.
- Vanilla or required mods.
- Test status and unresolved issues.
- Source and license or permission notes for user-provided audio and artwork when the user tracks them.

Package the map only after references pass, filenames match, and the user has reviewed any remaining playtest caveats.
