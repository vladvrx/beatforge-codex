"""Project authored musical beats into the constant BPM clock used by exported maps."""

from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
import math
from typing import Any


class ExportClock:
    def __init__(self, analysis: dict[str, Any]):
        self.bpm = float(analysis["bpm"])
        self.sample_rate = int(analysis.get("sampleRate", 44100))
        rows = analysis.get("beatGrid", [])
        if len(rows) < 2:
            raise ValueError("Export timing requires at least two adopted beat/sample rows")
        self.beats = [float(row["beat"]) for row in rows]
        self.samples = [float(row["sample"]) for row in rows]
        if not math.isfinite(self.bpm) or self.bpm <= 0 or self.sample_rate <= 0:
            raise ValueError("Invalid export BPM or sample rate")
        if not all(math.isfinite(value) for value in self.beats + self.samples):
            raise ValueError("Export timing grid contains a non-finite position")
        if any(right <= left for values in (self.beats, self.samples) for left, right in zip(values, values[1:])):
            raise ValueError("Export timing grid must increase in beat and sample positions")

    def beat(self, authored: float) -> float:
        index = min(len(self.beats) - 2, max(0, bisect_right(self.beats, authored) - 1))
        ratio = (authored - self.beats[index]) / (self.beats[index + 1] - self.beats[index])
        sample = self.samples[index] + ratio * (self.samples[index + 1] - self.samples[index])
        # Round to the source sample clock before writing fractional export beats.
        return round(round(sample) / self.sample_rate * self.bpm / 60.0, 8)


def project_map_timing(beatmap: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Keep absolute and relative generated v3 event times on the analyzed sample clock."""
    clock = ExportClock(analysis)
    result = deepcopy(beatmap)
    if result.get("customData", {}).get("_beatforgeTiming"):
        raise ValueError("Map timing has already been projected")
    if result.get("bpmEvents"):
        raise ValueError("Authored maps with BPM events require an explicit BPM-event conversion")
    for key in ("colorNotes", "bombNotes", "sliders", "burstSliders", "obstacles", "rotationEvents", "basicBeatmapEvents", "colorBoostBeatmapEvents"):
        for item in result.get(key, []):
            start = float(item["b"])
            item["b"] = clock.beat(start)
            if key in {"sliders", "burstSliders"}:
                item["tb"] = clock.beat(float(item["tb"]))
            elif key == "obstacles":
                item["d"] = round(clock.beat(start + float(item["d"])) - item["b"], 8)
            if key in {"basicBeatmapEvents", "colorBoostBeatmapEvents"}:
                item["b"] = max(0.0, item["b"])
    for key in ("lightColorEventBoxGroups", "lightRotationEventBoxGroups", "lightTranslationEventBoxGroups"):
        for group in result.get(key, []):
            start = float(group["b"])
            exported_start = clock.beat(start)
            group["b"] = max(0.0, exported_start)
            for box in group.get("e", []):
                # Only event arrays contain relative beat times. The filter's b is a flag.
                for array in ("e", "l", "t"):
                    for event in box.get(array, []):
                        if "b" in event:
                            event["b"] = round(clock.beat(start + float(event["b"])) - exported_start, 8)
    result["bpmEvents"] = []
    result.setdefault("customData", {})["_beatforgeTiming"] = {
        "schemaVersion": 1, "clock": "constant-bpm-sample-projection",
        "sampleRate": clock.sample_rate, "bpm": clock.bpm,
    }
    return result


def project_plan_timing(plan: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    clock = ExportClock(analysis)
    result = deepcopy(plan)
    for section in result.get("sections", []):
        for key in ("startBeat", "endBeat"):
            if key in section:
                section["authored" + key[0].upper() + key[1:]] = section[key]
                section[key] = clock.beat(float(section[key]))
        section["startSeconds"] = float(section["startBeat"]) * 60.0 / clock.bpm
        section["endSeconds"] = float(section["endBeat"]) * 60.0 / clock.bpm
    result["exportClock"] = "constant-bpm-sample-projection"
    return result
