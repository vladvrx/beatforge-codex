"""Pipeline tests: 120 BPM click track, grid, spacing, zip contents."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from beatforge.export_beatsaber import _find_ffmpeg, export_zip
from beatforge.place import generate_chart

FFMPEG = _find_ffmpeg()


def write_click_track(path: Path, bpm: float = 120.0, duration: float = 16.0, sr: int = 22050) -> Path:
    n = int(sr * duration)
    y = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    click_len = int(0.012 * sr)
    env = np.hanning(click_len).astype(np.float32)
    i = 0
    t = 0.0
    while t < duration - 0.02:
        start = int(t * sr)
        freq = 1200.0 if i % 4 == 0 else 900.0
        tone = np.sin(2 * np.pi * freq * np.arange(click_len) / sr).astype(np.float32) * env
        end = min(n, start + click_len)
        y[start:end] += tone[: end - start]
        i += 1
        t += period
    peak = float(np.max(np.abs(y))) or 1.0
    y = (y / peak) * 0.9
    sf.write(str(path), y, sr)
    return path


@pytest.fixture(scope="module")
def click_wav(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("click")
    return write_click_track(d / "click_120.wav")


@pytest.fixture(scope="module")
def expert_chart(click_wav: Path):
    return generate_chart(
        click_wav,
        title="Metronome",
        artist="Test",
        difficulties=["Expert"],
        style="flow",
        seed=1,
    )


def test_bpm_near_120(expert_chart) -> None:
    assert abs(expert_chart.bpm - 120.0) <= 2.0


def test_notes_exist(expert_chart) -> None:
    notes = expert_chart.difficulties["Expert"].notes
    assert len(notes) >= 8


def test_notes_on_beat_grid(expert_chart) -> None:
    notes = expert_chart.difficulties["Expert"].notes
    for n in notes:
        frac = n.beat % 1.0
        dist = min(frac, 1.0 - frac, abs(frac - 0.5), abs(frac - 0.25), abs(frac - 0.75))
        assert dist < 0.12, f"note beat {n.beat} not on 1/4 or 1/8 grid"


def test_per_hand_spacing(expert_chart) -> None:
    notes = expert_chart.difficulties["Expert"].notes
    min_gap = 0.35
    for hand in ("left", "right"):
        beats = [n.beat for n in notes if n.hand == hand]
        for a, b in zip(beats, beats[1:]):
            assert b - a >= min_gap - 0.06, f"{hand} gap {b - a} < {min_gap}"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_zip_contents(expert_chart, click_wav: Path, tmp_path: Path) -> None:
    zpath = tmp_path / "map.zip"
    export_zip(expert_chart, click_wav, zpath, keep_workdir=tmp_path / "work")
    assert zpath.is_file()
    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
    assert "Info.dat" in names
    assert "song.ogg" in names
    assert any(n.endswith(".dat") and n != "Info.dat" for n in names)
    info = json.loads(zipfile.ZipFile(zpath).read("Info.dat"))
    assert info["_version"] == "2.0.0"
    dat_name = info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"][0]["_beatmapFilename"]
    diff = json.loads(zipfile.ZipFile(zpath).read(dat_name))
    assert diff["version"] == "3.3.0"
    assert "colorNotes" in diff
    assert all(k in diff["colorNotes"][0] for k in ("b", "x", "y", "c", "d"))
