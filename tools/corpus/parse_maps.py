"""Normalize Beat Saber v2 / v3 / v4 notes, bombs, and obstacles."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass
class ParsedNote:
    beat: float
    x: int
    y: int
    color: int  # 0 left, 1 right
    cut: int


@dataclass
class ParsedBomb:
    beat: float
    x: int
    y: int


@dataclass
class ParsedWall:
    beat: float
    x: int
    y: int
    duration: float
    width: int
    height: int


@dataclass
class ParsedDifficulty:
    characteristic: str
    difficulty: str
    filename: str
    njs: float
    offset: float
    notes: list[ParsedNote]
    bombs: list[ParsedBomb]
    walls: list[ParsedWall]
    version: str


@dataclass
class ParsedMap:
    map_id: str
    title: str
    artist: str
    bpm: float
    duration: float
    difficulties: list[ParsedDifficulty]
    info_version: str


def load_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_info_beatmaps(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield difficulty descriptors from Info.dat v2 or v4."""
    out: list[dict[str, Any]] = []
    sets = info.get("_difficultyBeatmapSets") or info.get("difficultyBeatmapSets") or []
    for s in sets:
        char = s.get("_beatmapCharacteristicName") or s.get("beatmapCharacteristicName") or "Standard"
        for d in s.get("_difficultyBeatmaps") or s.get("difficultyBeatmaps") or []:
            out.append(
                {
                    "characteristic": char,
                    "difficulty": d.get("_difficulty") or d.get("difficulty") or "Expert",
                    "filename": d.get("_beatmapFilename")
                    or d.get("beatmapFilename")
                    or d.get("beatmapDataFilename")
                    or "",
                    "njs": _num(d.get("_noteJumpMovementSpeed") or d.get("noteJumpMovementSpeed"), 10),
                    "offset": _num(
                        d.get("_noteJumpStartBeatOffset") or d.get("noteJumpStartBeatOffset"), 0
                    ),
                }
            )
    # v4 flat list
    for d in info.get("difficultyBeatmaps") or []:
        out.append(
            {
                "characteristic": d.get("characteristic") or "Standard",
                "difficulty": d.get("difficulty") or "Expert",
                "filename": d.get("beatmapDataFilename") or d.get("beatmapFilename") or "",
                "njs": _num(d.get("noteJumpMovementSpeed"), 10),
                "offset": _num(d.get("noteJumpStartBeatOffset"), 0),
            }
        )
    # de-dupe by characteristic+difficulty+filename
    seen: set[tuple[str, str, str]] = set()
    uniq: list[dict[str, Any]] = []
    for d in out:
        key = (d["characteristic"], d["difficulty"], d["filename"])
        if key in seen or not d["filename"]:
            continue
        seen.add(key)
        uniq.append(d)
    return uniq


def parse_difficulty_dat(data: dict[str, Any]) -> tuple[list[ParsedNote], list[ParsedBomb], list[ParsedWall], str]:
    version = str(data.get("version") or data.get("_version") or "")
    if data.get("colorNotesData") is not None or (version.startswith("4")):
        return _parse_v4(data) + (version or "4.0.0",)
    if data.get("colorNotes") is not None or version.startswith("3"):
        return _parse_v3(data) + (version or "3.0.0",)
    return _parse_v2(data) + (version or "2.0.0",)


def _lookup(data_arr: list[Any] | None, idx: int) -> dict[str, Any]:
    if not data_arr:
        return {}
    if 0 <= idx < len(data_arr) and isinstance(data_arr[idx], dict):
        return data_arr[idx]
    return {}


def _parse_v4(data: dict[str, Any]) -> tuple[list[ParsedNote], list[ParsedBomb], list[ParsedWall]]:
    notes: list[ParsedNote] = []
    ndata = data.get("colorNotesData") or []
    for n in data.get("colorNotes") or []:
        meta = _lookup(ndata, _int(n.get("i")))
        # Some files inline x/y/c/d on the note itself
        x = _int(n.get("x", meta.get("x", 0)))
        y = _int(n.get("y", meta.get("y", 0)))
        c = _int(n.get("c", meta.get("c", 0)))
        d = _int(n.get("d", meta.get("d", 1)))
        if c not in (0, 1):
            continue
        notes.append(ParsedNote(beat=_num(n.get("b")), x=x, y=y, color=c, cut=d))

    bombs: list[ParsedBomb] = []
    bdata = data.get("bombNotesData") or []
    for b in data.get("bombNotes") or []:
        meta = _lookup(bdata, _int(b.get("i")))
        bombs.append(
            ParsedBomb(
                beat=_num(b.get("b")),
                x=_int(b.get("x", meta.get("x", 0))),
                y=_int(b.get("y", meta.get("y", 0))),
            )
        )

    walls: list[ParsedWall] = []
    wdata = data.get("obstaclesData") or []
    for w in data.get("obstacles") or []:
        meta = _lookup(wdata, _int(w.get("i")))
        walls.append(
            ParsedWall(
                beat=_num(w.get("b")),
                x=_int(w.get("x", meta.get("x", 0))),
                y=_int(w.get("y", meta.get("y", 0))),
                duration=_num(w.get("d", meta.get("d", 1))),
                width=_int(w.get("w", meta.get("w", 1))),
                height=_int(w.get("h", meta.get("h", 3))),
            )
        )
    return notes, bombs, walls


