from __future__ import annotations

import math
import random

import numpy as np

from beatforge.analyze import _detect_sustains
from beatforge.chart import (
    AnalysisResult,
    Arc,
    Chart,
    DifficultyChart,
    Note,
    Obstacle,
    Section,
    SustainRegion,
    time_to_beat,
)
from beatforge.export_beatsaber import _build_lightshow, build_difficulty_dat
from beatforge.place import _place_arcs, _place_walls
from beatforge.styles import DIFFICULTIES, STYLES


def make_offset_analysis() -> AnalysisResult:
    beats = [0.25 + i * 0.5 for i in range(40)]
    return AnalysisResult(
        bpm=120.0,
        duration=20.0,
        beats=beats,
        onset_times=[],
        onset_strengths=[],
        beat_energy=[0.8] * len(beats),
        sections=[
            Section(t=0.25, label="verse", energy=0.6),
            Section(t=4.25, label="drop", energy=0.9),
            Section(t=16.25, label="outro", energy=0.4),
        ],
        sustains=[SustainRegion(t=5.25, end_t=7.25, strength=0.9)],
    )


def test_sustained_phrase_becomes_playable_arc() -> None:
    analysis = make_offset_analysis()
    notes = [
        Note(t=5.25, beat=10.0, lane=1, row=0, hand="left", cut="up"),
        Note(t=7.25, beat=14.0, lane=0, row=2, hand="left", cut="down"),
        Note(t=6.0, beat=11.5, lane=2, row=1, hand="right", cut="up"),
    ]

    arcs = _place_arcs(analysis, notes, DIFFICULTIES["Expert"])

    assert len(arcs) == 1
    arc = arcs[0]
    assert arc.beat == 10.0 and arc.tail_beat == 14.0
    assert (arc.lane, arc.row) == (1, 0)
    assert (arc.tail_lane, arc.tail_row) == (0, 2)
    assert arc.hand == "left"
    assert not any(arc.beat < note.beat < arc.tail_beat for note in notes)


def test_harmonic_audio_produces_sustain_regions() -> None:
    sr = 22050
    duration = 8.0
    t = np.arange(int(sr * duration)) / sr
    y = (0.35 * np.sin(2 * math.pi * 220.0 * t)).astype(np.float32)
    beats = [i * 0.5 for i in range(16)]

    regions = _detect_sustains(
        y,
        sr,
        beats,
        [Section(t=0.0, label="body", energy=0.7)],
        duration,
    )

    assert regions
    assert all(region.end_t > region.t for region in regions)


def test_walls_use_visible_safe_geometry_and_detected_grid() -> None:
    analysis = make_offset_analysis()

    walls = _place_walls(analysis, [], STYLES["flow"], random.Random(4))

    assert walls
    for wall in walls:
        assert wall.beat == time_to_beat(wall.t, analysis.beats, analysis.bpm)
        assert 0 <= wall.lane and wall.lane + wall.width <= 4
        assert 0 <= wall.row and wall.row + wall.height <= 5
        assert wall.duration_beats > 0
        assert (wall.row, wall.height, wall.width) in {(0, 5, 2), (2, 3, 4)}
    assert len(walls) >= 2


def test_chill_style_still_gets_sparse_visible_walls() -> None:
    analysis = make_offset_analysis()
    analysis.sections = [Section(t=0.25, label="verse", energy=0.42)]

    walls = _place_walls(analysis, [], STYLES["chill"], random.Random(9))

    assert walls
    assert len(walls) <= 2
    assert all(wall.width == 2 and wall.height == 5 for wall in walls)


def test_strong_attack_with_energetic_tail_becomes_fallback_arc() -> None:
    analysis = make_offset_analysis()
    analysis.sustains = []
    analysis.onset_times = [5.25, 9.25]
    analysis.onset_strengths = [0.95, 0.8]
    notes = [
        Note(t=5.25, beat=10.0, lane=1, row=0, hand="left", cut="up"),
        Note(t=7.25, beat=14.0, lane=0, row=2, hand="left", cut="down"),
        Note(t=6.0, beat=11.5, lane=2, row=1, hand="right", cut="up"),
    ]

    arcs = _place_arcs(analysis, notes, DIFFICULTIES["Expert"])

    assert arcs
    assert arcs[0].tail_beat - arcs[0].beat >= 2.0


def test_lightshow_turns_color_boost_on_and_off_with_sections() -> None:
    analysis = make_offset_analysis()
    chart = Chart(
        title="lights",
        artist="test",
        bpm=analysis.bpm,
        duration=analysis.duration,
        beats=analysis.beats,
        sections=[
            Section(t=0.25, label="verse", energy=0.5),
            Section(t=4.25, label="drop", energy=0.9),
            Section(t=12.25, label="outro", energy=0.3),
        ],
        difficulties={"Expert": DifficultyChart()},
    )

    _, boosts = _build_lightshow(chart)

    assert [event["o"] for event in boosts] == [True, False]


def test_v3_export_contains_arcs_and_wall_dimensions() -> None:
    analysis = make_offset_analysis()
    head = Note(t=5.25, beat=10.0, lane=1, row=0, hand="left", cut="up")
    arc = Arc(
        t=5.25,
        beat=10.0,
        tail_t=7.25,
        tail_beat=14.0,
        lane=1,
        row=0,
        tail_lane=0,
        tail_row=2,
        hand="left",
        cut="up",
        tail_cut="down",
    )
    diff = DifficultyChart(
        notes=[head],
        arcs=[arc],
        obstacles=[
            Obstacle(t=8.25, beat=16.0, duration_beats=2.0, lane=0, row=0, width=2, height=5),
            Obstacle(t=10.25, beat=20.0, duration_beats=2.0, lane=0, row=2, width=4, height=3),
        ],
    )
    chart = Chart(
        title="objects",
        artist="test",
        bpm=analysis.bpm,
        duration=analysis.duration,
        beats=analysis.beats,
        sections=analysis.sections,
        difficulties={"Expert": diff},
        style="flow",
    )

    data = build_difficulty_dat(chart, "Expert")

    assert data["sliders"] == [
        {
            "c": 0,
            "b": 10.0,
            "x": 1,
            "y": 0,
            "d": 0,
            "mu": 1.0,
            "tb": 14.0,
            "tx": 0,
            "ty": 2,
            "tc": 1,
            "tmu": 1.0,
            "m": 0,
        }
    ]
    assert [(w["y"], w["h"], w["w"]) for w in data["obstacles"]] == [
        (0, 5, 2),
        (2, 3, 4),
    ]
    assert all(float(event["b"]).is_integer() for event in data["basicBeatmapEvents"])


def test_chart_round_trip_preserves_arcs() -> None:
    analysis = make_offset_analysis()
    arc = Arc(
        t=5.25,
        beat=10.0,
        tail_t=7.25,
        tail_beat=14.0,
        lane=1,
        row=0,
        tail_lane=0,
        tail_row=2,
        hand="left",
        cut="up",
        tail_cut="down",
    )
    chart = Chart(
        title="round trip",
        artist="test",
        bpm=120.0,
        duration=20.0,
        beats=analysis.beats,
        sections=analysis.sections,
        difficulties={"Expert": DifficultyChart(arcs=[arc])},
    )

    restored = Chart.from_dict(chart.to_dict())

    assert restored.difficulties["Expert"].arcs == [arc]
