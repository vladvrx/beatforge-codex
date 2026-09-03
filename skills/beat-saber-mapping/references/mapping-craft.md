# Mapping craft

Use this reference when designing patterns, arcs, chains, bombs, walls, difficulty spreads, or movement.

## Timing and musical selection

Fix the beat grid first. Confirm the first downbeat, BPM, meter, offset, and any tempo drift or tempo changes against transients throughout the whole song. A grid that matches the intro but drifts by the final chorus is not usable.

Map a deliberate musical layer. Common choices are drums, bass, lead, vocals, and accent effects. A section can switch focus, but the change should follow the arrangement. Use silence and sparse passages. More objects do not automatically create more intensity.

Start with timing notes if the rhythm is uncertain. Replace them with final patterns only after editor hit sounds line up at several points in the song.

## Flow, parity, and readability

Treat every hand as a sequence of forehand and backhand swings. Preserve parity by default. A reset is a distinct extra motion, so give it time, a visible cue, and a musical reason.

For each pattern, trace:

1. The hand position and saber direction before the first note.
2. The cut path and follow-through for every note.
3. The hand position left by the final note.
4. The time and route into the next pattern.

At higher effective BPM, favor clearer and more linear motion. Slower sections can support larger rotations, curved swings, wider reaches, and body movement. Do not make difficulty by obscuring the intended action.

Fresh sight reads are the real readability test. The mapper already knows the solution and will miss clues that are absent.

## Notes and ordinary sliders

Use single notes as the base level of emphasis. Doubles, stacks, windows, towers, and multi-note sliders add physical and visual weight. Keep emphasis consistent with the music so the largest patterns remain meaningful. A red/blue pair on one beat should often cut the same way so both sabers can swing together; opposite-facing windows are an accent, not every double. An arc or chain is one saber through the whole sustain: head and tail are the same color, and the idle hand may cut in the middle of a long hold, never as the tail destination. Do not place a second selected note on the tail beat.

Dots still imply a likely swing through context. Use them to allow freedom, not to avoid choosing a readable motion. They are resets, not the default vocabulary. Same-hand ordinary-note sliders should follow one cut path. Abrupt angle changes or inconsistent spacing turn one intended swing into several awkward corrections.

Pre-AI generation must:

- Put blocks in the opening window after a one-second read delay. If the onset detector sleeps through a fade-in, keep mapping the beat grid from that first readable beat. A first note after ten seconds is a generator bug, not a style choice. A note on beat 0 is also a bug: the player will miss it.
- Build short combos (return cuts, alternating lanes, stacked downs/ups). Do not tile one cut across the grid until SAT gives up and dumps dots.
- Keep sabers readable. Palette from cover art stays; do not "fix" color by replacing swings with dots.
- Alternate hands on hits closer than a beat so combos are left-right, not one saber reversing every half beat.
- Same-hand returns should be 180° on the swing axis (down then up). A 135° diagonal invert (down-right then up-left) is a wrist snap, not a combo.
- Keep the difficulty ladder. Hard stays on 8th-note spacing. Expert may be a bit busier than Hard but must not collapse into Expert+ 16ths. Consecutive blocks closer than the difficulty min gap (except true doubles on the same beat) are a generator bug.

Check face-level center notes, crossed hands, handclaps, stacked timing, and same-lane followups for vision and collision risk.

## Arcs, the sustained-note mechanic

Use an arc for a held vocal, synth glide, reverb tail, legato phrase, or a transition whose hand path matters. It can connect two notes or end without a tail note when the format and editor support that.

The head cut starts the motion. Curve direction and control-point multipliers shape the route. The tail position and direction must agree with the hand state the arc teaches. Keep the underlying head-to-tail transition readable even without the beam.

Arcs can improve continuity, but they also create clutter and vision blocks. Reserve them for selected phrases. Inspect the curve in the chosen environment because environment geometry can clamp or flatten large arcs.

Do not instruct the player to freeze the saber on an arc. The beam magnetizes and guides. It is not a strict hold-note rail.

## Chains, the burst mechanic

