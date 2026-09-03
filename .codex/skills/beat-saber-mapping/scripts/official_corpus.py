#!/usr/bin/env python3
"""Index locally installed first-party Beat Saber maps into a private corpus.

Raw official assets never leave the local cache. The distributable skill only
contains this importer and derived schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from beatforge_core import (
    INTERNAL_LEVEL_IDS,
    TOOL_VERSION,
    default_cache_dir,
    direction_vector,
    load_json_bytes,
    quantile,
    sha256_file,
)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS corpus_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bundles (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    source TEXT NOT NULL,
    pack_id TEXT,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    indexed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pack_definitions (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    status TEXT NOT NULL,
    level_count INTEGER NOT NULL,
    error TEXT,
    indexed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id TEXT NOT NULL REFERENCES bundles(id) ON DELETE CASCADE,
    title TEXT,
    artist TEXT,
    mapper TEXT,
    bpm REAL,
    format INTEGER,
    era_weight REAL NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS difficulties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level_id INTEGER NOT NULL REFERENCES levels(id) ON DELETE CASCADE,
    characteristic TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    njs REAL,
    spawn_offset REAL,
    notes INTEGER NOT NULL,
    bombs INTEGER NOT NULL,
    walls INTEGER NOT NULL,
    arcs INTEGER NOT NULL,
    chains INTEGER NOT NULL,
    light_events INTEGER NOT NULL,
    features_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    difficulty_id INTEGER NOT NULL REFERENCES difficulties(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    beat REAL NOT NULL,
    tail_beat REAL,
    color INTEGER,
    x INTEGER,
    y INTEGER,
    direction INTEGER,
    tail_x INTEGER,
    tail_y INTEGER,
    tail_direction INTEGER,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exclusions (
    bundle_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS style_profiles (
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    characteristic TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    profile_json TEXT NOT NULL,
    PRIMARY KEY (scope, scope_id, difficulty, characteristic)
);
CREATE TABLE IF NOT EXISTS difficulty_transformations (
    level_id INTEGER NOT NULL REFERENCES levels(id) ON DELETE CASCADE,
    characteristic TEXT NOT NULL,
    source_difficulty TEXT NOT NULL,
    target_difficulty TEXT NOT NULL,
    features_json TEXT NOT NULL,
    PRIMARY KEY (level_id, characteristic, source_difficulty, target_difficulty)
);
CREATE INDEX IF NOT EXISTS event_lookup ON events(difficulty_id, beat, color);
CREATE INDEX IF NOT EXISTS difficulty_lookup ON difficulties(characteristic, difficulty);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    database.row_factory = sqlite3.Row
    database.executescript(SCHEMA)
    return database


def set_meta(database: sqlite3.Connection, key: str, value: Any) -> None:
    database.execute(
        "INSERT INTO corpus_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def get_meta(database: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = database.execute("SELECT value FROM corpus_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def discover(game_root: Path) -> tuple[list[tuple[Path, str]], list[Path]]:
    base = game_root / "Beat Saber_Data" / "StreamingAssets" / "BeatmapLevelsData"
    dlc = game_root / "OC_ASSET_FILES"
    addressables = game_root / "Beat Saber_Data" / "StreamingAssets" / "aa" / "StandaloneWindows64"
    candidates = [(path, "base") for path in sorted(base.glob("*")) if path.is_file()]
    candidates += [(path, "owned_dlc") for path in sorted(dlc.glob("*")) if path.is_file()]
    packs = sorted(addressables.glob("*_pack_assets_all_*.bundle"))
    return candidates, packs


def library_fingerprint(game_root: Path) -> str:
    candidates, packs = discover(game_root)
    rows = []
    for path in [item[0] for item in candidates] + packs:
        stat = path.stat()
        rows.append(f"{path.name}\0{stat.st_size}\0{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()


def _steam_library_roots() -> list[Path]:
    manifests = [
        Path(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"),
        Path(r"C:\Program Files\Steam\steamapps\libraryfolders.vdf"),
    ]
    roots: list[Path] = []
    for manifest in manifests:
        if not manifest.is_file():
            continue
        text = manifest.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            library = Path(bytes(match.group(1), "utf-8").decode("unicode_escape"))
            roots.append(library / "steamapps" / "common" / "Beat Saber")
    return roots


def detect_game_root() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Oculus\Software\Software\hyperbolic-magnetism-beat-saber"),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Beat Saber"),
        Path(r"C:\Program Files\Steam\steamapps\common\Beat Saber"),
        *_steam_library_roots(),
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            pass
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "Beat Saber_Data").is_dir():
            return resolved
    return None


def infer_pack_members(pack_paths: Iterable[Path], level_ids: Iterable[str]) -> dict[str, str]:
    ids = list(level_ids)
    result: dict[str, str] = {}
    for path in pack_paths:
        pack_id = re.sub(r"_pack_assets_all_.*$", "", path.name)
        raw = path.read_bytes().lower()
        for level_id in ids:
            token = level_id.lower().encode("utf-8")
            if token in raw and level_id not in result:
                result[level_id] = pack_id
    return result


def _script_bytes(data: Any) -> bytes:
    script = getattr(data, "m_Script", b"")
    if isinstance(script, bytes):
        return script
    if isinstance(script, str):
        return script.encode("utf-8", "surrogateescape")
    return bytes(script)


def _walk_strings(value: Any) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                yield str(key), item
            else:
                yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def extract_bundle_assets(
    path: Path, include_audio: bool = False
) -> tuple[dict[str, bytes], list[dict[str, Any]], dict[str, bytes], dict[int, str]]:
    try:
        import UnityPy
    except ImportError as exc:
        raise RuntimeError("UnityPy is unavailable; run scripts/bootstrap.py") from exc
    environment = UnityPy.load(str(path))
    text_assets: dict[str, bytes] = {}
    behaviours: list[dict[str, Any]] = []
    audio_assets: dict[str, bytes] = {}
    path_names: dict[int, str] = {}
    for obj in environment.objects:
        type_name = obj.type.name
        try:
            if type_name == "TextAsset":
                data = obj.parse_as_object() if hasattr(obj, "parse_as_object") else obj.read()
                name = str(getattr(data, "m_Name", f"text-{obj.path_id}"))
                text_assets[name] = _script_bytes(data)
                path_names[int(obj.path_id)] = name
            elif type_name == "MonoBehaviour":
                try:
                    tree = obj.parse_as_dict() if hasattr(obj, "parse_as_dict") else obj.read_typetree()
                    if isinstance(tree, dict):
                        tree["_unityPathID"] = int(obj.path_id)
                        behaviours.append(tree)
                except Exception:
                    continue
            elif type_name == "AudioClip" and include_audio:
                data = obj.parse_as_object() if hasattr(obj, "parse_as_object") else obj.read()
                name = str(getattr(data, "m_Name", f"audio-{obj.path_id}"))
                try:
                    samples = data.samples
                    if isinstance(samples, dict):
                        for filename, payload in samples.items():
                            audio_assets[str(filename or name)] = bytes(payload)
                except Exception:
                    pass
        except Exception:
            continue
    return text_assets, behaviours, audio_assets, path_names


def _pack_id_from_path(path: Path) -> str:
    return re.sub(r"_pack_assets_all_.*$", "", path.name)


def index_pack_definitions(
    database: sqlite3.Connection, pack_paths: Iterable[Path]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Extract catalog metadata and record every pack bundle and failure."""
    catalog: dict[str, dict[str, Any]] = {}
    pack_hashes: dict[str, str] = {}
    for path in pack_paths:
        pack_id = _pack_id_from_path(path)
        digest = sha256_file(path)
        pack_hashes[pack_id] = digest
        stat = path.stat()
        try:
            _text, behaviours, _audio, _paths = extract_bundle_assets(path)
            pack_name = pack_id
            pack_order = 0
            for item in behaviours:
                if item.get("_packID"):
                    pack_id = str(item["_packID"])
                    pack_name = str(item.get("_packName", pack_id))
                    pack_hashes[pack_id] = digest
                if isinstance(item.get("_order"), int):
                    pack_order = max(pack_order, int(item["_order"]))
            level_count = 0
            for item in behaviours:
                level_id = item.get("_levelID")
                if not isinstance(level_id, str) or not level_id:
                    continue
                level = dict(item)
                level["_packID"] = pack_id
                level["_packName"] = pack_name
                level["_packBundleHash"] = digest
                level["_packOrder"] = pack_order
                catalog[level_id.casefold()] = level
                level_count += 1
            database.execute(
                "INSERT INTO pack_definitions VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET path=excluded.path,sha256=excluded.sha256,size=excluded.size,mtime_ns=excluded.mtime_ns,status=excluded.status,level_count=excluded.level_count,error=excluded.error,indexed_at=excluded.indexed_at",
                (pack_id, str(path), digest, stat.st_size, stat.st_mtime_ns, "indexed", level_count, None, int(time.time())),
            )
        except Exception as exc:
            database.execute(
                "INSERT INTO pack_definitions VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET path=excluded.path,sha256=excluded.sha256,size=excluded.size,mtime_ns=excluded.mtime_ns,status=excluded.status,level_count=excluded.level_count,error=excluded.error,indexed_at=excluded.indexed_at",
                (pack_id, str(path), digest, stat.st_size, stat.st_mtime_ns, "failed", 0, str(exc), int(time.time())),
            )
    for item in catalog.values():
        item["_eraWeight"] = 0.62 + 0.38 * min(1.0, int(item.get("_packOrder", 0)) / 400.0)
    return catalog, pack_hashes