def _parse_v3(data: dict[str, Any]) -> tuple[list[ParsedNote], list[ParsedBomb], list[ParsedWall]]:
    notes: list[ParsedNote] = []
    for n in data.get("colorNotes") or []:
        c = _int(n.get("c"))
        if c not in (0, 1):
            continue
        notes.append(
            ParsedNote(
                beat=_num(n.get("b")),
                x=_int(n.get("x")),
                y=_int(n.get("y")),
                color=c,
                cut=_int(n.get("d"), 1),
            )
        )
    bombs = [
        ParsedBomb(beat=_num(b.get("b")), x=_int(b.get("x")), y=_int(b.get("y")))
        for b in data.get("bombNotes") or []
    ]
    walls = [
        ParsedWall(
            beat=_num(w.get("b")),
            x=_int(w.get("x")),
            y=_int(w.get("y")),
            duration=_num(w.get("d"), 1),
            width=_int(w.get("w"), 1),
            height=_int(w.get("h"), 3),
        )
        for w in data.get("obstacles") or []
    ]
    return notes, bombs, walls


def _parse_v2(data: dict[str, Any]) -> tuple[list[ParsedNote], list[ParsedBomb], list[ParsedWall]]:
    notes: list[ParsedNote] = []
    bombs: list[ParsedBomb] = []
    for n in data.get("_notes") or []:
        t = _int(n.get("_type"))
        beat = _num(n.get("_time"))
        x = _int(n.get("_lineIndex"))
        y = _int(n.get("_lineLayer"))
        if t == 3:
            bombs.append(ParsedBomb(beat=beat, x=x, y=y))
        elif t in (0, 1):
            notes.append(
                ParsedNote(
                    beat=beat,
                    x=x,
                    y=y,
                    color=t,
                    cut=_int(n.get("_cutDirection"), 1),
                )
            )
    walls: list[ParsedWall] = []
    for w in data.get("_obstacles") or []:
        wtype = _int(w.get("_type"))
        # type 0 full wall (h=5 historically), type 1 crouch (top)
        if wtype == 1:
            y, h = 2, 3
        else:
            y, h = 0, 5
        walls.append(
            ParsedWall(
                beat=_num(w.get("_time")),
                x=_int(w.get("_lineIndex")),
                y=y,
                duration=_num(w.get("_duration"), 1),
                width=_int(w.get("_width"), 1),
                height=h,
            )
        )
    return notes, bombs, walls


def find_info_dat(folder: Path) -> Path | None:
    for cand in folder.rglob("*"):
        if cand.is_file() and cand.name.lower() == "info.dat":
            return cand
    return None


def parse_map_folder(folder: Path, map_id: str = "") -> ParsedMap | None:
    info_path = find_info_dat(folder)
    if info_path is None:
        return None
    info = load_json(info_path)
    bpm = _num(
        info.get("_beatsPerMinute")
        or (info.get("audio") or {}).get("bpm")
        or info.get("bpm"),
        120,
    )
    duration = _num(
        (info.get("audio") or {}).get("songDuration") or info.get("_songDuration"),
        0,
    )
    title = (
        info.get("_songName")
        or (info.get("song") or {}).get("title")
        or folder.name
    )
    artist = (
        info.get("_songAuthorName")
        or (info.get("song") or {}).get("author")
        or "Unknown"
    )
    info_version = str(info.get("_version") or info.get("version") or "2.0.0")
    diffs: list[ParsedDifficulty] = []
    base = info_path.parent
    for spec in parse_info_beatmaps(info):
        dat_path = base / spec["filename"]
        if not dat_path.is_file():
            # case-insensitive fallback
            matches = [p for p in base.glob("*.dat") if p.name.lower() == spec["filename"].lower()]
            dat_path = matches[0] if matches else dat_path
        if not dat_path.is_file():
            continue
        try:
            raw = load_json(dat_path)
            notes, bombs, walls, ver = parse_difficulty_dat(raw)
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        diffs.append(
            ParsedDifficulty(
                characteristic=spec["characteristic"],
                difficulty=spec["difficulty"],
                filename=spec["filename"],
                njs=spec["njs"],
                offset=spec["offset"],
                notes=notes,
                bombs=bombs,
                walls=walls,
                version=ver,
            )
        )
    return ParsedMap(
        map_id=map_id or folder.name,
        title=str(title),
        artist=str(artist),
        bpm=bpm,
        duration=duration,
        difficulties=diffs,
        info_version=info_version,
    )


def iter_standard(parsed: ParsedMap) -> Iterator[ParsedDifficulty]:
    for d in parsed.difficulties:
        if d.characteristic.lower() == "standard":
            yield d


def note_as_dict(n: ParsedNote) -> dict[str, Any]:
    return asdict(n)
