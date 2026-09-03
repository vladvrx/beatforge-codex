# Playtest songs vs regression fixtures

The operator will play whatever they dropped on the studio. That song is not the product’s only test, and it is not a mapping template.

## Playtest

A playtest is a human wearing a headset on a generated or imported folder. Comments like “colors good,” “walls good,” “Expert+ feels Easy,” “lights are dead” apply to *that* pack. Fix the generator so the next unrelated track improves. Do not special-case title, artist, or BPM from the playtest OGG.

## Regression fixtures (code)

- Synthetic PCM / click tracks in `tests/` and skill `test_pipeline.py`.
- Official `Magic` on disk for v4 parse/gold, never redistributed.
- Optional measured human custom level via `--expect-pch-v5` when that folder still exists. The numbers are measurements of that file, not targets to copy into new maps.

## Generation

`--difficulty` may be a subset. Full spread is still the default when the operator leaves every chip on. Lower diffs are independently realized, not thinned Expert+.