def json_assets(text_assets: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, raw in text_assets.items():
        try:
            parsed = load_json_bytes(raw, name)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            result[name] = parsed
    return result


def major_version(data: dict[str, Any]) -> int:
    value = data.get("version", data.get("_version", "0"))
    try:
        return int(str(value).split(".", 1)[0])
    except ValueError:
        return 0


def looks_like_info(data: dict[str, Any]) -> bool:
    return "_difficultyBeatmapSets" in data or "difficultyBeatmaps" in data or "song" in data and "audio" in data


def looks_like_beatmap(data: dict[str, Any]) -> bool:
    keys = set(data)
    return bool(keys & {"_notes", "colorNotes", "bombNotes", "arcs", "chains", "sliders", "burstSliders"})


def info_metadata(info: dict[str, Any]) -> dict[str, Any]:
    if major_version(info) >= 4:
        song = info.get("song", {}) if isinstance(info.get("song"), dict) else {}
        audio = info.get("audio", {}) if isinstance(info.get("audio"), dict) else {}
        return {
            "title": song.get("title", song.get("songName", "Unknown")),
            "artist": song.get("author", song.get("songAuthorName", "Unknown")),
            "mapper": info.get("levelAuthor", info.get("levelAuthorName", "Beat Games")),
            "bpm": audio.get("bpm", info.get("beatsPerMinute")),
            "format": major_version(info),
        }
    return {
        "title": info.get("_songName", "Unknown"),
        "artist": info.get("_songAuthorName", "Unknown"),
        "mapper": info.get("_levelAuthorName", "Beat Games"),
        "bpm": info.get("_beatsPerMinute"),
        "format": major_version(info),
    }


def difficulty_refs(info: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if major_version(info) >= 4:
        for entry in info.get("difficultyBeatmaps", []):
            if not isinstance(entry, dict):
                continue
            result.append(
                {
                    "characteristic": str(entry.get("characteristic", "Standard")),
                    "difficulty": str(entry.get("difficulty", "Unknown")),
                    "filename": entry.get("beatmapDataFilename"),
                    "lightshow": entry.get("lightshowDataFilename"),
                    "njs": entry.get("noteJumpMovementSpeed"),
                    "offset": entry.get("noteJumpStartBeatOffset"),
                }
            )
    else:
        for group in info.get("_difficultyBeatmapSets", []):
            if not isinstance(group, dict):
                continue
            characteristic = str(group.get("_beatmapCharacteristicName", "Standard"))
            for entry in group.get("_difficultyBeatmaps", []):
                if not isinstance(entry, dict):
                    continue
                result.append(
                    {
                        "characteristic": characteristic,
                        "difficulty": str(entry.get("_difficulty", "Unknown")),
                        "filename": entry.get("_beatmapFilename"),
                        "lightshow": None,
                        "njs": entry.get("_noteJumpMovementSpeed"),
                        "offset": entry.get("_noteJumpStartBeatOffset"),
                    }
                )
    return result


DIFFICULTY_NAMES = {0: "Easy", 1: "Normal", 2: "Hard", 3: "Expert", 4: "ExpertPlus"}


def _referenced_name(reference: Any, path_names: dict[int, str]) -> str | None:
    if not isinstance(reference, dict):
        return None
    path_id = reference.get("m_PathID")
    return path_names.get(int(path_id)) if isinstance(path_id, int) else None


def official_difficulty_refs(
    level_data: dict[str, Any],
    path_names: dict[int, str],
    catalog_entry: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Join level-bundle object references with pack preview metadata."""
    result: list[dict[str, Any]] = []
    preview_groups = catalog_entry.get("_previewDifficultyBeatmapSets", []) if catalog_entry else []
    for group_index, group in enumerate(level_data.get("_difficultyBeatmapSets", [])):
        if not isinstance(group, dict):
            continue
        characteristic = str(
            group.get("_beatmapCharacteristicSerializedName", group.get("_beatmapCharacteristicName", "Standard"))
        )
        preview_group = preview_groups[group_index] if group_index < len(preview_groups) and isinstance(preview_groups[group_index], dict) else {}
        preview_by_difficulty = {
            item.get("_difficulty"): item
            for item in preview_group.get("_previewDifficultyBeatmaps", [])
            if isinstance(item, dict)
        }
        for entry in group.get("_difficultyBeatmaps", []):
            if not isinstance(entry, dict):
                continue
            difficulty_value = entry.get("_difficulty")
            preview = preview_by_difficulty.get(difficulty_value, {})
            result.append(
                {
                    "characteristic": characteristic,
                    "difficulty": DIFFICULTY_NAMES.get(difficulty_value, str(difficulty_value)),
                    "filename": _referenced_name(entry.get("_beatmapAsset"), path_names),
                    "lightshow": _referenced_name(entry.get("_lightshowAsset"), path_names),
                    "njs": preview.get("_noteJumpMovementSpeed"),
                    "offset": preview.get("_noteJumpStartBeatOffset"),
                }
            )
    return result


def lightshow_event_count(data: dict[str, Any]) -> int:
    version = major_version(data)
    if version >= 4:
        return sum(len(data.get(key, [])) for key in ("basicEvents", "colorBoostEvents", "eventBoxGroups"))
    if version == 3:
        return sum(
            len(data.get(key, []))
            for key in (
                "basicBeatmapEvents",
                "colorBoostBeatmapEvents",
                "lightColorEventBoxGroups",
                "lightRotationEventBoxGroups",
                "lightTranslationEventBoxGroups",
            )
        )
    return len(data.get("_events", []))


def catalog_metadata(bundle_id: str, entry: dict[str, Any] | None, map_format: int) -> dict[str, Any]:
    entry = entry or {}
    return {
        "title": entry.get("_songName", bundle_id),
        "artist": entry.get("_songAuthorName", "Unknown"),
        "mapper": entry.get("_levelAuthorName", "Beat Games"),
        "bpm": entry.get("_beatsPerMinute"),
        "format": map_format,
        "songDuration": entry.get("_songDuration"),
        "songTimeOffset": entry.get("_songTimeOffset", 0.0),
        "packID": entry.get("_packID"),
        "packName": entry.get("_packName"),
        "packOrder": entry.get("_packOrder"),
        "eraWeight": entry.get("_eraWeight"),
        "levelID": entry.get("_levelID", bundle_id),
    }


def _event(kind: str, raw: dict[str, Any], **normalized: Any) -> dict[str, Any]:
    return {"kind": kind, **normalized, "payload": raw}


def normalize_beatmap(data: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    version = major_version(data)
    events: list[dict[str, Any]] = []
    if version <= 2:
        for raw in data.get("_notes", []):
            if not isinstance(raw, dict):
                continue
            note_type = raw.get("_type")
            kind = "bomb" if note_type == 3 else "note"
            events.append(
                _event(
                    kind,
                    raw,
                    beat=float(raw.get("_time", 0.0)),
                    color=None if kind == "bomb" else int(note_type),
                    x=int(raw.get("_lineIndex", 0)),
                    y=int(raw.get("_lineLayer", 0)),
                    direction=None if kind == "bomb" else int(raw.get("_cutDirection", 8)),
                )
            )
        for raw in data.get("_obstacles", []):
            if isinstance(raw, dict):
                events.append(_event("wall", raw, beat=float(raw.get("_time", 0.0)), tail_beat=float(raw.get("_time", 0.0)) + float(raw.get("_duration", 0.0)), x=int(raw.get("_lineIndex", 0)), y=int(raw.get("_type", 0))))
        light_count = len(data.get("_events", []))
    elif version == 3:
        for raw in data.get("colorNotes", []):
            if isinstance(raw, dict):
                events.append(_event("note", raw, beat=float(raw.get("b", 0.0)), color=int(raw.get("c", 0)), x=int(raw.get("x", 0)), y=int(raw.get("y", 0)), direction=int(raw.get("d", 8))))
        for raw in data.get("bombNotes", []):
            if isinstance(raw, dict):
                events.append(_event("bomb", raw, beat=float(raw.get("b", 0.0)), color=None, x=int(raw.get("x", 0)), y=int(raw.get("y", 0)), direction=None))
        for raw in data.get("obstacles", []):
            if isinstance(raw, dict):
                events.append(_event("wall", raw, beat=float(raw.get("b", 0.0)), tail_beat=float(raw.get("b", 0.0)) + float(raw.get("d", 0.0)), x=int(raw.get("x", 0)), y=int(raw.get("y", 0))))
        for raw in data.get("sliders", []):
            if isinstance(raw, dict):
                events.append(_event("arc", raw, beat=float(raw.get("b", 0.0)), tail_beat=float(raw.get("tb", 0.0)), color=int(raw.get("c", 0)), x=int(raw.get("x", 0)), y=int(raw.get("y", 0)), direction=int(raw.get("d", 8)), tail_x=int(raw.get("tx", 0)), tail_y=int(raw.get("ty", 0)), tail_direction=int(raw.get("tc", 8))))
        for raw in data.get("burstSliders", []):
            if isinstance(raw, dict):
                events.append(_event("chain", raw, beat=float(raw.get("b", 0.0)), tail_beat=float(raw.get("tb", 0.0)), color=int(raw.get("c", 0)), x=int(raw.get("x", 0)), y=int(raw.get("y", 0)), direction=int(raw.get("d", 8)), tail_x=int(raw.get("tx", 0)), tail_y=int(raw.get("ty", 0))))
        light_count = sum(len(data.get(key, [])) for key in ("basicBeatmapEvents", "colorBoostBeatmapEvents", "lightColorEventBoxGroups", "lightRotationEventBoxGroups", "lightTranslationEventBoxGroups"))
    else:
        note_data = data.get("colorNotesData", [])
        for raw in data.get("colorNotes", []):
            if not isinstance(raw, dict) or not isinstance(raw.get("i"), int) or not 0 <= raw["i"] < len(note_data):
                continue
            meta = note_data[raw["i"]]
            events.append(_event("note", raw, beat=float(raw.get("b", 0.0)), color=int(meta.get("c", 0)), x=int(meta.get("x", 0)), y=int(meta.get("y", 0)), direction=int(meta.get("d", 8))))
        bomb_data = data.get("bombNotesData", [])
        for raw in data.get("bombNotes", []):
            if isinstance(raw, dict) and isinstance(raw.get("i"), int) and 0 <= raw["i"] < len(bomb_data):
                meta = bomb_data[raw["i"]]
                events.append(_event("bomb", raw, beat=float(raw.get("b", 0.0)), color=None, x=int(meta.get("x", 0)), y=int(meta.get("y", 0)), direction=None))
        obstacle_data = data.get("obstaclesData", [])
        for raw in data.get("obstacles", []):
            if isinstance(raw, dict) and isinstance(raw.get("i"), int) and 0 <= raw["i"] < len(obstacle_data):
                meta = obstacle_data[raw["i"]]
                events.append(_event("wall", raw, beat=float(raw.get("b", 0.0)), tail_beat=float(raw.get("b", 0.0)) + float(meta.get("d", 0.0)), x=int(meta.get("x", 0)), y=int(meta.get("y", 0))))
        arc_data = data.get("arcsData", [])
        for raw in data.get("arcs", []):
            if not isinstance(raw, dict):
                continue
            hi, ti, ai = raw.get("hi"), raw.get("ti"), raw.get("ai")
            if not all(isinstance(index, int) for index in (hi, ti, ai)) or not (0 <= hi < len(note_data) and 0 <= ti < len(note_data) and 0 <= ai < len(arc_data)):
                continue
            head, tail, meta = note_data[hi], note_data[ti], arc_data[ai]
            events.append(_event("arc", raw, beat=float(raw.get("hb", 0.0)), tail_beat=float(raw.get("tb", 0.0)), color=int(head.get("c", 0)), x=int(head.get("x", 0)), y=int(head.get("y", 0)), direction=int(head.get("d", 8)), tail_x=int(tail.get("x", 0)), tail_y=int(tail.get("y", 0)), tail_direction=int(tail.get("d", 8)), arc_meta=meta))
        chain_data = data.get("chainsData", [])
        for raw in data.get("chains", []):
            if not isinstance(raw, dict):
                continue
            hi, ci = raw.get("i"), raw.get("ci")
            if not isinstance(hi, int) or not isinstance(ci, int) or not (0 <= hi < len(note_data) and 0 <= ci < len(chain_data)):
                continue
            head, meta = note_data[hi], chain_data[ci]
            events.append(_event("chain", raw, beat=float(raw.get("hb", 0.0)), tail_beat=float(raw.get("tb", 0.0)), color=int(head.get("c", 0)), x=int(head.get("x", 0)), y=int(head.get("y", 0)), direction=int(head.get("d", 8)), tail_x=int(meta.get("tx", head.get("x", 0))), tail_y=int(meta.get("ty", head.get("y", 0))), chain_meta=meta))
        light_count = 0
    events.sort(key=lambda item: (item.get("beat", 0.0), item["kind"], item.get("color") or 0))
    return events, light_count


def map_features(events: list[dict[str, Any]], bpm: float | None) -> dict[str, Any]:
    notes = [event for event in events if event["kind"] == "note"]
    by_hand = {0: [], 1: []}
    for note in notes:
        if note.get("color") in by_hand:
            by_hand[note["color"]].append(note)
    intervals: list[float] = []
    reaches: list[float] = []
    parity_violations = 0
    rapid_same_hand = 0
    for hand_notes in by_hand.values():
        for left, right in zip(hand_notes, hand_notes[1:]):
            gap = right["beat"] - left["beat"]
            intervals.append(gap)
            if gap <= 0.25:
                rapid_same_hand += 1
            reaches.append(math.hypot(right.get("x", 0) - left.get("x", 0), right.get("y", 0) - left.get("y", 0)))
            lv = direction_vector(int(left.get("direction", 8)))[1]
            rv = direction_vector(int(right.get("direction", 8)))[1]
            if gap <= 1.0 and lv != 0 and rv != 0 and lv == rv:
                parity_violations += 1
    max_beat = max((float(event.get("tail_beat", event["beat"])) for event in events), default=0.0)
    duration_seconds = max_beat * 60.0 / bpm if bpm and bpm > 0 else 0.0
    center_notes = sum(1 for note in notes if note.get("x") in (1, 2) and note.get("y") == 1)
    position_transitions: Counter[str] = Counter()
    direction_transitions: Counter[str] = Counter()
    recovery_seconds: list[float] = []
    for hand_notes in by_hand.values():
        for left, right in zip(hand_notes, hand_notes[1:]):
            position_transitions[f"{left.get('x')},{left.get('y')}>{right.get('x')},{right.get('y')}"] += 1
            direction_transitions[f"{left.get('direction', 8)}>{right.get('direction', 8)}"] += 1
            if bpm and bpm > 0:
                recovery_seconds.append((float(right["beat"]) - float(left["beat"])) * 60.0 / bpm)
    arc_durations = [float(event.get("tail_beat", event["beat"])) - float(event["beat"]) for event in events if event["kind"] == "arc"]
    chain_durations = [float(event.get("tail_beat", event["beat"])) - float(event["beat"]) for event in events if event["kind"] == "chain"]
    density_envelope = []
    for start in np.arange(0.0, max_beat + 16.0, 16.0):
        count = sum(1 for note in notes if start <= float(note["beat"]) < start + 16.0)
        density_envelope.append({"startBeat": float(start), "notesPerBeat": count / 16.0})
    walls = [event for event in events if event["kind"] == "wall"]
    bombs = [event for event in events if event["kind"] == "bomb"]
    wall_occupancy = sum(max(0.0, float(event.get("tail_beat", event["beat"])) - float(event["beat"])) for event in walls)
    bomb_nearest_note = [
        min((abs(float(note["beat"]) - float(bomb["beat"])) for note in notes), default=math.inf)
        for bomb in bombs
    ]
    return {
        "maxBeat": max_beat,
        "durationSeconds": duration_seconds,
        "nps": len(notes) / duration_seconds if duration_seconds > 0 else 0.0,
        "medianSameHandGap": quantile(intervals, 0.5),
        "p10SameHandGap": quantile(intervals, 0.1),
        "p95Reach": quantile(reaches, 0.95),
        "parityViolationRate": parity_violations / max(1, len(intervals)),
        "rapidSameHandRate": rapid_same_hand / max(1, len(intervals)),
        "centerVisionRate": center_notes / max(1, len(notes)),
        "counts": dict(Counter(event["kind"] for event in events)),
        "densityEnvelope16Beats": density_envelope,
        "positionTransitions": dict(position_transitions),
        "directionTransitions": dict(direction_transitions),
        "medianRecoverySeconds": quantile(recovery_seconds, 0.5),
        "p10RecoverySeconds": quantile(recovery_seconds, 0.1),
        "arcLifecycle": {
            "medianDurationBeats": quantile(arc_durations, 0.5),
            "p95DurationBeats": quantile(arc_durations, 0.95),
        },
        "chainLifecycle": {
            "medianDurationBeats": quantile(chain_durations, 0.5),
            "p95DurationBeats": quantile(chain_durations, 0.95),
        },
        "obstacleGrammar": {
            "wallBeatOccupancyRate": wall_occupancy / max(1.0, max_beat),
            "medianBombNoteSeparationBeats": quantile([value for value in bomb_nearest_note if math.isfinite(value)], 0.5),
        },
    }


def rebuild_transformations(database: sqlite3.Connection) -> None:
    database.execute("DELETE FROM difficulty_transformations")
    order = {name: index for index, name in enumerate(("Easy", "Normal", "Hard", "Expert", "ExpertPlus"))}
    rows = database.execute(
        "SELECT level_id,characteristic,difficulty,notes,bombs,walls,arcs,chains,features_json FROM difficulties ORDER BY level_id,characteristic"
    ).fetchall()
    grouped: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["level_id"]), str(row["characteristic"]))].append(row)
    for (level_id, characteristic), items in grouped.items():
        items.sort(key=lambda row: order.get(str(row["difficulty"]), 99))
        for source, target in zip(items, items[1:]):
            source_features = json.loads(source["features_json"])
            target_features = json.loads(target["features_json"])
            transform = {
                "noteCountRatio": float(target["notes"]) / max(1, int(source["notes"])),
                "npsRatio": float(target_features.get("nps", 0.0)) / max(1e-6, float(source_features.get("nps", 0.0))),
                "objectCountRatios": {
                    kind: float(target[kind]) / max(1, int(source[kind]))
                    for kind in ("bombs", "walls", "arcs", "chains")
                },
            }
            database.execute(
                "INSERT INTO difficulty_transformations VALUES(?,?,?,?,?)",
                (level_id, characteristic, source["difficulty"], target["difficulty"], json.dumps(transform)),
            )


def resolve_json_name(assets: dict[str, dict[str, Any]], requested: Any) -> dict[str, Any] | None:
    if not isinstance(requested, str):
        return None
    folded = requested.casefold()
    for name, data in assets.items():
        stem = Path(name).name.casefold()
        if stem == folded or Path(stem).stem == Path(folded).stem:
            return data
    return None


def insert_bundle(
    database: sqlite3.Connection,
    path: Path,
    source: str,
    pack_id: str,
    catalog_entry: dict[str, Any] | None = None,
) -> tuple[str, int]:
    bundle_id = path.name
    file_digest = sha256_file(path)
    dependency_digest = str((catalog_entry or {}).get("_packBundleHash", ""))
    digest = hashlib.sha256(f"{file_digest}:{dependency_digest}:{TOOL_VERSION}".encode("ascii")).hexdigest()
    stat = path.stat()
    existing = database.execute("SELECT sha256,status FROM bundles WHERE id=?", (bundle_id,)).fetchone()
    if existing and existing["sha256"] == digest and existing["status"] == "indexed":
        return "unchanged", 0
    database.execute("DELETE FROM bundles WHERE id=?", (bundle_id,))
    database.execute("DELETE FROM exclusions WHERE bundle_id=?", (bundle_id,))
    if bundle_id.casefold() in INTERNAL_LEVEL_IDS:
        database.execute(
            "INSERT INTO bundles VALUES(?,?,?,?,?,?,?,?,?,?)",
            (bundle_id, str(path), source, pack_id, digest, stat.st_size, stat.st_mtime_ns, "excluded", None, int(time.time())),
        )
        database.execute("INSERT INTO exclusions VALUES(?,?)", (bundle_id, "internal non-playable test fixture"))
        return "excluded", 0
    try:
        text_assets, behaviours, _audio, path_names = extract_bundle_assets(path)
        assets = json_assets(text_assets)
        infos = [(name, data) for name, data in assets.items() if looks_like_info(data)]
        beatmaps = {name: data for name, data in assets.items() if looks_like_beatmap(data)}
        official_level_data = next(
            (
                item
                for item in behaviours
                if isinstance(item.get("_difficultyBeatmapSets"), list)
                and any(
                    isinstance(group, dict) and "_beatmapCharacteristicSerializedName" in group
                    for group in item.get("_difficultyBeatmapSets", [])
                )
            ),
            None,
        )
        if not infos and official_level_data is None:
            raise ValueError(
                f"no level metadata found in {len(text_assets)} TextAssets {sorted(assets)[:8]} and {len(behaviours)} MonoBehaviours"
            )
        if not beatmaps:
            raise ValueError(
                f"level metadata found ({[name for name, _data in infos] or 'MonoBehaviour'}), "
                f"but no gameplay beatmaps were found among {sorted(assets)[:8]}"
            )
        database.execute(
            "INSERT INTO bundles VALUES(?,?,?,?,?,?,?,?,?,?)",
            (bundle_id, str(path), source, pack_id, digest, stat.st_size, stat.st_mtime_ns, "indexed", None, int(time.time())),
        )
        imported = 0
        unresolved: list[str] = []
        level_sources: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for _name, info in infos:
            level_sources.append((info_metadata(info), difficulty_refs(info)))
        if official_level_data is not None:
            map_format = max((major_version(item) for item in beatmaps.values()), default=0)
            level_sources.append(
                (
                    catalog_metadata(bundle_id, catalog_entry, map_format),
                    official_difficulty_refs(official_level_data, path_names, catalog_entry),
                )
            )
        for metadata, refs in level_sources:
            era_weight = float((catalog_entry or {}).get("_eraWeight", 1.0 if metadata["format"] >= 4 else 0.85 if metadata["format"] == 3 else 0.65))
            level_insert = database.execute(
                "INSERT INTO levels(bundle_id,title,artist,mapper,bpm,format,era_weight,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                (bundle_id, metadata["title"], metadata["artist"], metadata["mapper"], metadata["bpm"], metadata["format"], era_weight, json.dumps(metadata, ensure_ascii=False)),
            )
            level_id = int(level_insert.lastrowid)
            if not refs and len(beatmaps) == 1:
                refs = [{"characteristic": "Standard", "difficulty": "Unknown", "filename": next(iter(beatmaps)), "lightshow": None, "njs": None, "offset": None}]
            for ref in refs:
                map_data = resolve_json_name(beatmaps, ref["filename"])
                if map_data is None:
                    unresolved.append(f"{ref['characteristic']}/{ref['difficulty']}:{ref['filename']}")
                    continue
                events, light_count = normalize_beatmap(map_data)
                lightshow_data = resolve_json_name(assets, ref.get("lightshow"))
                if lightshow_data is not None:
                    light_count = lightshow_event_count(lightshow_data)
                feature = map_features(events, float(metadata["bpm"]) if metadata["bpm"] else None)
                counts = Counter(event["kind"] for event in events)
                difficulty_insert = database.execute(
                    "INSERT INTO difficulties(level_id,characteristic,difficulty,njs,spawn_offset,notes,bombs,walls,arcs,chains,light_events,features_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (level_id, ref["characteristic"], ref["difficulty"], ref["njs"], ref["offset"], counts["note"], counts["bomb"], counts["wall"], counts["arc"], counts["chain"], light_count, json.dumps(feature)),
                )
                difficulty_id = int(difficulty_insert.lastrowid)
                database.executemany(
                    "INSERT INTO events(difficulty_id,kind,beat,tail_beat,color,x,y,direction,tail_x,tail_y,tail_direction,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            difficulty_id,
                            event["kind"],
                            event["beat"],
                            event.get("tail_beat"),
                            event.get("color"),
                            event.get("x"),
                            event.get("y"),
                            event.get("direction"),
                            event.get("tail_x"),
                            event.get("tail_y"),
                            event.get("tail_direction"),
                            json.dumps(event.get("payload", {}), ensure_ascii=False),
                        )
                        for event in events
                    ],
                )
                imported += 1
        if not imported:
            raise ValueError(
                f"metadata found, but no referenced difficulty JSON could be resolved; "
                f"unresolved refs: {unresolved[:12]}; available beatmaps: {sorted(beatmaps)[:12]}"
            )
        return "indexed", imported
    except Exception as exc:
        database.execute("DELETE FROM bundles WHERE id=?", (bundle_id,))
        database.execute(
            "INSERT INTO bundles VALUES(?,?,?,?,?,?,?,?,?,?)",
            (bundle_id, str(path), source, pack_id, digest, stat.st_size, stat.st_mtime_ns, "failed", str(exc), int(time.time())),
        )
        return "failed", 0


def rebuild_profiles(database: sqlite3.Connection) -> None:
    database.execute("DELETE FROM style_profiles")
    rows = database.execute(
        """
        SELECT b.pack_id,l.format,l.era_weight,d.difficulty,d.characteristic,d.features_json
        FROM difficulties d JOIN levels l ON l.id=d.level_id JOIN bundles b ON b.id=l.bundle_id
        WHERE b.status='indexed'
        """
    ).fetchall()
    groups: dict[tuple[str, str, str, str], list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for row in rows:
        features = json.loads(row["features_json"])
        era = "modern-v4" if int(row["format"] or 0) >= 4 else "v3" if int(row["format"] or 0) == 3 else "legacy-v2"
        for scope, scope_id in (("pack", row["pack_id"] or "unknown"), ("era", era), ("global", "official")):
            groups[(scope, scope_id, row["difficulty"], row["characteristic"])].append((float(row["era_weight"]), features))
    feature_names = ("nps", "medianSameHandGap", "p10SameHandGap", "p95Reach", "parityViolationRate", "rapidSameHandRate", "centerVisionRate")
    for (scope, scope_id, difficulty, characteristic), samples in groups.items():
        profile: dict[str, Any] = {}
        total_weight = sum(weight for weight, _ in samples) or 1.0
        for name in feature_names:
            profile[name] = sum(weight * float(item.get(name, 0.0)) for weight, item in samples) / total_weight
        profile["objectRates"] = {}
        for kind in ("bomb", "wall", "arc", "chain"):
            profile["objectRates"][kind] = sum(weight * float(item.get("counts", {}).get(kind, 0)) for weight, item in samples) / total_weight
        database.execute(
            "INSERT INTO style_profiles VALUES(?,?,?,?,?,?)",
            (scope, scope_id, difficulty, characteristic, len(samples), json.dumps(profile)),
        )
    global_features = [json.loads(row["features_json"]) for row in rows if row["characteristic"] == "Standard"]
    ranker = {
        "kind": "official-distance-plus-hard-penalties",
        "features": {
            name: {
                "mean": sum(float(item.get(name, 0.0)) for item in global_features) / max(1, len(global_features)),
                "scale": max(1e-6, quantile([abs(float(item.get(name, 0.0))) for item in global_features], 0.75, 1.0)),
            }
            for name in feature_names
        },
        "syntheticNegativePenalties": {
            "occupancyConflict": 100.0,
            "wrongColorEndpoint": 100.0,
            "unreachableTransition": 30.0,
            "parityViolation": 8.0,
            "visionBlock": 12.0,
        },
    }
    set_meta(database, "quality_ranker", ranker)


def sync(args: argparse.Namespace) -> int:
    detected = args.game_root or detect_game_root()
    if detected is None:
        print("ERROR: Beat Saber install was not found; pass --game-root PATH", file=sys.stderr)
        return 2
    game_root = detected.resolve()
    candidates, pack_paths = discover(game_root)
    if not candidates:
        print(f"ERROR: no first-party level bundles found under {game_root}", file=sys.stderr)
        return 2
    database = connect(args.database)
    catalog, _pack_hashes = index_pack_definitions(database, pack_paths)
    version_path = game_root / "BeatSaberVersion.txt"
    game_version = version_path.read_text(encoding="utf-8-sig").strip() if version_path.exists() else "unknown"
    catalog_path = game_root / "Beat Saber_Data" / "StreamingAssets" / "aa" / "catalog.json"
    catalog_hash = sha256_file(catalog_path) if catalog_path.exists() else None
    summary = Counter()
    difficulty_count = 0
    selected = candidates[: args.max_bundles] if args.max_bundles else candidates
    for path, source in selected:
        entry = catalog.get(path.name.casefold())
        pack_id = str(entry.get("_packID", source)) if entry else source
        status, imported = insert_bundle(database, path, source, pack_id, entry)
        summary[status] += 1
        difficulty_count += imported
        database.commit()
    rebuild_profiles(database)
    rebuild_transformations(database)
    set_meta(database, "tool_version", TOOL_VERSION)
    set_meta(database, "game_version", game_version)
    set_meta(database, "catalog_sha256", catalog_hash)
    set_meta(database, "candidate_bundle_count", len(candidates))
    set_meta(database, "pack_definition_count", len(pack_paths))
    set_meta(database, "library_fingerprint", library_fingerprint(game_root))
    set_meta(database, "last_sync_unix", int(time.time()))
    database.commit()
    payload = {
        "database": str(args.database.resolve()),
        "gameVersion": game_version,
        "candidateBundles": len(candidates),
        "packDefinitions": len(pack_paths),
        "selectedBundles": len(selected),
        "difficultiesImported": difficulty_count,
        "results": dict(summary),
    }
    print(json.dumps(payload, indent=2))
    return 1 if summary["failed"] else 0


def report(args: argparse.Namespace) -> int:
    if not args.database.exists():
        print(f"ERROR: corpus database does not exist: {args.database}", file=sys.stderr)
        return 2
    database = connect(args.database)
    bundle_status = {row["status"]: row["count"] for row in database.execute("SELECT status,COUNT(*) AS count FROM bundles GROUP BY status")}
    pack_status = {row["status"]: row["count"] for row in database.execute("SELECT status,COUNT(*) AS count FROM pack_definitions GROUP BY status")}
    by_pack = [dict(row) for row in database.execute("SELECT pack_id,COUNT(DISTINCT b.id) bundles,COUNT(DISTINCT l.id) levels,COUNT(d.id) difficulties FROM bundles b LEFT JOIN levels l ON l.bundle_id=b.id LEFT JOIN difficulties d ON d.level_id=l.id GROUP BY pack_id ORDER BY pack_id")]
    by_format = [dict(row) for row in database.execute("SELECT l.format,COUNT(DISTINCT l.id) AS levels,COUNT(d.id) AS difficulties FROM levels l LEFT JOIN difficulties d ON d.level_id=l.id GROUP BY l.format ORDER BY l.format")]
    by_source = [dict(row) for row in database.execute("SELECT source,status,COUNT(*) AS bundles FROM bundles GROUP BY source,status ORDER BY source,status")]
    by_difficulty = [dict(row) for row in database.execute("SELECT characteristic,difficulty,COUNT(*) AS count,SUM(notes) AS notes,SUM(arcs) AS arcs,SUM(chains) AS chains,SUM(bombs) AS bombs,SUM(walls) AS walls FROM difficulties GROUP BY characteristic,difficulty ORDER BY characteristic,difficulty")]
    by_characteristic = [dict(row) for row in database.execute("SELECT characteristic,COUNT(*) AS count FROM difficulties GROUP BY characteristic ORDER BY characteristic")]
    songs = [dict(row) for row in database.execute(
        """
        SELECT l.title AS song,l.artist,b.pack_id AS pack,l.format,
               COUNT(d.id) AS difficulties,
               SUM(CASE WHEN COALESCE(d.notes,0)+COALESCE(d.bombs,0)+COALESCE(d.walls,0)+COALESCE(d.arcs,0)+COALESCE(d.chains,0)>0 THEN 1 ELSE 0 END) AS gameplay,
               SUM(CASE WHEN COALESCE(d.light_events,0)>0 THEN 1 ELSE 0 END) AS lightshow
        FROM levels l
        JOIN bundles b ON b.id=l.bundle_id
        LEFT JOIN difficulties d ON d.level_id=l.id
        GROUP BY l.id
        ORDER BY l.title,l.artist
        """
    )]
    extraction_row = database.execute(
        """
        SELECT COUNT(*) AS difficulties,
               SUM(CASE WHEN COALESCE(notes,0)+COALESCE(bombs,0)+COALESCE(walls,0)+COALESCE(arcs,0)+COALESCE(chains,0)>0 THEN 1 ELSE 0 END) AS gameplay,
               SUM(CASE WHEN COALESCE(light_events,0)>0 THEN 1 ELSE 0 END) AS lightshow,
               SUM(CASE WHEN COALESCE(notes,0)+COALESCE(bombs,0)+COALESCE(walls,0)+COALESCE(arcs,0)+COALESCE(chains,0)=0 THEN 1 ELSE 0 END) AS missingGameplay,
               SUM(CASE WHEN COALESCE(light_events,0)=0 THEN 1 ELSE 0 END) AS missingLightshow
        FROM difficulties
        """
    ).fetchone()
    extraction = dict(extraction_row) if extraction_row is not None else {
        "difficulties": 0,
        "gameplay": 0,
        "lightshow": 0,
        "missingGameplay": 0,
        "missingLightshow": 0,
    }
    failures = [dict(row) for row in database.execute("SELECT id,path,error FROM bundles WHERE status='failed' ORDER BY id")]
    exclusions = [dict(row) for row in database.execute("SELECT bundle_id,reason FROM exclusions ORDER BY bundle_id")]
    payload = {
        "database": str(args.database.resolve()),
        "gameVersion": get_meta(database, "game_version"),
        "catalogSha256": get_meta(database, "catalog_sha256"),
        "candidateBundles": get_meta(database, "candidate_bundle_count"),
        "packDefinitions": get_meta(database, "pack_definition_count"),
        "bundleStatus": bundle_status,
        "packDefinitionStatus": pack_status,
        "packs": by_pack,
        "songs": songs,
        "formats": by_format,
        "sources": by_source,
        "characteristics": by_characteristic,
        "difficulties": by_difficulty,
        "extraction": extraction,
        "failures": failures,
        "exclusions": exclusions,
    }
    print(json.dumps(payload, indent=2))
    return 1 if failures else 0


def corpus_is_fresh(database_path: Path, game_root: Path) -> bool:
    if not database_path.exists():
        return False
    database = connect(database_path)
    version_path = game_root / "BeatSaberVersion.txt"
    current_version = version_path.read_text(encoding="utf-8-sig").strip() if version_path.exists() else "unknown"
    catalog_path = game_root / "Beat Saber_Data" / "StreamingAssets" / "aa" / "catalog.json"
    current_hash = sha256_file(catalog_path) if catalog_path.exists() else None
    return (
        get_meta(database, "game_version") == current_version
        and get_meta(database, "catalog_sha256") == current_hash
        and get_meta(database, "library_fingerprint") == library_fingerprint(game_root)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(database=default_cache_dir() / "official-corpus.sqlite3")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync", help="incrementally import installed first-party maps")
    sync_parser.add_argument("--game-root", type=Path, default=None, help="Beat Saber install root; auto-detected on common Oculus/Steam paths")
    sync_parser.add_argument("--database", type=Path, default=default_cache_dir() / "official-corpus.sqlite3")
    sync_parser.add_argument("--max-bundles", type=int, default=0, help="test-only import limit; zero means all")
    sync_parser.set_defaults(function=sync)
    report_parser = subparsers.add_parser("report", help="print corpus coverage and failures")
    report_parser.add_argument("--database", type=Path, default=default_cache_dir() / "official-corpus.sqlite3")
    report_parser.set_defaults(function=report)
    args = parser.parse_args()
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
