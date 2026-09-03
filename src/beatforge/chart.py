"""LEGACY — chart IR for the pre-premium pipeline. The studio pipeline uses the
beat-saber-mapping skill's own normalization instead (see docs/ARCHITECTURE.md).

Beat Saber chart representation used by placement, review, and export."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any


class Hand(IntEnum):
    LEFT = 0  # red
    RIGHT = 1  # blue


class Cut(IntEnum):
    UP = 0
    DOWN = 1
    LEFT = 2
    RIGHT = 3
    UP_LEFT = 4
    UP_RIGHT = 5
    DOWN_LEFT = 6
    DOWN_RIGHT = 7
    DOT = 8


CUT_NAMES = {
    Cut.UP: "up",
    Cut.DOWN: "down",
    Cut.LEFT: "left",
    Cut.RIGHT: "right",
    Cut.UP_LEFT: "up_left",
    Cut.UP_RIGHT: "up_right",
    Cut.DOWN_LEFT: "down_left",
    Cut.DOWN_RIGHT: "down_right",
    Cut.DOT: "dot",
}

NAME_TO_CUT = {v: k for k, v in CUT_NAMES.items()}


@dataclass
class Note:
    t: float
    beat: float
    lane: int  # 0-3
    row: int  # 0-2
    hand: str  # "left" | "right"
    cut: str  # cut name
    strength: float = 1.0

    @property
    def color(self) -> int:
        return 0 if self.hand == "left" else 1

    @property
    def cut_dir(self) -> int:
        return int(NAME_TO_CUT.get(self.cut, Cut.DOWN))


@dataclass
class Arc:
    """A Beat Saber arc from a playable head note to a guided tail."""

    t: float
    beat: float
    tail_t: float
    tail_beat: float
    lane: int
    row: int
    tail_lane: int
    tail_row: int
    hand: str
    cut: str
    tail_cut: str
    head_control: float = 1.0
    tail_control: float = 1.0
    mid_anchor_mode: int = 0

    @property
    def color(self) -> int:
        return 0 if self.hand == "left" else 1

    @property
    def cut_dir(self) -> int:
        return int(NAME_TO_CUT.get(self.cut, Cut.DOWN))

    @property
    def tail_cut_dir(self) -> int:
        return int(NAME_TO_CUT.get(self.tail_cut, Cut.DOWN))


@dataclass
class Obstacle:
    t: float
    beat: float
    duration_beats: float
    lane: int
    row: int
    width: int = 1
    height: int = 3


@dataclass
class Bomb:
    t: float
    beat: float
    lane: int
    row: int


@dataclass
class Section:
    t: float
    label: str
    energy: float


@dataclass
class SustainRegion:
    """A tonal region whose energy continues after its attack."""

    t: float
    end_t: float
    strength: float


@dataclass
class DifficultyChart:
    notes: list[Note] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    obstacles: list[Obstacle] = field(default_factory=list)
    bombs: list[Bomb] = field(default_factory=list)


@dataclass
class Chart:
    title: str
    artist: str
    bpm: float
    duration: float
    beats: list[float]
    sections: list[Section]
    difficulties: dict[str, DifficultyChart]
    style: str = "auto"
    level_author: str = "Beatforge"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "bpm": self.bpm,
            "duration": self.duration,
            "beats": self.beats,
            "sections": [asdict(s) for s in self.sections],
            "style": self.style,
            "level_author": self.level_author,
            "difficulties": {
                name: {
                    "notes": [asdict(n) for n in diff.notes],
                    "arcs": [asdict(a) for a in diff.arcs],
                    "obstacles": [asdict(o) for o in diff.obstacles],
                    "bombs": [asdict(b) for b in diff.bombs],
                }
                for name, diff in self.difficulties.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chart:
        diffs: dict[str, DifficultyChart] = {}
        for name, d in data.get("difficulties", {}).items():
            diffs[name] = DifficultyChart(
                notes=[Note(**n) for n in d.get("notes", [])],
                arcs=[Arc(**a) for a in d.get("arcs", [])],
                obstacles=[Obstacle(**o) for o in d.get("obstacles", [])],
                bombs=[Bomb(**b) for b in d.get("bombs", [])],
            )
        return cls(
            title=data.get("title", "Untitled"),
            artist=data.get("artist", "Unknown"),
            bpm=float(data["bpm"]),
            duration=float(data["duration"]),
            beats=list(data.get("beats", [])),
            sections=[Section(**s) for s in data.get("sections", [])],
            difficulties=diffs,
            style=data.get("style", "auto"),
            level_author=data.get("level_author", "Beatforge"),
        )


@dataclass
class AnalysisResult:
    bpm: float
    duration: float
    beats: list[float]
    onset_times: list[float]
    onset_strengths: list[float]
    beat_energy: list[float]
    sections: list[Section]
    sustains: list[SustainRegion] = field(default_factory=list)


def time_to_beat(t: float, beats: list[float], bpm: float = 120.0) -> float:
    """Map an audio timestamp onto the detected beat grid without losing offset."""

    if len(beats) < 2:
        return max(0.0, t * bpm / 60.0)
    lo = beats[0]
    hi = beats[-1]
    if t <= lo:
        step = (beats[1] - beats[0]) or 1e-6
        return max(0.0, (t - lo) / step)
    if t >= hi:
        step = (beats[-1] - beats[-2]) or 1e-6
        return (len(beats) - 1) + (t - hi) / step

    import bisect

    i = bisect.bisect_right(beats, t)
    a, b = beats[i - 1], beats[i]
    frac = (t - a) / ((b - a) or 1e-6)
    return (i - 1) + min(max(frac, 0.0), 1.0)


def beat_to_time(beat: float, beats: list[float], bpm: float = 120.0) -> float:
    """Map a beat position back onto the detected audio timeline."""

    if len(beats) < 2:
        return max(0.0, beat * 60.0 / bpm)
    if beat <= 0.0:
        step = (beats[1] - beats[0]) or 1e-6
        return max(0.0, beats[0] + beat * step)
    last = len(beats) - 1
    if beat >= last:
        step = (beats[-1] - beats[-2]) or 1e-6
        return beats[-1] + (beat - last) * step
    i = int(beat)
    frac = beat - i
    return beats[i] + (beats[i + 1] - beats[i]) * frac
