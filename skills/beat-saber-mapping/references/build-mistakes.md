# Build mistakes (song-agnostic)

These showed up first on a long electronic playtest. Encode the *rule*. If a bug appeared at beat 598.5 on one track, the next song must not hit the same class at a different beat.

## Ranked by how often it showed up

1. **Split occupancy (most frequent).** SAT, pose geometry, realize, and QA each used a different “until” for the same hold. Symptoms: `RuntimeError: hand N is occupied at beat X`, `RAPID_SAME_HAND_AFTER_HOLD`, Expert/Expert+ dropping every chain after a SAT fallback. **Fix:** `scripts/safety_contract.py` only. Never re-type `0.25` or `0.250001` in choreography or validate_map.

2. **Window SAT freeze as a hard abort.** Overlapping CP-SAT windows froze poses from the previous window; the next window was infeasible; `generate_all` fell back to two-stage SAT that downgraded holds. Symptom is song-length maps, not one title. **Fix:** retry unfrozen overlap with prefix exits and prefix same-beat heads; density may drop; hard rules may not.

3. **Hold tail vs next note not in the SAT double/vision model.** Arc/chain tails are extra geometry at a later beat. SAT compared heads only, then QA raised `CENTER_VISION_DOUBLE`. **Fix:** constrain hold tails against intents on the tail beat; constrain new window notes against prefix heads/tails at the same beat.

4. **Song-shaped tests and constants.** Hardcoding one playtest’s beats, fake collision counts (22 vs measured 8 on one human Expert+), or “make this song pass” without a property test. **Fix:** property tests over duration, BPM, and density. One human folder may remain a measured `--expect-*` fixture only.

5. **Treating fallback success as premium success.** Joint SAT `None` still produced a map with `poseSolver: cp-sat` and zero chains. Automations looked green. **Fix:** provenance must show `joint-cp-sat` when holds were requested; tests assert chains survive on dense Expert+ intents, not only `errors == []`.

6. **Loosening timing because one song needs anchors.** Mix ensembles often fail p95 or drift. That is `needs_anchors` for any disagreeing song. Never lower 10 ms / 20 ms gates.

7. **Empty opening.** Global onset quantiles and a 1.25 peak floor can skip a quiet intro, so the first block lands after 10s. **Fix:** keep a beat-grid pulse from one second in `select_intents`, and pick weaker onsets in the first 10s. Do not place a note on beat 0. Do not special-case one title.

8. **Dot flood instead of combos.** SAT tiled one cut, then only dots stayed legal. **Fix:** combo-first pose domains, dots as rare resets, penalize `d==8` in SAT. Palette is not the swing language.

9. **Opposite arrows every hit.** Mixing both parity families in one pose domain plus one-hand half-beat streams produced 135° ping-pong (DR then UL). **Fix:** one family per parity, cardinal down/up first, alternate hands within 1 beat, ban 135° reversals at combo spacing.

10. **Expert stacked onto Expert+.** Adaptive gaps and Expert chains emitted 16th stacks, so Expert played like Expert+. **Fix:** Hard stays on beat-or-better spacing (`min_gap` 1.0). Expert is eighths (`0.5`, no chains). Expert+ uses eighths in verses and sixteenths only in peaks (`peak_min_gap` 0.25). Official Standard note-count ratios average about 1.56 Normal→Hard, 1.42 Hard→Expert, 1.29 Expert→Expert+. Never shrink below the active gap.

11. **Double-time clock as the map pulse.** Beat This can tick 8ths (~180 BPM) on a ~90 BPM song. Unconfirmed download then places Hard on those 8ths, so it feels off-beat and like Expert+. **Fix:** when mix clocks fail and BPM ≥ 155, fold every other beat, rewrite `bpm`, and re-snap onsets onto the adopted grid. Do not lower 10/20 ms gates.

## Agent checklist before changing choreography or QA

- Did occupancy, tail length, and RAPID_SAME_HAND come from `safety_contract`?
- Would this still be true at BPM 70 and 240, and at beat 4 as well as beat 600?
- If joint SAT fails, does the fallback keep hard safety, and is the solver label honest?
- Are new tests parametric, not copied playtest timestamps?
