# Beatforge style study

> Historical baseline from the pre-quality-gate corpus. The current fetch pipeline
> requires a BeatSaver community score of at least 85%; rerun `rescan.py` and
> `compare_styles.py` after downloading the filtered catalog to regenerate these
> aggregate figures.

Principles distilled from maps in every curated + verified BeatSaver playlist (`order=Curated&verified=true`). Notes are never copied — only aggregate rhythm, density, flow, and spatial statistics inform Beatforge placement.

## Corpus

- Playlists enumerated: **919**
- Unique maps fingerprinted: **19403**
- Difficulty files scored: **44457** (40512 Standard)
- Automapper maps skipped at catalog time
- All Standard difficulties are fingerprinted (Easy through Expert+ when present)

## Easy → Expert+ scaling

| Difficulty | Maps | NPS median | p25 | p75 |
|---|---:|---:|---:|---:|
| Easy | 2905 | 1.59 | 1.114 | 2.288 |
| Normal | 3556 | 2.499 | 1.937 | 3.412 |
| Hard | 7081 | 3.275 | 2.688 | 4.083 |
| Expert | 10384 | 4.254 | 3.523 | 5.387 |
| ExpertPlus | 16819 | 5.475 | 4.418 | 7.23 |

Typical full-spread ratios (median across maps that include both diffs):
- Ex+/Easy: **3.178×**
- Expert/Normal: **1.833×**
- Expert/Hard: **1.323×**

Ladder rule: Easy is mostly quarter-note bottom-row reading; Hard is where 1/8 vocabulary becomes common; Expert is the 'complete' chart; Expert+ adds density, height, and tighter hand gaps rather than a new genre of pattern.

## Expert-level fingerprint medians (Standard)

| Feature | mean | median | p25 | p75 |
|---|---:|---:|---:|---:|
| `nps` | 5.7814 | 4.9714 | 3.9771 | 6.5823 |
| `jump_ratio` | 0.3056 | 0.2954 | 0.1997 | 0.3996 |
| `reset_ratio` | 0.0877 | 0.0438 | 0.0151 | 0.0928 |
| `flow_ratio` | 0.9118 | 0.956 | 0.907 | 0.9848 |
| `cross_ratio` | 0.2536 | 0.254 | 0.1816 | 0.327 |
| `stream_ratio` | 0.0682 | 0.0088 | 0.0 | 0.0576 |
| `section_contrast` | 0.2566 | 0.2246 | 0.1589 | 0.3191 |
| `motif_reuse` | 1.347 | 0.5968 | 0.3333 | 0.9745 |
| `dot_ratio` | 0.0773 | 0.0437 | 0.0069 | 0.1143 |
| `hand_alt_ratio` | 0.6802 | 0.6881 | 0.6222 | 0.7529 |
| `spatial_travel` | 5.8435 | 1.6874 | 1.5471 | 1.8096 |
| `bomb_rate` | 0.361 | 0.041 | 0.0 | 0.267 |
| `wall_rate` | 1.4863 | 0.5769 | 0.1703 | 1.3842 |
| `same_col_repeat` | 0.2992 | 0.2832 | 0.2286 | 0.3453 |
| `cut_entropy` | 0.8241 | 0.8492 | 0.7707 | 0.9133 |
| `lane_change_rate` | 0.7005 | 0.7166 | 0.6549 | 0.7707 |
| `triplet_ratio` | 0.059 | 0.0082 | 0.0 | 0.0522 |
| `double_stack_rate` | 0.0682 | 0.032 | 0.0 | 0.1 |

## Style families (k-means on Standard diffs)

### Cluster 0: **tech** (986 diffs)

resets, verticality, cross-hand, lower stream

- NPS 15.3232, flow 0.3835, reset 0.6068, stream 0.6787, jumps 0.3181, cross 0.2407
- Common BeatSaver tags: poodle (356), challenge (225), pop (187), balanced (164), electronic (126), tech (125)

Stand-outs vs cluster mean (largest feature distance — study, do not clone):
- `3021e` song about love [ExpertPlus] dist=569.359 nps=38.6165 flow=0.5077 reset=0.4923 tags=
- `1b3f5` It's Been So Long [Expert] dist=502.108 nps=11.9133 flow=0.4232 reset=0.5768 tags=
- `1b5e5` FIREWORK FULL COVER [ExpertPlus] dist=352.612 nps=16.1581 flow=0.1149 reset=0.8851 tags=pop, comedy-meme, dance-style
- `2fb1` SMASH [ExpertPlus] dist=171.297 nps=167.2 flow=0.5 reset=0.5 tags=
- `4a0d7` if snakes in the wall was on crack [ExpertPlus] dist=113.315 nps=120.0469 flow=0.4094 reset=0.5906 tags=challenge

