#!/usr/bin/env python3
"""Read-only structural inspector for Beat Saber custom map folders and ZIPs."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


@dataclass
class Report:
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


class Package:
    def names(self) -> list[str]:
        raise NotImplementedError

    def read(self, name: str) -> bytes:
        raise NotImplementedError


class FolderPackage(Package):
    def __init__(self, root: Path) -> None:
        self.root = root

    def names(self) -> list[str]:
        return [p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()]

    def read(self, name: str) -> bytes:
        return (self.root / PurePosixPath(name)).read_bytes()


class ZipPackage(Package):
    def __init__(self, path: Path) -> None:
        self.zip = zipfile.ZipFile(path)

    def names(self) -> list[str]:
        return [n for n in self.zip.namelist() if not n.endswith("/")]

    def read(self, name: str) -> bytes:
        return self.zip.read(name)


def decode_json(raw: bytes, label: str) -> Any:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{label}: invalid JSON or gzip data: {exc}") from exc


def major_version(data: dict[str, Any]) -> int | None:
    value = data.get("version", data.get("_version"))
    if not isinstance(value, str):
        return None
    try:
        return int(value.split(".", 1)[0])
    except ValueError:
        return None


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_sorted(items: Iterable[dict[str, Any]], beat_key: str, label: str, report: Report) -> None:
    beats = [item.get(beat_key) for item in items]
    numeric = [float(beat) for beat in beats if finite_number(beat)]
    if any(b < a for a, b in zip(numeric, numeric[1:])):
        report.warn(f"{label}: collection is not sorted by beat")


def check_note(note: dict[str, Any], label: str, v2: bool, report: Report) -> None:
    x = note.get("_lineIndex" if v2 else "x")
    y = note.get("_lineLayer" if v2 else "y")
    color = note.get("_type" if v2 else "c")
    direction = note.get("_cutDirection" if v2 else "d")
    if not isinstance(x, int) or isinstance(x, bool) or x not in range(4):
        report.warn(f"{label}: note lane is not an integer from 0 to 3")
    if not isinstance(y, int) or isinstance(y, bool) or y not in range(3):
        report.warn(f"{label}: note row is not an integer from 0 to 2")
    if color not in (0, 1):
        report.warn(f"{label}: note color is not 0 or 1")
    if not isinstance(direction, int) or isinstance(direction, bool) or direction not in range(9):
        report.warn(f"{label}: cut direction is not an integer from 0 to 8")


def check_grid_position(item: dict[str, Any], label: str, x_key: str, y_key: str, report: Report) -> None:
    x = item.get(x_key)
    y = item.get(y_key)
    if not isinstance(x, int) or isinstance(x, bool) or x not in range(4):
        report.warn(f"{label}: lane is not an integer from 0 to 3")
    if not isinstance(y, int) or isinstance(y, bool) or y not in range(3):
        report.warn(f"{label}: row is not an integer from 0 to 2")


def check_tail_order(item: dict[str, Any], label: str, head_key: str, tail_key: str, report: Report) -> None:
    head = item.get(head_key)
    tail = item.get(tail_key)
    if not finite_number(head) or not finite_number(tail):
        report.error(f"{label}: head and tail beats must be finite numbers")
    elif tail < head:
        report.error(f"{label}: tail beat precedes head beat")


def check_index(index: Any, size: int, label: str, report: Report) -> None:
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= size:
        report.error(f"{label}: metadata index {index!r} is outside 0..{max(size - 1, -1)}")


def inspect_v2(data: dict[str, Any], label: str, report: Report) -> dict[str, int]:
    raw_notes = data.get("_notes", [])
    if not isinstance(raw_notes, list):
        report.error(f"{label}: _notes is not an array")
        raw_notes = []
    color_notes = [n for n in raw_notes if isinstance(n, dict) and n.get("_type") in (0, 1)]
    bombs = [n for n in raw_notes if isinstance(n, dict) and n.get("_type") == 3]
    unknown = [n for n in raw_notes if isinstance(n, dict) and n.get("_type") not in (0, 1, 3)]
    for index, note in enumerate(color_notes):
        check_note(note, f"{label} color note {index}", True, report)
    for index, bomb in enumerate(bombs):
        check_grid_position(bomb, f"{label} bomb {index}", "_lineIndex", "_lineLayer", report)
    if unknown:
        report.warn(f"{label}: {len(unknown)} legacy notes have an unknown _type")
    obstacles = data.get("_obstacles", [])
    if not isinstance(obstacles, list):
        report.error(f"{label}: _obstacles is not an array")
        obstacles = []
    for index, wall in enumerate(obstacles):
        if not isinstance(wall, dict):
            report.error(f"{label} obstacle {index}: object is not a JSON object")
            continue
        if not finite_number(wall.get("_duration")) or wall["_duration"] <= 0:
            report.error(f"{label} obstacle {index}: duration must be positive")
        if not finite_number(wall.get("_width")) or wall["_width"] <= 0:
            report.error(f"{label} obstacle {index}: width must be positive")
    check_sorted([n for n in raw_notes if isinstance(n, dict)], "_time", f"{label} notes", report)
    check_sorted([w for w in obstacles if isinstance(w, dict)], "_time", f"{label} obstacles", report)
    for index, arc in enumerate(data.get("_sliders", [])):
        if isinstance(arc, dict):
            check_tail_order(arc, f"{label} arc {index}", "_headTime", "_tailTime", report)
    return {
        "notes": len(color_notes),
        "bombs": len(bombs),
        "walls": len(obstacles),
        "arcs": len(data.get("_sliders", [])) if isinstance(data.get("_sliders", []), list) else 0,
        "chains": len(data.get("_burstSliders", [])) if isinstance(data.get("_burstSliders", []), list) else 0,
    }


def inspect_v3(data: dict[str, Any], label: str, report: Report) -> dict[str, int]:
    keys = ("colorNotes", "bombNotes", "obstacles", "sliders", "burstSliders")
    arrays: dict[str, list[Any]] = {}
    for key in keys:
        value = data.get(key, [])
        if not isinstance(value, list):
            report.error(f"{label}: {key} is not an array")
            value = []
        arrays[key] = value
    for index, note in enumerate(arrays["colorNotes"]):
        if isinstance(note, dict):
            check_note(note, f"{label} color note {index}", False, report)
        else:
            report.error(f"{label} color note {index}: object is not a JSON object")
    for index, bomb in enumerate(arrays["bombNotes"]):
        if isinstance(bomb, dict):
            check_grid_position(bomb, f"{label} bomb {index}", "x", "y", report)
        else:
            report.error(f"{label} bomb {index}: object is not a JSON object")
    for index, wall in enumerate(arrays["obstacles"]):
        if not isinstance(wall, dict):
            report.error(f"{label} obstacle {index}: object is not a JSON object")
            continue
        if not finite_number(wall.get("d")) or wall["d"] <= 0:
            report.error(f"{label} obstacle {index}: duration must be positive")
        if not finite_number(wall.get("w")) or wall["w"] <= 0:
            report.error(f"{label} obstacle {index}: width must be positive")
        if not finite_number(wall.get("h")) or wall["h"] <= 0:
            report.error(f"{label} obstacle {index}: height must be positive")
    for index, chain in enumerate(arrays["burstSliders"]):
        if not isinstance(chain, dict):
            report.error(f"{label} chain {index}: object is not a JSON object")
        elif not isinstance(chain.get("sc"), int) or chain["sc"] < 1:
            report.warn(f"{label} chain {index}: slice count should be a positive integer")
    for index, arc in enumerate(arrays["sliders"]):
        if isinstance(arc, dict):
            check_tail_order(arc, f"{label} arc {index}", "b", "tb", report)
        else:
            report.error(f"{label} arc {index}: object is not a JSON object")
    for index, chain in enumerate(arrays["burstSliders"]):
        if isinstance(chain, dict):
            check_tail_order(chain, f"{label} chain {index}", "b", "tb", report)
    for key in keys:
        beat_key = "b"
        check_sorted([x for x in arrays[key] if isinstance(x, dict)], beat_key, f"{label} {key}", report)
    return {
        "notes": len(arrays["colorNotes"]),
        "bombs": len(arrays["bombNotes"]),
        "walls": len(arrays["obstacles"]),
        "arcs": len(arrays["sliders"]),
        "chains": len(arrays["burstSliders"]),
    }


def inspect_v4(data: dict[str, Any], label: str, report: Report) -> dict[str, int]:
    pairs = {
        "colorNotes": "colorNotesData",
        "bombNotes": "bombNotesData",
        "obstacles": "obstaclesData",
        "arcs": "arcsData",
        "chains": "chainsData",
    }
    arrays: dict[str, list[Any]] = {}
    for events_key, data_key in pairs.items():
        events = data.get(events_key, [])
        metadata = data.get(data_key, [])
        if not isinstance(events, list):
            report.error(f"{label}: {events_key} is not an array")
            events = []
        if not isinstance(metadata, list):
            report.error(f"{label}: {data_key} is not an array")
            metadata = []
        arrays[events_key] = events
        arrays[data_key] = metadata
    for required in ("colorNotes", "bombNotes", "obstacles"):
        if required not in data:
            report.warn(f"{label}: omitted {required}; in-game selection counts may be wrong")
    for index, event in enumerate(arrays["colorNotes"]):
        if not isinstance(event, dict):
            report.error(f"{label} color note {index}: event is not a JSON object")
            continue
        check_index(event.get("i"), len(arrays["colorNotesData"]), f"{label} color note {index}", report)
        if isinstance(event.get("i"), int) and 0 <= event["i"] < len(arrays["colorNotesData"]):
            note = arrays["colorNotesData"][event["i"]]
            if isinstance(note, dict):
                check_note(note, f"{label} color note {index}", False, report)
    for key in ("bombNotes", "obstacles"):
        data_key = pairs[key]
        for index, event in enumerate(arrays[key]):
            if isinstance(event, dict):
                check_index(event.get("i"), len(arrays[data_key]), f"{label} {key} {index}", report)
            else:
                report.error(f"{label} {key} {index}: event is not a JSON object")
    for index, event in enumerate(arrays["bombNotes"]):
        if not isinstance(event, dict):
            continue
        metadata_index = event.get("i")
        if isinstance(metadata_index, int) and 0 <= metadata_index < len(arrays["bombNotesData"]):
            metadata = arrays["bombNotesData"][metadata_index]
            if isinstance(metadata, dict):
                check_grid_position(metadata, f"{label} bomb {index}", "x", "y", report)
    for index, event in enumerate(arrays["obstacles"]):
        if not isinstance(event, dict):
            continue
        metadata_index = event.get("i")
        if isinstance(metadata_index, int) and 0 <= metadata_index < len(arrays["obstaclesData"]):
            wall = arrays["obstaclesData"][metadata_index]
            if not isinstance(wall, dict):
                report.error(f"{label} obstacle {index}: metadata is not a JSON object")
                continue
            for key, name in (("d", "duration"), ("w", "width"), ("h", "height")):
                if not finite_number(wall.get(key)) or wall[key] <= 0:
                    report.error(f"{label} obstacle {index}: {name} must be positive")
    for index, arc in enumerate(arrays["arcs"]):
        if not isinstance(arc, dict):
            report.error(f"{label} arc {index}: event is not a JSON object")
            continue
        check_index(arc.get("hi"), len(arrays["colorNotesData"]), f"{label} arc {index} head", report)
        check_index(arc.get("ti"), len(arrays["colorNotesData"]), f"{label} arc {index} tail", report)
        check_index(arc.get("ai"), len(arrays["arcsData"]), f"{label} arc {index}", report)
        check_tail_order(arc, f"{label} arc {index}", "hb", "tb", report)
    for index, chain in enumerate(arrays["chains"]):
        if not isinstance(chain, dict):
            report.error(f"{label} chain {index}: event is not a JSON object")
            continue
        check_index(chain.get("i"), len(arrays["colorNotesData"]), f"{label} chain {index} head", report)
        check_index(chain.get("ci"), len(arrays["chainsData"]), f"{label} chain {index}", report)
        check_tail_order(chain, f"{label} chain {index}", "hb", "tb", report)
        ci = chain.get("ci")
        if isinstance(ci, int) and 0 <= ci < len(arrays["chainsData"]):
            metadata = arrays["chainsData"][ci]
            if isinstance(metadata, dict) and (not isinstance(metadata.get("c"), int) or metadata["c"] < 1):
                report.warn(f"{label} chain {index}: slice count should be a positive integer")
    for key in pairs:
        beat_key = "hb" if key in ("arcs", "chains") else "b"
        check_sorted([x for x in arrays[key] if isinstance(x, dict)], beat_key, f"{label} {key}", report)
    return {
        "notes": len(arrays["colorNotes"]),
        "bombs": len(arrays["bombNotes"]),
        "walls": len(arrays["obstacles"]),
        "arcs": len(arrays["arcs"]),
        "chains": len(arrays["chains"]),
    }


def referenced_files(info: dict[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    files: list[tuple[str, str]] = []
    maps: list[tuple[str, str, str]] = []
    major = major_version(info)
    if major == 2:
        for field, role in (("_songFilename", "audio"), ("_coverImageFilename", "cover")):
            value = info.get(field)
            if isinstance(value, str):
                files.append((value, role))
        for beatmap_set in info.get("_difficultyBeatmapSets", []):
            if not isinstance(beatmap_set, dict):
                continue
            characteristic = str(beatmap_set.get("_beatmapCharacteristicName", "Unknown"))
            for difficulty in beatmap_set.get("_difficultyBeatmaps", []):
                if not isinstance(difficulty, dict):
                    continue
                name = difficulty.get("_beatmapFilename")
                level = str(difficulty.get("_difficulty", "Unknown"))
                if isinstance(name, str):
                    maps.append((name, characteristic, level))
    elif major == 4:
        song = info.get("audio", {})
        if isinstance(song, dict):
            for field, role in (("songFilename", "audio"), ("audioDataFilename", "audio data")):
                value = song.get(field)
                if isinstance(value, str):
                    files.append((value, role))
        for field, role in (("songPreviewFilename", "preview audio"), ("coverImageFilename", "cover")):
            value = info.get(field)
            if isinstance(value, str):
                files.append((value, role))
        for difficulty in info.get("difficultyBeatmaps", []):
            if not isinstance(difficulty, dict):
                continue
            characteristic = str(difficulty.get("characteristic", "Unknown"))
            level = str(difficulty.get("difficulty", "Unknown"))
            name = difficulty.get("beatmapDataFilename")
            if isinstance(name, str):
                maps.append((name, characteristic, level))
            lightshow = difficulty.get("lightshowDataFilename")
            if isinstance(lightshow, str):
                files.append((lightshow, f"{characteristic} {level} lightshow"))
    return files, maps


def inspect_package(path: Path) -> int:
    report = Report()
    package: Package
    if path.is_dir():
        package = FolderPackage(path)
    elif path.is_file() and zipfile.is_zipfile(path):
        package = ZipPackage(path)
    else:
        print(f"ERROR: {path} is not a directory or ZIP archive", file=sys.stderr)
        return 2

    names = package.names()
    exact = set(names)
    folded: dict[str, list[str]] = {}
    for name in names:
        folded.setdefault(name.casefold(), []).append(name)

    info_name = "Info.dat"
    if info_name not in exact:
        candidates = folded.get(info_name.casefold(), [])
        nested = [name for name in names if PurePosixPath(name).name.casefold() == "info.dat"]
        if candidates:
            report.error(f"Info.dat has incorrect case: {candidates[0]}")
            info_name = candidates[0]
        elif nested:
            report.error(f"Info.dat is nested instead of at package root: {nested[0]}")
            info_name = nested[0]
        else:
            print("ERROR: package has no Info.dat", file=sys.stderr)
            return 2

    try:
        info = decode_json(package.read(info_name), info_name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not isinstance(info, dict):
        print("ERROR: Info.dat root is not a JSON object", file=sys.stderr)
        return 2
    info_major = major_version(info)
    if info_major not in (2, 4):
        report.error(f"Info.dat: unsupported or missing schema version {info.get('version', info.get('_version'))!r}")

    files, beatmaps = referenced_files(info)
    for name, role in files:
        if name not in exact:
            alternatives = folded.get(name.casefold(), [])
            if alternatives:
                report.error(f"{role} filename case mismatch: referenced {name!r}, found {alternatives[0]!r}")
            else:
                report.error(f"missing referenced {role} file: {name}")

    print(f"Package: {path}")
    print(f"Info schema: {info.get('version', info.get('_version', 'unknown'))}")
    if not beatmaps:
        report.error("Info.dat references no difficulty beatmaps")
    for name, characteristic, difficulty in beatmaps:
        if name not in exact:
            alternatives = folded.get(name.casefold(), [])
            if alternatives:
                report.error(f"beatmap filename case mismatch: referenced {name!r}, found {alternatives[0]!r}")
            else:
                report.error(f"missing referenced beatmap: {name}")
            continue
        try:
            data = decode_json(package.read(name), name)
        except ValueError as exc:
            report.error(str(exc))
            continue
        if not isinstance(data, dict):
            report.error(f"{name}: root is not a JSON object")
            continue
        version = major_version(data)
        if version == 2:
            counts = inspect_v2(data, name, report)
        elif version == 3:
            counts = inspect_v3(data, name, report)
        elif version == 4:
            counts = inspect_v4(data, name, report)
        else:
            report.error(f"{name}: unsupported or missing schema version")
            continue
        summary = ", ".join(f"{key}={value}" for key, value in counts.items())
        print(f"- {characteristic} {difficulty}: {name} [v{version}] {summary}")

    for message in report.errors:
        print(f"ERROR: {message}")
    for message in report.warnings:
        print(f"WARN: {message}")
    print(f"Result: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 1 if report.errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Map folder or ZIP archive")
    return inspect_package(parser.parse_args().package.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
