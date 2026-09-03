"""Parser + fingerprint unit tests on hand-authored fixtures."""

from __future__ import annotations

from corpus.fingerprint import fingerprint_notes
from corpus.parse_maps import ParsedBomb, ParsedNote, ParsedWall, parse_difficulty_dat, parse_info_beatmaps


def test_parse_v2_notes() -> None:
    data = {
        "_version": "2.6.0",
        "_notes": [
            {"_time": 4.0, "_lineIndex": 1, "_lineLayer": 0, "_type": 0, "_cutDirection": 1},
            {"_time": 4.0, "_lineIndex": 2, "_lineLayer": 0, "_type": 1, "_cutDirection": 1},
            {"_time": 5.0, "_lineIndex": 1, "_lineLayer": 1, "_type": 3, "_cutDirection": 8},
        ],
        "_obstacles": [
            {"_time": 8.0, "_lineIndex": 0, "_type": 0, "_duration": 2.0, "_width": 1},
        ],
    }
    notes, bombs, walls, ver = parse_difficulty_dat(data)
    assert ver.startswith("2")
    assert len(notes) == 2
    assert notes[0].color == 0 and notes[1].color == 1
    assert len(bombs) == 1
    assert len(walls) == 1
    assert walls[0].duration == 2.0


def test_parse_v3_notes() -> None:
    data = {
        "version": "3.3.0",
        "colorNotes": [
            {"b": 10.0, "x": 0, "y": 0, "c": 0, "d": 1, "a": 0},
            {"b": 10.5, "x": 3, "y": 1, "c": 1, "d": 0, "a": 0},
        ],
        "bombNotes": [{"b": 12.0, "x": 1, "y": 1}],
        "obstacles": [{"b": 20.0, "x": 3, "y": 0, "d": 1.0, "w": 1, "h": 3}],
    }
    notes, bombs, walls, ver = parse_difficulty_dat(data)
    assert ver.startswith("3")
    assert [n.cut for n in notes] == [1, 0]
    assert bombs[0].x == 1
    assert walls[0].height == 3


def test_parse_v4_indexed() -> None:
    data = {
        "version": "4.0.0",
        "colorNotes": [{"b": 6.0, "r": 0, "i": 1}, {"b": 7.0, "r": 0, "i": 0}],
        "colorNotesData": [
            {"x": 2, "y": 0, "c": 1, "d": 0, "a": 0},
            {"x": 1, "y": 2, "c": 0, "d": 1, "a": 0},
        ],
        "bombNotes": [{"b": 8.0, "i": 0}],
        "bombNotesData": [{"x": 1, "y": 1}],
        "obstacles": [{"b": 9.0, "i": 0}],
        "obstaclesData": [{"x": 0, "y": 0, "d": 4.0, "w": 1, "h": 5}],
    }
    notes, bombs, walls, ver = parse_difficulty_dat(data)
    assert ver.startswith("4")
    assert notes[0].x == 1 and notes[0].y == 2 and notes[0].color == 0
    assert notes[1].x == 2 and notes[1].color == 1
    assert bombs[0].y == 1
    assert walls[0].duration == 4.0


def test_parse_info_v2_and_v4() -> None:
    info_v2 = {
        "_difficultyBeatmapSets": [
            {
                "_beatmapCharacteristicName": "Standard",
                "_difficultyBeatmaps": [
                    {
                        "_difficulty": "Expert",
                        "_beatmapFilename": "Expert.dat",
                        "_noteJumpMovementSpeed": 16,
                        "_noteJumpStartBeatOffset": 0,
                    }
                ],
            }
        ]
    }
    specs = parse_info_beatmaps(info_v2)
    assert specs[0]["filename"] == "Expert.dat"
    info_v4 = {
        "difficultyBeatmaps": [
            {
                "characteristic": "Standard",
                "difficulty": "Hard",
                "beatmapDataFilename": "Hard.dat",
                "noteJumpMovementSpeed": 12,
            }
        ]
    }
    specs4 = parse_info_beatmaps(info_v4)
    assert specs4[0]["difficulty"] == "Hard"


def _fixture_notes() -> list[ParsedNote]:
    # Per-hand down/up swings on quarter notes, plus one jump at beat 12
    notes: list[ParsedNote] = []
    for i in range(8):
        beat = 4.0 + i
        color = i % 2
        cut = 1 if (i // 2) % 2 == 0 else 0  # each hand: down, up, down, up
        x = 1 if color == 0 else 2
        notes.append(ParsedNote(beat=beat, x=x, y=0, color=color, cut=cut))
    notes.append(ParsedNote(beat=12.0, x=1, y=0, color=0, cut=1))
    notes.append(ParsedNote(beat=12.0, x=2, y=0, color=1, cut=1))
    return notes


def test_fingerprint_jump_and_nps() -> None:
    notes = _fixture_notes()
    fp = fingerprint_notes(
        notes,
        bombs=[ParsedBomb(beat=16.0, x=1, y=1)],
        walls=[ParsedWall(beat=8.0, x=0, y=0, duration=2.0, width=1, height=3)],
        bpm=120.0,
        duration=10.0,
        njs=16.0,
    )
    assert fp["note_count"] == 10
    assert fp["bomb_count"] == 1
    assert fp["wall_count"] == 1
    assert abs(fp["nps"] - 1.0) < 0.05  # 10 notes / 10s
    assert fp["jump_ratio"] > 0.1
    assert fp["hand_alt_ratio"] > 0.5
    assert fp["flow_ratio"] > 0.5
    assert fp["reset_ratio"] == 0.0
    assert fp["ioi_hist"].get("1.0", 0) > 0.4
    assert fp["row_hist"][0] == 1.0
    assert 0.4 <= fp["hand_balance"] <= 0.6


def test_fingerprint_reset_detection() -> None:
    notes = [
        ParsedNote(beat=4.0, x=1, y=0, color=0, cut=1),
        ParsedNote(beat=5.0, x=1, y=0, color=0, cut=1),
        ParsedNote(beat=6.0, x=1, y=0, color=0, cut=1),
        ParsedNote(beat=4.5, x=2, y=0, color=1, cut=0),
        ParsedNote(beat=5.5, x=2, y=0, color=1, cut=1),
    ]
    fp = fingerprint_notes(notes, bpm=120.0, duration=8.0)
    assert fp["reset_ratio"] > 0.3
