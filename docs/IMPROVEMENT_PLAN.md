# BeatForge improvement plan

This implementation follows the repository review at `5c0cfb9`. The aim is a studio where creative direction changes the generated chart, the result can be heard and inspected, and a selected passage can be revised without losing the rest of the work.

## 1. Reliable generation and recovery

- Repair the browser metadata field contract and both supported timing-anchor formats.
- Require readable, passing QA before a pack is eligible for installation. Preserve a clearly labeled unverified timing workflow without treating structural errors as acceptable.
- Make later failed playtests supersede earlier matching clears.
- Preserve job history, identify interrupted jobs after restart, and offer retry and cancellation.
- Verify these behaviors with real API requests and isolated installation doubles.

## 2. Creative direction and musical structure

- Introduce a versioned mapping plan shared by the browser, API, CLI, generator, and provenance.
- Support intensity, density, instrument emphasis, style, verse/chorus contrast, and optional bombs/walls.
- Interpret a documented set of brief phrases deterministically, display the applied settings, and never pretend unrestricted prose understanding.
- Improve phrase consistency and rank safe choreography candidates using musical and corpus evidence.
- Verify that the same seed and plan reproduce output, that different plans change intended behavior, and that hard constraints remain intact.

## 3. Inspect and revise maps

- Add an audio-synchronized perspective chart preview, waveform, difficulty selection, seeking, playback speed, section markers, and QA markers.
- Load actual generated map objects through a confined preview API.
- Regenerate a selected beat range into a separate revision, preserve objects outside that range, revalidate seams and the complete result, and retain the original job.
- Provide a real synthetic sample with audio and chart data for local and static previews. Clearly distinguish a sample from the full generation pipeline.
- Verify the browser controls, audio synchronization, actual note data, and revision preservation.

## 4. Correct and evaluate learning

- Share beat/sample-aligned feature extraction between training and inference. Use actual section labels and available stem energies, record missing features, and remove invented stems and the fixed 120 BPM clock.
- Require valid inference checkpoints and record their identity.
- Correct terminal bootstrapping in PPO and make training reproducible.
- Add held-out evaluation that reports map quality separately from training loss and reward.
- Verify feature timing, section alignment, checkpoint errors, terminal boundaries, and benchmark output with deterministic fixtures.

## 5. Setup, verification, and delivery

- Provide a local launcher and actionable dependency checks.
- Keep the canonical and portable skill copies synchronized.
- Run relevant application and mapping suites, a local Studio browser pass, and generated-pack validation.
- Record test results and limitations. A visual preview or automated validator cannot establish headset comfort or a human clear.
- Deliver the updated repository and instructions for starting it locally. Remote publishing is a separate action from local implementation.

## 6. A measured self-improvement loop

User expansion: make BeatForge self-improving and add features that improve the experience. This extends the active implementation goal.

- Save explicit feedback against the exact chart hash, difficulty, and beat range: timing, flow, readability, repetition, intensity, and freeform headset notes.
- Record A/B preferences between two maps of the same audio and difficulty. Keep an unmodified baseline and retain failed experiments for diagnosis.
- Treat accepted section revisions as examples of user preference. Export a local dataset with versioned provenance and without copying song audio.
- Add a model registry with benchmark results, active checkpoint, prior checkpoint, and rollback. Promotion requires matching held-out benchmark identity, no hard failures, and improvement on the selected quality metric without defined regressions.
- Keep training results, structural validation, and human preference scores separate. The app must never invent ratings, human clears, or claim that a promoted checkpoint has been played.
- Provide a visible improvement dashboard explaining evidence collected, outstanding evaluations, and the next useful action. Training and promotion remain explicit operations with visible results.

## 7. Faster everyday use

- Save and reuse personal creative presets, including instrument focus, density, style, and accessibility choices.
- Compare two revisions in the preview at the same audio position and difficulty, then save a preference.
- Show actionable QA findings with beat navigation and retain section feedback alongside revisions.
- Restore the last selected local job after refresh, show interrupted work clearly, and allow recovery without re-uploading audio.
- Expose useful preview, feedback, and comparison operations through typed WebMCP tools with the same validation as the visible controls.

## Progress

- [x] Review current repository and create implementation branch.
- [x] Reliable generation and recovery (API lifecycle coverage and durable retry/cancel state).
- [x] Creative controls and musical structure (versioned plans, deterministic brief interpretation, candidate ranking).
- [x] Real map preview and section revisions (audio clock, waveform, QA navigation, isolated range regeneration).
- [x] Learning correctness and evaluation (sample-aligned features, masked PPO, held-out evaluation, checkpoint registry).
- [x] Setup, integration, regression checks, and delivery audit (launcher, doctor, portable lock, browser and API suites).
- [x] Feedback dataset, evaluation-gated model registry, and improvement dashboard.
- [x] Personal presets and comparison workflow.

The software-side gates are complete. A real headset session remains a human release activity: sight-read each selected difficulty at full and slow speed, inspect comfort and reach, and record that evidence through the Studio before treating a map as cleared.

The goal remains active while any required implementation or verification is incomplete.