Use a chain for a burst, roll, rasp, rapid texture, or a single accented motion with internal detail. The head establishes color, direction, timing, and scoring behavior. The links continue toward the tail.

Set tail placement, link count, and squish so the chain reads as one intended slice. More links increase visual and scoring density. Very compressed or long diagonal chains need collision and accuracy testing. Avoid zero-link tricks unless the user explicitly wants unsupported behavior.

Use an arc when the sound sustains. Use a chain when the sound breaks into a short burst. Use ordinary-note sliders when the music contains several discrete hits that should share one swing.

## Bombs

Assign every bomb one role:

- Cosmetic bombs mark a sound or create tension without entering either saber's likely path.
- Bomb holds occupy the natural reset area and tell the player to keep a hand where the prior swing left it.
- Bomb resets push the saber away from its post-swing position so the next note can use new parity.

Bombs have weak directional information. Establish the map's bomb language early and use it consistently. Account for pre-swing, follow-through, and the small motion back toward neutral between notes. Leave space around active swings unless deliberate advanced play has been clearly taught and tested.

Reset bombs usually belong closer to the next swing than the previous one because the reset motion happens before the bombs reach the player. Bottom resets are easier to read than top resets. Bomb spirals should chase the saber's current route and finish with clear parity.

Keep bombs visible through lighting and never hide them inside or behind a wall.

## Walls and body movement

Walls act on the head, not the sabers. Side walls can teach a dodge, lean, or regular sway. Overhead walls can trigger a crouch. Cosmetic walls can frame the playfield or represent a sound when the target format supports them.

Body motion happens before wall contact. Place the wall after the musical cue by enough time for the target player to react. Coordinate nearby notes with the motion. A down swing can help a crouch; a top-row backhand just before one fights it.

Crouches are tiring and alter eye height, reach, and vision. Space them generously, signpost them, and avoid immediate top-row demands as the player stands. Side-to-side sway also has a physical speed limit. Test at the intended NJS rather than judging walls on a static timeline.

Never make a wall combination that forces damage or removes every safe body position.

## Difficulty and jump settings

Define difficulty by the player's expected motion vocabulary:

- Rhythmic precision and effective BPM.
- Pattern complexity, angles, rotations, resets, and hand independence.
- Reading load, vision, NJS, and reaction time.
- Reach, stamina, leaning, swaying, dodging, and crouching.

For lower difficulties, keep the song's identity but rewrite the motion. Reduce simultaneous demands, precision, resets, reach, row changes, hazards, and reading load. Give more recovery time. Do not preserve a hard pattern by deleting a random half of its notes.

NJS controls approach speed and timing strictness. Spawn offset changes reaction time and visible note spacing. Start conservatively, then raise NJS only when the map's real density needs more separation. Re-evaluate jump distance after changing BPM data, NJS, offset, or difficulty density.

## Lighting

Choose an environment before detailed lighting because event groups and geometry differ. Establish a base state so notes, walls, and bombs remain visible. Use color, brightness, rotation, boost, and flashes to follow the arrangement without competing with gameplay.

Check rapid flashes, long darkness, strong contrast changes, and hazard visibility in headset. Official post-2022 Standard maps (Electronic Mixtape, OST 5–8, Daft Punk) keep a continuous light clock on the beat grid, not only on note onsets: rings and boost follow section changes, side lasers answer accents, and event-box color/rotation groups keep the environment moving between hits. If automated lighting is used, edit its musical phrasing and safety problems rather than shipping it untouched.

## Primary references

- [Official Beat Saber level-editor documentation](https://beatsaber.com/documentation.html)
- [Official object placement guide](https://beatsaber.com/documentation/placing-notes/index.html)
- [Official terminology](https://beatsaber.com/documentation/terminology/index.html)
- [BSMG basic mapping](https://bsmg.wiki/mapping/basic-mapping.html)
- [BSMG intermediate mapping](https://bsmg.wiki/mapping/intermediate-mapping.html)
- [BSMG downmapping](https://bsmg.wiki/mapping/downmapping.html)