### Cluster 1: **flow** (19088 diffs)

continuous swings, high flow_ratio, moderate density

- NPS 4.644, flow 0.9458, reset 0.0542, stream 0.0145, jumps 0.307, cross 0.2517
- Common BeatSaver tags: balanced (8630), tech (5787), dance-style (4935), electronic (3736), k-pop (2818), j-pop (2558)

Stand-outs vs cluster mean (largest feature distance — study, do not clone):
- `53257` PLEAWSE DON'T TOUCHI MEE [ExpertPlus] dist=1197.937 nps=5.3289 flow=0.7143 reset=0.2857 tags=challenge, fitness, k-pop
- `25e65` 猫ダッシュ☆もふもふパラダイス [ExpertPlus] dist=904.171 nps=3.6551 flow=0.9746 reset=0.0254 tags=
- `3d29f` Shikairo Days / シカ色デイズ [ExpertPlus] dist=688.019 nps=5.3559 flow=1.0 reset=0.0 tags=anime, comedy-meme, accuracy
- `3d16b` Shikanokonokonokokoshitantan [ExpertPlus] dist=647.508 nps=6.1148 flow=1.0 reset=0.0 tags=fitness, anime, comedy-meme, challenge
- `4c8a3` Ceremony [Normal] dist=259.933 nps=2.4691 flow=0.8716 reset=0.1284 tags=k-pop

### Cluster 2: **flow-2** (9430 diffs)

continuous swings, high flow_ratio, moderate density

- NPS 6.9383, flow 0.9075, reset 0.0916, stream 0.1128, jumps 0.2734, cross 0.2493
- Common BeatSaver tags: tech (3214), balanced (3042), electronic (2867), speed (1717), dance-style (1055), dubstep (732)

Stand-outs vs cluster mean (largest feature distance — study, do not clone):
- `2f2d9` When I Use It [Hard] dist=1055.949 nps=23.1148 flow=1.0 reset=0.0 tags=challenge, accuracy, speedcore
- `ac10` Enigma [ExpertPlus] dist=781.631 nps=10.9675 flow=0.8978 reset=0.1022 tags=
- `63f4` FIRETHROWER [ExpertPlus] dist=499.517 nps=7.3921 flow=0.8099 reset=0.1901 tags=electronic, dubstep, house, techno
- `48b99` Bones in the Soil, Rust in the Oil [ExpertPlus] dist=228.715 nps=9.4791 flow=0.9562 reset=0.0438 tags=challenge, tech, indie
- `9fe3` Death Moon [ExpertPlus] dist=178.929 nps=7.0368 flow=0.9946 reset=0.0054 tags=electronic

### Cluster 3: **flow-3** (10919 diffs)

continuous swings, high flow_ratio, moderate density

- NPS 2.6634, flow 0.9015, reset 0.0969, stream 0.0046, jumps 0.2222, cross 0.1327
- Common BeatSaver tags: balanced (5044), tech (2685), dance-style (2625), electronic (2247), accuracy (1698), k-pop (1350)

Stand-outs vs cluster mean (largest feature distance — study, do not clone):
- `4d75f` 永遠の都で Wall show [Hard] dist=169.543 nps=2.8547 flow=0.9941 reset=0.0059 tags=video-game-soundtrack
- `415cd` Supernatural [Easy] dist=165.938 nps=1.7461 flow=0.0 reset=1.0 tags=dance-style, tech, k-pop, dance
- `48067` REVIVƎЯ [ExpertPlus] dist=135.804 nps=1.5233 flow=1.0 reset=0.0 tags=balanced, accuracy, j-pop, pop
- `9c54` Wakusei_Rabbit [Hard] dist=130.788 nps=3.7171 flow=1.0 reset=0.0 tags=
- `1ad55` Shelter [ExpertPlus] dist=111.963 nps=2.8318 flow=0.8818 reset=0.1182 tags=balanced

### Cluster 4: **speed** (1 diffs)

high NPS, high stream/0.25 IOI, fewer jumps

- NPS 414.1455, flow 0.5, reset 0.0, stream 1.0, jumps 1.0, cross 0.0
- Common BeatSaver tags: comedy-meme (1), challenge (1), k-pop (1)

