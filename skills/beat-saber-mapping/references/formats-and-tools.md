# Formats and tools

Use this reference when creating or editing map files, selecting an editor, converting schemas, declaring mods, or diagnosing package failures.

## Package shape

`Info.dat` is the exact entrypoint filename. It points to the audio, cover, audio-data, difficulty, and lightshow files. Keep those referenced files at the map root. A BeatSaver upload zip should contain the files at its root, not inside an extra enclosing folder.

Use OGG audio for broad custom-map upload compatibility. Use a square PNG or JPG cover. Check the current upload service rules before release.

Do not hand-edit compressed official assets. Work on an uncompressed project copy and let the chosen editor or packaging tool write its supported form.

## Choose a schema deliberately

- v2 is legacy. Preserve it for repairs only when conversion would break the user's toolchain or custom data.
- v3 stores interactable objects and lighting in each difficulty file. It supports color notes, bombs, obstacles, arcs in `sliders`, and chains in `burstSliders`.
- v4 splits interactable beatmap data from non-interactable lightshow data. Its gameplay collections use event records that reference shared metadata arrays. Do not flatten or renumber those indices casually.

For an existing map, preserve its schema unless conversion is part of the request. For a new map, check the current editor, game, BeatSaver, mod, and leaderboard support. Newest does not mean most compatible.

In v4, explicitly include `colorNotes`, `bombNotes`, and `obstacles`, even when empty, so selection-screen counts can display correctly.

## Core v3 object fields

These are compact field reminders, not a complete schema:

```json
{
  "version": "3.3.0",
  "colorNotes": [{"b": 8, "x": 1, "y": 1, "c": 0, "d": 1, "a": 0}],
  "bombNotes": [{"b": 9, "x": 2, "y": 0}],
  "obstacles": [{"b": 12, "d": 2, "x": 0, "y": 0, "w": 1, "h": 5}],
  "sliders": [{"c": 1, "b": 16, "x": 2, "y": 0, "d": 0, "mu": 1, "tb": 18, "tx": 3, "ty": 2, "tc": 0, "tmu": 1, "m": 0}],
  "burstSliders": [{"c": 0, "b": 20, "x": 1, "y": 2, "d": 1, "tb": 20.5, "tx": 1, "ty": 0, "sc": 4, "s": 0.5}]
}
```

`b` and `tb` are beat positions. `x` is normally lane 0 through 3 and `y` row 0 through 2. `c` is saber color 0 or 1. `d` is cut direction 0 through 8, with 8 meaning any direction. Obstacle `d` is duration, not cut direction. Arc multipliers are `mu` and `tmu`. Chain `sc` is slice count and `s` is squish.

## v4 indirection

In v4, timed object arrays refer to metadata arrays. For example, each `colorNotes` item has a beat and an `i` index into `colorNotesData`. Arcs refer to head and tail color-note metadata plus `arcsData`; chains refer to head note metadata plus `chainsData`. Validate every index after adding, removing, or sorting shared data.

The v4 Info file can reference separate beatmap and lightshow filenames for each difficulty. A v3 difficulty carries its own lighting, so a v4 lightshow reference does not replace lighting for a v3 difficulty.

Read the current schema before writing raw JSON:

- [BSMG map-format overview](https://bsmg.wiki/mapping/map-format.html)
- [BSMG Info schema](https://bsmg.wiki/mapping/map-format/info.html)
- [BSMG beatmap schema](https://bsmg.wiki/mapping/map-format/beatmap.html)
- [BSMG audio schema](https://bsmg.wiki/mapping/map-format/audio.html)
- [BSMG lightshow schema](https://bsmg.wiki/mapping/map-format/lightshow.html)

## Editors and previews

Prefer an editor that natively supports the chosen schema and object types. The [official level editor documentation](https://beatsaber.com/documentation.html) covers notes, bombs, walls, arcs, chains, static events, and group lighting. Community editors include [ChroMapper](https://chromapper.atlassian.net/wiki/) and [Beatmapper](https://beatmapper.app/). Verify current format support before starting a project or conversion.

Use a geometrically accurate preview such as [ArcViewer](https://allpoland.github.io/ArcViewer/) to inspect vision, arcs, walls, spawn behavior, and crouched eye height. A preview helps, but only the game reproduces the full scoring and headset experience.

## Modded maps

Custom data can require SongCore, Chroma, Noodle Extensions, Mapping Extensions, Cinema, or another mod. Only add modded features when requested. Record requirements and suggestions in the schema-appropriate custom-data fields and in the delivery manifest.

Test the exact PC or Quest mod stack the user targets. Do not assume a PC extension works on Quest or in an unmodded game. Keep a vanilla fallback only if the user asks for one.

## Editing rules

- Back up before schema conversion and compare object counts afterward.
- Preserve unknown custom-data keys unless their removal is explicitly requested.
- Keep collection timing sorted when the format or tool expects it.
- Avoid floating-point drift. Snap only when the musical grid proves the intended precision.
- Let an editor or schema library serialize complex v4 data when available.
- Never invent an audio checksum, BPM region, or lightshow reference to make a file look complete.
