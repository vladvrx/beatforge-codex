"""Versioned, deterministic creative controls shared by Studio and the portable mapper."""

from __future__ import annotations

import math
import re
from typing import Any

SCHEMA_VERSION = 1
INSTRUMENTS = ("auto", "drums", "bass", "vocals", "guitar", "piano", "other")
STYLES = ("balanced", "flow", "tech", "chill")
SCALE_FIELDS = ("intensity", "density", "verseDensity", "chorusDensity")
DEFAULTS = {
    "schemaVersion": SCHEMA_VERSION, "brief": "", "intensity": 1.0,
    "density": 1.0, "verseDensity": 1.0, "chorusDensity": 1.0,
    "dominantInstrument": "auto", "style": "balanced", "noBombs": False,
    "noWalls": False, "candidateCount": 2, "sectionOverrides": [],
}


def _scale(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.5 <= value <= 1.5:
        raise ValueError(f"{field} must be a number from 0.5 to 1.5")
    return float(value)


def normalize_mapping_plan(value: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve supported brief phrases, with explicit controls taking precedence."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("mapping plan must be an object")
    unknown = set(value) - set(DEFAULTS) - {"interpretation"}
    if unknown:
        raise ValueError("unknown mapping plan controls: " + ", ".join(sorted(unknown)))
    if type(value.get("schemaVersion", 1)) is not int or value.get("schemaVersion", 1) != 1:
        raise ValueError("mapping plan schemaVersion must be 1")
    brief = value.get("brief", "")
    if not isinstance(brief, str) or len(brief) > 2000:
        raise ValueError("brief must be text of at most 2000 characters")
    text = brief.casefold()
    inferred: dict[str, Any] = {}
    recognized: list[str] = []
    phrases = [
        (r"\b(?:no|without) bombs\b", "noBombs", True, "No bombs"),
        (r"\b(?:no|without) walls\b", "noWalls", True, "No walls"),
        (r"\b(?:sparse|lighter|easier) verses?\b", "verseDensity", 0.75, "Lighter verses"),
        (r"\b(?:dense|denser|bigger|stronger) chorus(?:es)?\b", "chorusDensity", 1.25, "Denser choruses"),
        (r"\b(?:less|fewer) notes\b", "density", 0.75, "Fewer notes"),
        (r"\bmore notes\b", "density", 1.25, "More notes"),
        (r"\b(?:relaxed|chill)\b", "style", "chill", "Chill style"),
        (r"\b(?:flow|flowing|smooth)\b", "style", "flow", "Flow style"),
        (r"\b(?:tech|technical)\b", "style", "tech", "Tech style"),
    ]
    for pattern, field, setting, description in phrases:
        if re.search(pattern, text):
            inferred[field] = setting
            if field not in value:
                recognized.append(description)
    for instrument in INSTRUMENTS[1:]:
        if re.search(r"\b(?:follow|focus on|emphasize) (?:the )?" + instrument + r"\b", text):
            inferred["dominantInstrument"] = instrument
            if "dominantInstrument" not in value:
                recognized.append("Follow " + instrument)
    result = {**DEFAULTS, **inferred, **value, "brief": brief}
    for field in SCALE_FIELDS:
        result[field] = _scale(result[field], field)
    if result["dominantInstrument"] not in INSTRUMENTS:
        raise ValueError("unknown dominantInstrument")
    if result["style"] not in STYLES:
        raise ValueError("unknown mapping style")
    for field in ("noBombs", "noWalls"):
        if not isinstance(result[field], bool):
            raise ValueError(f"{field} must be a boolean")
    count = result["candidateCount"]
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 3:
        raise ValueError("candidateCount must be an integer from 1 to 3")
    overrides = result["sectionOverrides"]
    if not isinstance(overrides, list) or len(overrides) > 200:
        raise ValueError("sectionOverrides must be a list with at most 200 entries")
    checked = []
    seen = set()
    for override in overrides:
        if not isinstance(override, dict) or set(override) - {"id", "density", "intensity", "dominantInstrument"}:
            raise ValueError("invalid section override")
        section_id = override.get("id")
        if not isinstance(section_id, str) or not section_id or len(section_id) > 100 or section_id in seen:
            raise ValueError("section overrides need unique nonempty ids")
        seen.add(section_id)
        item = dict(override)
        for field in ("density", "intensity"):
            if field in item:
                item[field] = _scale(item[field], "section " + field)
        if "dominantInstrument" in item and item["dominantInstrument"] not in INSTRUMENTS:
            raise ValueError("unknown section dominantInstrument")
        checked.append(item)
    result["sectionOverrides"] = checked
    interpretation = value.get("interpretation", [])
    if not isinstance(interpretation, list) or not all(isinstance(item, str) for item in interpretation):
        raise ValueError("interpretation must be a list of phrases")
    result["interpretation"] = recognized or list(interpretation)
    return result


def build_section_plan(analysis: dict[str, Any], sections: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    plan = normalize_mapping_plan(value)
    source = sections.get("sections") or [{"label": "body", "startBeat": 0.0, "endBeat": float(analysis.get("durationSeconds", 0.0)) * float(analysis.get("bpm", 120.0)) / 60.0, "intensity": 0.5}]
    overrides = {entry["id"]: entry for entry in plan["sectionOverrides"]}
    style_scale = {"balanced": 1.0, "flow": 0.95, "tech": 1.05, "chill": 0.75}[plan["style"]]
    planned = []
    for index, section in enumerate(source):
        item = dict(section)
        section_id = str(item.get("id") or f"section-{index:03d}")
        label = str(item.get("label") or item.get("type") or "body")
        family = re.sub(r"[\d\W_]+", "", label.casefold()) or "body"
        chorus = any(word in family for word in ("chorus", "drop", "peak"))
        verse = "verse" in family
        factor = plan["chorusDensity"] if chorus else plan["verseDensity"] if verse else 1.0
        override = overrides.get(section_id, {})
        density = plan["density"] * factor * style_scale * override.get("density", 1.0)
        item.update({
            "id": section_id, "label": label, "motifId": family,
            "motifOffset": sum((i + 1) * ord(character) for i, character in enumerate(family)) % 4,
            "density": round(max(0.35, min(1.75, density)), 6),
            "intensity": max(0.0, min(1.0, float(item.get("intensity", 0.5)) * plan["intensity"] * override.get("intensity", 1.0))),
            "dominantInstrument": override.get("dominantInstrument", plan["dominantInstrument"]),
            "style": plan["style"],
        })
        planned.append(item)
    missing = set(overrides) - {item["id"] for item in planned}
    if missing:
        raise ValueError("unknown section ids: " + ", ".join(sorted(missing)))
    return {"schemaVersion": 1, "source": sections.get("source", "internal"), "sections": planned, "controls": plan}