Stand-outs vs cluster mean (largest feature distance — study, do not clone):
- `51ed9` kpop demon hunters [ExpertPlus] dist=0.0 nps=414.1455 flow=0.5 reset=0.0 tags=comedy-meme, challenge, k-pop

### Cluster 5: **tech-5** (88 diffs)

resets, verticality, cross-hand, lower stream

- NPS 10.7058, flow 0.7058, reset 0.2146, stream 0.2587, jumps 0.35, cross 0.3517
- Common BeatSaver tags: electronic (10), dance-style (9), dance (9), j-pop (7), balanced (7), challenge (7)

Stand-outs vs cluster mean (largest feature distance — study, do not clone):
- `e34e` Lick It [ExpertPlus] dist=7508.976 nps=6.0388 flow=0.9111 reset=0.0889 tags=
- `29ae1` Down [ExpertPlus] dist=3785.884 nps=9.0095 flow=0.2982 reset=0.7018 tags=poodle
- `2b90d` Us Against The World [Expert] dist=2465.505 nps=10.2336 flow=0.323 reset=0.677 tags=poodle
- `2b90d` Us Against The World [ExpertPlus] dist=2331.116 nps=10.5876 flow=0.348 reset=0.652 tags=poodle
- `12498` Signager [Normal] dist=2134.775 nps=6.0866 flow=0.9158 reset=0.0842 tags=

## Universal good-map rules

1. **Parity / flow first.** Expert median `flow_ratio` is 0.956. Each hand should mostly swing opposite the previous cut; resets are seasoning, not the meal.
2. **Hands stay home.** Median `cross_ratio` 0.254. Left lives in columns 0–1, right in 2–3, with rare intentional crosses.
3. **Doubles are accents.** Median `jump_ratio` 0.295. Place two-hand hits on strong onsets / high energy, not as default density padding.
4. **Section contrast.** Median `section_contrast` 0.225. Intros and outros must be thinner than drops even when the song is loud the whole way.
5. **Motifs repeat.** Median `motif_reuse` 0.597. Verse/drop patterns should recur; do not generate independent random notes every bar.
6. **Dots are rare.** Median `dot_ratio` 0.044. Use dots for percussion ornaments, not as an escape from parity.
7. **Same-hand spacing scales with difficulty.** Easy ≥ 1 beat, Hard ≥ ½, Expert ~⅓, Expert+ can stream ¼ in speed maps only.
8. **Bombs and walls support reading, they are not NPS.** Curated maps use them as punctuation in drops, not as a substitute for notes.
9. **Full spreads teach.** When generating multiple diffs, keep the same motif skeleton and thin/subdivide rather than inventing unrelated charts.

## Anti-patterns

- Mixed high-reset+high-flow charts are rare (181/27196); pick a lane: either flow-forward swings or intentional tech resets, not both at once.
- Heavy cross-hand occupancy (>35% of notes) appears in 4922/27196 diffs — keep hands mostly on their sides except in tech/cross styles.
- High-NPS maps with almost flat density curves are uncommon (388/27196); intros/outros should breathe even on Expert+.
- Stacking two notes in the same cell on the same beat.
- Face-plants: both sabers cutting inward/down into each other on inner columns.
- Notes in the first ~2–3 seconds before the player can ready up.
- Reset chains (same cut 3+ times on one hand) outside of tech maps.
- Ignoring the kick/snare grid: off-grid spam that does not lock to 1/4 or 1/8.

## Encoded into Beatforge

Difficulty ladders (`src/beatforge/styles.py`) use corpus median NPS:

| Diff | target NPS | subdivision | min hand gap (beats) | NJS |
|---|---:|---:|---:|---:|
| Easy | 1.59 | 1.0 | 1.0 | 10.0 |
| Normal | 2.5 | 1.0 | 0.75 | 10.0 |
| Hard | 3.27 | 0.5 | 0.5 | 12.0 |
| Expert | 4.25 | 0.5 | 0.35 | 16.0 |
| ExpertPlus | 5.47 | 0.5 | 0.25 | 18.0 |

Style profiles (flow / tech / speed / chill) are tuned to measured style-family medians (`data/corpus/style_family_medians.json`): tech runs ~54% same-cut transitions, speed streams at ~0.61 with low lane changes, chill avoids bombs entirely.

Full spreads are **additive**: difficulties are placed easiest-first and each tier inherits every note of the previous tier (re-deriving cuts in its own context), then escalates inherited singles into doubles. Nesting is verified by the critic's spread check (rule 9).

## Reproduction

```bash
python tools/corpus/fetch_beatsaver.py
python tools/corpus/compare_styles.py
```

