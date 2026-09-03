# Beat Saber beatmap layout

This is what a custom map *is*. Beatforge reads community maps this way; it never copies their note sequences into generated charts.

High-rated charts (≥85% upvote ratio) from the local corpus can be packed for study with `python tools/corpus/export_layout_pack.py`:

| Path (under gitignored `data/corpus/layout-pack/`) | What it is |
|---|---|
| `charts-*.jsonl.gz` | One JSON object per line: every **Standard** difficulty as compact note tuples (sharded) |
| `full/<id>/` | A handful of complete `Info.dat` + `Expert.dat` (etc.) files to open in an editor |
| `index.json` | Counts, example ids, field legend |

Third-party map contents stay local: nothing under `data/corpus/` is committed. The raw `data/corpus/maps/` dump (tens of GB) also stays gitignored.

## Files in a map folder

```
SongName/
  Info.dat          metadata, BPM, list of difficulty files
  Easy.dat          note/bomb/wall/lighting for that tier
  Normal.dat
  Hard.dat
  Expert.dat
  ExpertPlus.dat
  song.ogg          audio (not stored in this repo)
  cover.jpg         art (not stored in this repo)
```

`Info.dat` (schema 2.x) points at difficulties:

```json
{
  "_version": "2.0.0",
  "_songName": "Title",
  "_songAuthorName": "Artist",
  "_beatsPerMinute": 128,
  "_songFilename": "song.ogg",
  "_difficultyBeatmapSets": [
    {
      "_beatmapCharacteristicName": "Standard",
      "_difficultyBeatmaps": [
        {
          "_difficulty": "Expert",
          "_difficultyRank": 7,
          "_beatmapFilename": "Expert.dat",
          "_noteJumpMovementSpeed": 16
        }
      ]
    }
  ]
}
```

## A note (v3 difficulty file)

```json
{ "b": 16.0, "x": 1, "y": 0, "c": 0, "d": 1 }
```

| Field | Meaning |
|---|---|
| `b` | Time in **beats** (not seconds). seconds = `b * 60 / bpm` |
| `x` | Column 0–3 (left → right) |
| `y` | Row 0–2 (bottom → top) |
| `c` | Saber: `0` red/left, `1` blue/right |
| `d` | Cut direction (see below) |

v2 uses `_time`, `_lineIndex`, `_lineLayer`, `_type`, `_cutDirection`. v4 splits metadata into `colorNotesData` and indexes it from `colorNotes`. `parse_maps.py` normalizes all three.

## Cut directions (`d`)

```
4 (up-left)    0 (up)    5 (up-right)
2 (left)       8 (dot)   3 (right)
6 (down-left)  1 (down)  7 (down-right)
```

Dot (`8`) has no required swing angle. Flow maps chain opposites (down then up). Tech maps repeat a cut on purpose (a "reset").

## Compact tuple in `charts.jsonl.gz`

Each line is one map. `diffs[].notes` are arrays:

```text
[beat, x, y, color, cut]
```

Same for bombs `[beat, x, y]` and walls `[beat, x, y, durationBeats, width, height]`.

Hands stay mostly on their side: left `x` in `{0,1}`, right `x` in `{2,3}`. A jump is two notes at the same `b` with different `c`.

## What good maps do (from the ≥85% slice)

See `docs/style-study.md`. Short version: high `flow_ratio`, rare resets, density follows sections, motifs repeat, Easy→Expert+ is the same skeleton with more subdivision.
