#!/usr/bin/env python3
"""Validate Beat Saber structure, timing provenance, and hand-state invariants."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from beatforge_core import ValidationReport, circular_direction_distance, load_json_bytes, write_json
from kinematics import bomb_on_swing_path, cross_hand_finding, transition_finding
from artwork import delta_e_2000, rgb_to_lab
from safety_contract import RECOVERY_BEATS, same_hand_after_hold_too_soon


def ai_release_route_enabled() -> bool:
    """``release_candidate`` is reserved for the AI-routed review path."""
    return os.environ.get("BEATFORGE_AI_RELEASE_ROUTE", "").strip().casefold() in {"1", "true", "yes"}


class Package:
    def names(self) -> list[str]:
        raise NotImplementedError

    def read(self, name: str) -> bytes:
        raise NotImplementedError


class FolderPackage(Package):
    def __init__(self, root: Path) -> None:
        self.root = root

    def names(self) -> list[str]:
        return [path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file()]

    def read(self, name: str) -> bytes:
        return (self.root / PurePosixPath(name)).read_bytes()


class ZipPackage(Package):
    def __init__(self, path: Path) -> None:
        self.archive = zipfile.ZipFile(path)

    def names(self) -> list[str]:
        return [name for name in self.archive.namelist() if not name.endswith("/")]

    def read(self, name: str) -> bytes:
        return self.archive.read(name)


def major_version(data: dict[str, Any]) -> int:
    try:
        return int(str(data.get("version", data.get("_version", "0"))).split(".", 1)[0])
    except ValueError:
        return 0


def info_refs(info: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    files: list[str] = []
    maps: list[dict[str, str]] = []
    if major_version(info) >= 4:
        audio = info.get("audio", {})
        for key in ("songFilename", "audioDataFilename"):
            if isinstance(audio, dict) and isinstance(audio.get(key), str):
                files.append(audio[key])
        for key in ("coverImageFilename", "songPreviewFilename"):
            if isinstance(info.get(key), str):
                files.append(info[key])
        for item in info.get("difficultyBeatmaps", []):
            if not isinstance(item, dict):
                continue
            filename = item.get("beatmapDataFilename")
            if isinstance(filename, str):
                files.append(filename)
                maps.append({"filename": filename, "difficulty": str(item.get("difficulty", "Unknown")), "characteristic": str(item.get("characteristic", "Unknown"))})
            lightshow = item.get("lightshowDataFilename")
            if isinstance(lightshow, str):
                files.append(lightshow)
    else:
        for key in ("_songFilename", "_coverImageFilename"):
            if isinstance(info.get(key), str):
                files.append(info[key])
        for group in info.get("_difficultyBeatmapSets", []):
            if not isinstance(group, dict):
                continue
            characteristic = str(group.get("_beatmapCharacteristicName", "Unknown"))
            for item in group.get("_difficultyBeatmaps", []):
                if not isinstance(item, dict):
                    continue
                filename = item.get("_beatmapFilename")
                if isinstance(filename, str):
                    files.append(filename)
                    maps.append({"filename": filename, "difficulty": str(item.get("_difficulty", "Unknown")), "characteristic": characteristic})
    return files, maps


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_info_colors(info: dict[str, Any], report: ValidationReport) -> None:
    if major_version(info) >= 4:
        return
    schemes = info.get("_colorSchemes", [])
    if not schemes:
        report.add("warning", "COLOR_SCHEME_MISSING", "Info.dat has no explicit map color scheme", file="Info.dat")
        return
    if not isinstance(schemes, list):
        report.add("error", "COLOR_SCHEME_ARRAY", "_colorSchemes must be an array", file="Info.dat")
        return
    required = (
        "saberAColor",
        "saberBColor",
        "environmentColor0",
        "environmentColor1",
        "obstaclesColor",
        "environmentColor0Boost",
        "environmentColor1Boost",
    )
    for index, wrapper in enumerate(schemes):
        if not isinstance(wrapper, dict) or wrapper.get("useOverride") is not True:
            report.add("error", "COLOR_SCHEME_OVERRIDE", f"color scheme {index} must enable useOverride", file="Info.dat")
            continue
        scheme = wrapper.get("colorScheme")
        if not isinstance(scheme, dict):
            report.add("error", "COLOR_SCHEME_OBJECT", f"color scheme {index} is missing colorScheme", file="Info.dat")
            continue
        for field in required:
            color = scheme.get(field)
            if not isinstance(color, dict):
                report.add("error", "COLOR_FIELD_MISSING", f"color scheme {index} is missing {field}", file="Info.dat")
                continue
            for channel in ("r", "g", "b", "a"):
                value = color.get(channel)
                if not finite(value) or not 0.0 <= float(value) <= 1.0:
                    report.add("error", "COLOR_CHANNEL_RANGE", f"{field}.{channel} must be between 0 and 1", file="Info.dat")
        left = scheme.get("saberAColor", {})
        right = scheme.get("saberBColor", {})
        if all(finite(left.get(channel)) and finite(right.get(channel)) for channel in ("r", "g", "b")):
            delta = delta_e_2000(
                rgb_to_lab([left["r"], left["g"], left["b"]]),
                rgb_to_lab([right["r"], right["g"], right["b"]]),
            )
            if delta < 30.0:
                report.add("error", "SABER_COLOR_CONTRAST", f"saber colors have DeltaE2000 {delta:.2f}, below 30", file="Info.dat", deltaE2000=round(delta, 6))


def exact_note(notes: list[dict[str, Any]], beat: float, color: int, x: int, y: int) -> list[dict[str, Any]]:
    return [note for note in notes if abs(float(note.get("b", math.inf)) - beat) < 1e-6 and note.get("c") == color and note.get("x") == x and note.get("y") == y]


def validate_v3(
    data: dict[str, Any],
    filename: str,
    report: ValidationReport,
    *,
    bpm: float,
    difficulty: str,
) -> dict[str, Any]:
    arrays: dict[str, list[dict[str, Any]]] = {}
    for key in ("colorNotes", "bombNotes", "obstacles", "sliders", "burstSliders"):
        value = data.get(key, [])
        if not isinstance(value, list):
            report.add("error", "ARRAY_REQUIRED", f"{key} must be an array", file=filename)
            value = []
        arrays[key] = [item for item in value if isinstance(item, dict)]
        if len(arrays[key]) != len(value):
            report.add("error", "OBJECT_REQUIRED", f"{key} contains non-object entries", file=filename)
    notes = sorted(arrays["colorNotes"], key=lambda item: (float(item.get("b", 0)), int(item.get("c", 0))))
    for index, note in enumerate(notes):
        beat = note.get("b")
        if not finite(beat) or float(beat) < 0:
            report.add("error", "INVALID_NOTE_BEAT", f"note {index} has an invalid beat", file=filename)
        if note.get("c") not in (0, 1):
            report.add("error", "INVALID_NOTE_COLOR", f"note {index} color must be 0 or 1", file=filename, beat=float(beat) if finite(beat) else None)
        if note.get("x") not in range(4) or note.get("y") not in range(3):
            report.add("error", "INVALID_NOTE_POSITION", f"note {index} is outside the 4x3 grid", file=filename, beat=float(beat) if finite(beat) else None)
        if note.get("d") not in range(9):
            report.add("error", "INVALID_CUT_DIRECTION", f"note {index} direction must be 0 through 8", file=filename, beat=float(beat) if finite(beat) else None)
    duplicate_keys = Counter((float(note.get("b", 0)), note.get("c"), note.get("x"), note.get("y")) for note in notes)
    for key, count in duplicate_keys.items():
        if count > 1:
            report.add("error", "DUPLICATE_NOTE", f"{count} identical notes share beat/color/position {key}", file=filename, beat=key[0])
    same_color_times = Counter((round(float(note.get("b", 0)), 6), note.get("c")) for note in notes)
    notes_by_beat_color: dict[tuple[float, Any], list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        notes_by_beat_color[(round(float(note.get("b", 0)), 6), note.get("c"))].append(note)
    for (beat, color), count in same_color_times.items():
        if count > 1:
            pair = notes_by_beat_color[(beat, color)]
            left, right = pair[0], pair[1]
            report.add_motion(
                "SAME_COLOR_SIMULTANEOUS",
                f"color {color} has {count} notes at one beat",
                file=filename,
                beat=beat,
                difficulty=difficulty,
                previousBeat=float(left["b"]),
                currentBeat=float(right["b"]),
                color=color,
                previousPosition={"x": int(left["x"]), "y": int(left["y"])},
                currentPosition={"x": int(right["x"]), "y": int(right["y"])},
                previousDirection=int(left.get("d", 8)),
                currentDirection=int(right.get("d", 8)),
                availableRecoveryBeats=0.0,
                requiredRecoveryBeats=RECOVERY_BEATS.get(difficulty, 0.3),
                failedConstraint="simultaneous_same_color",
            )

    hand_intervals: dict[int, list[tuple[float, float, str]]] = defaultdict(list)
    missing_arc_tails = 0
    rapid_same_after = 0
    next_same_after = 0
    missing_opposite_setup = 0
    for kind, items in (("arc", arrays["sliders"]), ("chain", arrays["burstSliders"])):
        for index, item in enumerate(items):
            beat = item.get("b")
            tail = item.get("tb")
            color = item.get("c")
            if not finite(beat) or not finite(tail) or float(tail) <= float(beat):
                report.add("error", "INVALID_HOLD_RANGE", f"{kind} {index} must end after its head", file=filename, beat=float(beat) if finite(beat) else None)
                continue
            if color not in (0, 1):
                report.add("error", "INVALID_HOLD_COLOR", f"{kind} {index} color must be 0 or 1", file=filename, beat=float(beat))
                continue
            heads = exact_note(notes, float(beat), int(color), int(item.get("x", -1)), int(item.get("y", -1)))
            if not heads:
                report.add("error", "HOLD_HEAD_MISSING", f"{kind} {index} has no matching same-color head note", file=filename, beat=float(beat))
            elif all(head.get("d") != item.get("d") for head in heads):
                report.add("error", "HOLD_HEAD_DIRECTION", f"{kind} {index} head direction disagrees with its note", file=filename, beat=float(beat))
            if kind == "arc":
                tails = exact_note(notes, float(tail), int(color), int(item.get("tx", -1)), int(item.get("ty", -1)))
                if not tails:
                    missing_arc_tails += 1
                    report.add("warning", "ARC_TAIL_MISSING", f"arc {index} has no matching tail note", file=filename, beat=float(tail))
                elif all(tail_note.get("d") != item.get("tc") for tail_note in tails):
                    report.add("error", "ARC_TAIL_DIRECTION", f"arc {index} tail direction disagrees with its note", file=filename, beat=float(tail))
            hand_intervals[int(color)].append((float(beat), float(tail), kind))
            conflicts = [note for note in notes if note.get("c") == color and float(beat) < float(note.get("b", 0)) < float(tail) - 1e-6]
            if conflicts:
                conflict = conflicts[0]
                report.add_motion(
                    "HAND_OCCUPANCY_CONFLICT",
                    f"{kind} {index} overlaps {len(conflicts)} same-hand notes",
                    file=filename,
                    beat=float(beat),
                    difficulty=difficulty,
                    previousBeat=float(beat),
                    currentBeat=float(conflict.get("b", beat)),
                    color=int(color),
                    previousPosition={"x": int(item.get("x", -1)), "y": int(item.get("y", -1))},
                    currentPosition={"x": int(conflict.get("x", -1)), "y": int(conflict.get("y", -1))},
                    previousDirection=int(item.get("d", 8)),
                    currentDirection=int(conflict.get("d", 8)),
                    availableRecoveryBeats=float(conflict.get("b", beat)) - float(beat),
                    requiredRecoveryBeats=RECOVERY_BEATS.get(difficulty, 0.3),
                    failedConstraint="hold_occupancy",
                )
            after = [note for note in notes if float(note.get("b", 0)) > float(tail) + 1e-6]
            if after:
                first = after[0]
                if first.get("c") == color:
                    next_same_after += 1
                    gap = float(first["b"]) - float(tail)
                    if same_hand_after_hold_too_soon(gap):
                        rapid_same_after += 1
                        report.add_motion(
                            "RAPID_SAME_HAND_AFTER_HOLD",
                            f"{kind} {index} is followed by the same color after only {gap:g} beat",
                            file=filename,
                            beat=float(first["b"]),
                            difficulty=difficulty,
                            previousBeat=float(tail),
                            currentBeat=float(first["b"]),
                            color=color,
                            previousPosition={
                                "x": int(item.get("tx", item.get("x", -1))),
                                "y": int(item.get("ty", item.get("y", -1))),
                            },
                            currentPosition={"x": int(first.get("x", -1)), "y": int(first.get("y", -1))},
                            previousDirection=int(item.get("tc", item.get("d", 8))),
                            currentDirection=int(first.get("d", 8)),
                            availableRecoveryBeats=gap,
                            requiredRecoveryBeats=RECOVERY_BEATS.get(difficulty, 0.3),
                            failedConstraint="post_hold_recovery",
                        )

    for color, intervals in hand_intervals.items():
        ordered = sorted(intervals)
        for left, right in zip(ordered, ordered[1:]):
            if right[0] < left[1] - 1e-6:
                report.add_motion(
                    "OVERLAPPING_HOLDS",
                    f"color {color} has overlapping {left[2]} and {right[2]}",
                    file=filename,
                    beat=right[0],
                    difficulty=difficulty,
                    previousBeat=left[0],
                    currentBeat=right[0],
                    color=color,
                    previousPosition={"x": -1, "y": -1},
                    currentPosition={"x": -1, "y": -1},
                    previousDirection=8,
                    currentDirection=8,
                    availableRecoveryBeats=right[0] - left[1],
                    requiredRecoveryBeats=RECOVERY_BEATS.get(difficulty, 0.3),
                    failedConstraint="overlapping_holds",
                )

    parity = 0
    reaches = 0
    flow_conflicts = 0
    arc_links = {
        (
            int(item.get("c", -1)),
            round(float(item.get("b", -1)), 6),
            int(item.get("x", -1)),
            int(item.get("y", -1)),
            round(float(item.get("tb", -1)), 6),
            int(item.get("tx", -1)),
            int(item.get("ty", -1)),
        )
        for item in arrays["sliders"]
        if finite(item.get("b")) and finite(item.get("tb"))
    }
    chain_links: set[tuple[int, float, int, int, float, int, int]] = set()
    flow_events: dict[int, list[dict[str, Any]]] = {
        color: [dict(note) for note in notes if note.get("c") == color]
        for color in (0, 1)
    }
    for item in arrays["burstSliders"]:
        if item.get("c") not in (0, 1) or not finite(item.get("b")) or not finite(item.get("tb")):
            continue
        color = int(item["c"])
        link = (
            color,
            round(float(item["b"]), 6),
            int(item.get("x", -1)),
            int(item.get("y", -1)),
            round(float(item["tb"]), 6),
            int(item.get("tx", -1)),
            int(item.get("ty", -1)),
        )
        chain_links.add(link)
        flow_events[color].append(
            {
                "b": float(item["tb"]),
                "x": int(item.get("tx", -1)),
                "y": int(item.get("ty", -1)),
                "c": color,
                "d": int(item.get("d", 8)),
                "_virtualTail": "chain",
            }
        )
    recovery_beats = RECOVERY_BEATS.get(difficulty, 0.3)
    for color, hand_notes in flow_events.items():
        hand_notes.sort(key=lambda item: (float(item.get("b", 0)), bool(item.get("_virtualTail"))))
        for left, right in zip(hand_notes, hand_notes[1:]):
            gap = float(right["b"]) - float(left["b"])
            link = (
                color,
                round(float(left["b"]), 6),
                int(left["x"]),
                int(left["y"]),
                round(float(right["b"]), 6),
                int(right["x"]),
                int(right["y"]),
            )
            if link in arc_links or link in chain_links:
                continue
            finding = transition_finding(
                left,
                right,
                bpm,
                recovery_beats=recovery_beats,
            )
            if finding:
                flow_conflicts += 1
                if finding["constraint"] == "parity_family_repeat":
                    parity += 1
                detail = {key: value for key, value in finding.items() if key != "message"}
                report.add_motion(
                    "SAME_HAND_FLOW_CONFLICT",
                    f"color {color}: {finding['message']}",
                    file=filename,
                    beat=float(right["b"]),
                    difficulty=difficulty,
                    previousBeat=float(left["b"]),
                    currentBeat=float(right["b"]),
                    color=color,
                    previousPosition={"x": int(left["x"]), "y": int(left["y"])},
                    currentPosition={"x": int(right["x"]), "y": int(right["y"])},
                    previousDirection=int(left["d"]),
                    currentDirection=int(right["d"]),
                    availableRecoveryBeats=gap,
                    requiredRecoveryBeats=recovery_beats,
                    failedConstraint=str(finding.get("failedConstraint") or finding.get("constraint")),
                    **{key: value for key, value in detail.items() if key not in {
                        "difficulty", "previousBeat", "currentBeat", "color", "previousPosition",
                        "currentPosition", "previousDirection", "currentDirection",
                        "availableRecoveryBeats", "requiredRecoveryBeats", "failedConstraint",
                    }},
                )
            distance = math.hypot(int(right["x"]) - int(left["x"]), int(right["y"]) - int(left["y"]))
            if gap <= 0.25 and distance > 2.5:
                reaches += 1
                report.add_motion(
                    "UNREACHABLE_TRANSITION",
                    f"color {color} moves {distance:.2f} grid units in {gap:g} beat",
                    file=filename,
                    beat=float(right["b"]),
                    difficulty=difficulty,
                    previousBeat=float(left["b"]),
                    currentBeat=float(right["b"]),
                    color=color,
                    previousPosition={"x": int(left["x"]), "y": int(left["y"])},
                    currentPosition={"x": int(right["x"]), "y": int(right["y"])},
                    previousDirection=int(left["d"]),
                    currentDirection=int(right["d"]),
                    availableRecoveryBeats=gap,
                    requiredRecoveryBeats=recovery_beats,
                    failedConstraint="unreachable_lane_or_row",
                )
            if gap <= 0.25 and circular_direction_distance(int(left["d"]), int(right["d"])) == 0 and left["d"] != 8:
                report.add("warning", "REPEATED_DIRECTION", f"color {color} repeats cut direction at high speed", file=filename, beat=float(right["b"]))

    note_positions = {(round(float(note["b"]), 6), int(note["x"]), int(note["y"])) for note in notes}
    bomb_collisions = 0
    for bomb in arrays["bombNotes"]:
        key = (round(float(bomb.get("b", 0)), 6), int(bomb.get("x", -1)), int(bomb.get("y", -1)))
        if key in note_positions:
            bomb_collisions += 1
            report.add("error", "BOMB_NOTE_COLLISION", "bomb occupies the same beat and cell as a note", file=filename, beat=key[0])
        for note in notes:
            if not bomb_on_swing_path(bomb, note):
                continue
            if key in note_positions and int(note["x"]) == key[1] and int(note["y"]) == key[2] and round(float(note["b"]), 6) == key[0]:
                continue
            bomb_collisions += 1
            report.add_motion(
                "BOMB_SWING_PATH",
                "bomb sits in a pre-swing, contact, or follow-through cell",
                file=filename,
                beat=float(bomb.get("b", 0)),
                difficulty=difficulty,
                previousBeat=float(note.get("b", 0)),
                currentBeat=float(bomb.get("b", 0)),
                color=int(note.get("c", -1)),
                previousPosition={"x": int(note.get("x", -1)), "y": int(note.get("y", -1))},
                currentPosition={"x": int(bomb.get("x", -1)), "y": int(bomb.get("y", -1))},
                previousDirection=int(note.get("d", 8)),
                currentDirection=8,
                availableRecoveryBeats=abs(float(bomb.get("b", 0)) - float(note.get("b", 0))),
                requiredRecoveryBeats=RECOVERY_BEATS.get(difficulty, 0.3),
                failedConstraint="bomb_swing_path",
            )
            break
    for index, wall in enumerate(arrays["obstacles"]):
        if not finite(wall.get("b")) or not finite(wall.get("d")) or float(wall.get("d", 0)) <= 0:
            report.add("error", "INVALID_WALL_TIME", f"wall {index} has an invalid beat or duration", file=filename)
        if not isinstance(wall.get("w"), int) or int(wall.get("w", 0)) <= 0 or not isinstance(wall.get("h"), int) or int(wall.get("h", 0)) <= 0:
            report.add("error", "INVALID_WALL_SIZE", f"wall {index} has an invalid width or height", file=filename, beat=float(wall.get("b", 0)))
        if int(wall.get("x", 0)) <= 0 and int(wall.get("w", 0)) >= 4 and int(wall.get("y", 0)) <= 0 and int(wall.get("h", 0)) >= 5:
            report.add("error", "FORCED_WALL_DAMAGE", f"wall {index} fills every standing lane and height", file=filename, beat=float(wall.get("b", 0)))

    simultaneous: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        simultaneous[float(note["b"])].append(note)
    cross_hand_collisions = 0
    for beat, beat_notes in simultaneous.items():
        red = [note for note in beat_notes if note.get("c") == 0]
        blue = [note for note in beat_notes if note.get("c") == 1]
        if len(red) == 1 and len(blue) == 1:
            finding = cross_hand_finding(red[0], blue[0])
            if finding:
                cross_hand_collisions += 1
                report.add_motion(
                    "CROSS_HAND_PATH_COLLISION",
                    "simultaneous saber paths intersect or violate controller clearance",
                    file=filename,
                    beat=beat,
                    difficulty=difficulty,
                    previousBeat=beat,
                    currentBeat=beat,
                    color=-1,
                    previousPosition={"x": int(red[0]["x"]), "y": int(red[0]["y"])},
                    currentPosition={"x": int(blue[0]["x"]), "y": int(blue[0]["y"])},
                    previousDirection=int(red[0]["d"]),
                    currentDirection=int(blue[0]["d"]),
                    availableRecoveryBeats=0.0,
                    requiredRecoveryBeats=RECOVERY_BEATS.get(difficulty, 0.3),
                    failedConstraint=str(finding.get("failedConstraint") or finding.get("constraint")),
                    redPosition={"x": int(red[0]["x"]), "y": int(red[0]["y"])},
                    bluePosition={"x": int(blue[0]["x"]), "y": int(blue[0]["y"])},
                    redDirection=int(red[0]["d"]),
                    blueDirection=int(blue[0]["d"]),
                    **{key: value for key, value in finding.items() if key not in {
                        "message", "difficulty", "previousBeat", "currentBeat", "color",
                        "previousPosition", "currentPosition", "previousDirection", "currentDirection",
                        "availableRecoveryBeats", "requiredRecoveryBeats", "failedConstraint",
                    }},
                )
        if len(beat_notes) >= 2 and all(note["x"] in (1, 2) and note["y"] >= 1 for note in beat_notes):
            report.add_motion(
                "CENTER_VISION_DOUBLE",
                "simultaneous center notes block incoming vision",
                file=filename,
                beat=beat,
                difficulty=difficulty,
                previousBeat=beat,
                currentBeat=beat,
                color=-1,
                previousPosition={"x": int(beat_notes[0]["x"]), "y": int(beat_notes[0]["y"])},
                currentPosition={"x": int(beat_notes[1]["x"]), "y": int(beat_notes[1]["y"])},
                previousDirection=int(beat_notes[0].get("d", 8)),
                currentDirection=int(beat_notes[1].get("d", 8)),
                availableRecoveryBeats=0.0,
                requiredRecoveryBeats=RECOVERY_BEATS.get(difficulty, 0.3),
                failedConstraint="vision_block",
            )

    max_beat = max([float(note.get("b", 0)) for note in notes] + [0.0])
    return {
        "version": 3,
        "notes": len(notes),
        "bombs": len(arrays["bombNotes"]),
        "walls": len(arrays["obstacles"]),
        "arcs": len(arrays["sliders"]),
        "chains": len(arrays["burstSliders"]),
        "missingArcTails": missing_arc_tails,
        "nextSameColorAfterHold": next_same_after,
        "postHoldMissingOppositeSetup": missing_opposite_setup,
        "rapidSameHandAfterHold": rapid_same_after,
        "likelyParityBreaks": parity,
        "sameHandFlowConflicts": flow_conflicts,
        "crossHandPathCollisions": cross_hand_collisions,
        "unreachableTransitions": reaches,
        "bombNoteCollisions": bomb_collisions,
        "maxBeat": max_beat,
    }


def validate_basic(data: dict[str, Any], filename: str, report: ValidationReport) -> dict[str, Any]:
    version = major_version(data)
    if version == 2:
        raw_notes = [item for item in data.get("_notes", []) if isinstance(item, dict)]
        notes = [item for item in raw_notes if item.get("_type") in (0, 1)]
        bombs = [item for item in raw_notes if item.get("_type") == 3]
        walls = [item for item in data.get("_obstacles", []) if isinstance(item, dict)]
        return {"version": 2, "notes": len(notes), "bombs": len(bombs), "walls": len(walls), "arcs": len(data.get("_sliders", [])), "chains": len(data.get("_burstSliders", []))}
    if version >= 4:
        pairs = {"colorNotes": "colorNotesData", "bombNotes": "bombNotesData", "obstacles": "obstaclesData", "arcs": "arcsData", "chains": "chainsData"}
        for events, metadata in pairs.items():
            if not isinstance(data.get(events, []), list) or not isinstance(data.get(metadata, []), list):
                report.add("error", "V4_ARRAY_REQUIRED", f"{events} and {metadata} must be arrays", file=filename)
                continue
            for index, event in enumerate(data.get(events, [])):
                if not isinstance(event, dict):
                    report.add("error", "V4_OBJECT_REQUIRED", f"{events} item {index} is not an object", file=filename)
                    continue
                key = "ai" if events == "arcs" else "ci" if events == "chains" else "i"
                value = event.get(key)
                if not isinstance(value, int) or not 0 <= value < len(data.get(metadata, [])):
                    report.add("error", "V4_INDEX_RANGE", f"{events} item {index} has invalid {key} index", file=filename)
        return {"version": version, "notes": len(data.get("colorNotes", [])), "bombs": len(data.get("bombNotes", [])), "walls": len(data.get("obstacles", [])), "arcs": len(data.get("arcs", [])), "chains": len(data.get("chains", []))}
    report.add("error", "UNKNOWN_MAP_VERSION", "beatmap version is missing or unsupported", file=filename)
    return {"version": version, "notes": 0, "bombs": 0, "walls": 0, "arcs": 0, "chains": 0}


def validate_package(path: Path, vr_playtest: bool = False, sight_read: bool = False) -> ValidationReport:
    report = ValidationReport()
    if path.is_dir():
        package: Package = FolderPackage(path)
    elif path.is_file() and zipfile.is_zipfile(path):
        package = ZipPackage(path)
    else:
        report.add("error", "PACKAGE_NOT_FOUND", "path is not a map folder or ZIP archive", file=str(path))
        return report
    names = package.names()
    exact = set(names)
    if "Info.dat" not in exact:
        report.add("error", "INFO_MISSING", "Info.dat must exist at the package root")
        return report
    try:
        info = load_json_bytes(package.read("Info.dat"), "Info.dat")
    except ValueError as exc:
        report.add("error", "INFO_INVALID", str(exc), file="Info.dat")
        return report
    validate_info_colors(info, report)
    files, maps = info_refs(info)
    raw_bpm = info.get("beatsPerMinute", info.get("_beatsPerMinute", 120.0))
    bpm = float(raw_bpm) if finite(raw_bpm) and float(raw_bpm) > 0 else 120.0
    for filename in files:
        if filename not in exact:
            folded = [name for name in names if name.casefold() == filename.casefold()]
            if folded:
                report.add("error", "REFERENCE_CASE_MISMATCH", f"Info.dat references {filename}, but package contains {folded[0]}", file="Info.dat")
            else:
                report.add("error", "REFERENCE_MISSING", f"Info.dat references missing file {filename}", file="Info.dat")
    map_metrics: dict[str, Any] = {}
    for ref in maps:
        filename = ref["filename"]
        if filename not in exact:
            continue
        try:
            data = load_json_bytes(package.read(filename), filename)
            if not isinstance(data, dict):
                raise ValueError("root must be an object")
            metrics = (
                validate_v3(
                    data,
                    filename,
                    report,
                    bpm=bpm,
                    difficulty=ref["difficulty"],
                )
                if major_version(data) == 3
                else validate_basic(data, filename, report)
            )
            metrics.update({"difficulty": ref["difficulty"], "characteristic": ref["characteristic"]})
            map_metrics[filename] = metrics
        except (ValueError, TypeError) as exc:
            report.add("error", "BEATMAP_INVALID", str(exc), file=filename)
    click_evidence: dict[str, Any] = {}
    if path.is_dir():
        analysis_path = path / "_beatforge" / "analysis.json"
        if analysis_path.exists():
            try:
                analysis = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
                click_evidence = analysis.get("clickTrackEvidence") or {}
                checkpoints = click_evidence.get("checkpoints") or []
                if len(checkpoints) < 3:
                    report.add("warning", "CLICK_TRACK_EVIDENCE_INCOMPLETE", "click track is missing start, middle, or end sample checkpoints", file="_beatforge/analysis.json")
                if analysis.get("status") != "timing_verified":
                    report.add(
                        "warning",
                        "TIMING_UNVERIFIED",
                        "analysis status is not timing_verified; pack may still download. Use AI timing review to check the grid.",
                        file="_beatforge/analysis.json",
                    )
            except (OSError, json.JSONDecodeError) as exc:
                report.add("error", "TIMING_REPORT_INVALID", str(exc), file="_beatforge/analysis.json")
        else:
            report.add("warning", "TIMING_REPORT_MISSING", "no sample-based timing report is bundled")
    report.metrics = {
        "infoVersion": major_version(info),
        "mapCount": len(map_metrics),
        "maps": map_metrics,
        "totals": {
            key: sum(int(metrics.get(key, 0)) for metrics in map_metrics.values())
            for key in ("notes", "bombs", "walls", "arcs", "chains", "missingArcTails", "nextSameColorAfterHold", "postHoldMissingOppositeSetup", "rapidSameHandAfterHold", "sameHandFlowConflicts", "crossHandPathCollisions")
        },
        "clickTrackEvidence": click_evidence,
        "releaseGate": {
            "structuralInspection": not report.errors,
            "vrPlaytest": vr_playtest,
            "freshSightRead": sight_read,
            "aiReleaseRoute": ai_release_route_enabled(),
        },
    }
    if not report.errors:
        if vr_playtest and sight_read and ai_release_route_enabled():
            report.status = "release_candidate"
        else:
            report.status = "playtest_candidate"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("map", type=Path)
    parser.add_argument("--json", type=Path, help="write the machine-readable report")
    parser.add_argument("--vr-playtest-passed", action="store_true", help="record VR evidence; does not stamp release_candidate unless BEATFORGE_AI_RELEASE_ROUTE=1")
    parser.add_argument("--fresh-sight-read-passed", action="store_true", help="record sight-read evidence; same gate as --vr-playtest-passed")
    parser.add_argument("--expect-pch-v5", action="store_true", help="assert the known Version 5 regression counts")
    args = parser.parse_args()
    report = validate_package(args.map, args.vr_playtest_passed, args.fresh_sight_read_passed)
    if args.expect_pch_v5:
        totals = report.metrics.get("totals", {})
        expectations = {"arcs": 42, "missingArcTails": 42, "nextSameColorAfterHold": 40, "rapidSameHandAfterHold": 12, "crossHandPathCollisions": 8}
        for key, expected in expectations.items():
            actual = totals.get(key)
            if actual != expected:
                report.add("error", "PCH_REGRESSION_MISMATCH", f"expected {key}={expected}, found {actual}")
        expert_plus = report.metrics.get("maps", {}).get("ExpertPlus.dat", {})
        if expert_plus.get("rapidSameHandAfterHold") != 11:
            report.add(
                "error",
                "PCH_REGRESSION_MISMATCH",
                f"expected ExpertPlus rapidSameHandAfterHold=11, found {expert_plus.get('rapidSameHandAfterHold')}",
                file="ExpertPlus.dat",
            )
    payload = report.to_dict()
    if args.json:
        write_json(args.json, payload)
    print(json.dumps(payload, indent=2))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
