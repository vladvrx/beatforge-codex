"""LEGACY — exporter for the pre-premium pipeline. The studio pipeline packages
maps inside skills/beat-saber-mapping/scripts/generate_map.py (see
docs/ARCHITECTURE.md). Kept only for the legacy CLI and its tests.

Export Chart IR to Beat Saber custom map zip."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from beatforge.chart import Chart, time_to_beat
from beatforge.styles import DIFFICULTIES

# Minimal 1x1 JPEG (red-ish) as placeholder cover
_PLACEHOLDER_JPEG = bytes(
    [
        0xFF,
        0xD8,
        0xFF,
        0xE0,
        0x00,
        0x10,
        0x4A,
        0x46,
        0x49,
        0x46,
        0x00,
        0x01,
        0x01,
        0x00,
        0x00,
        0x01,
        0x00,
        0x01,
        0x00,
        0x00,
        0xFF,
        0xDB,
        0x00,
        0x43,
        0x00,
        0x08,
        0x06,
        0x06,
        0x07,
        0x06,
        0x05,
        0x08,
        0x07,
        0x07,
        0x07,
        0x09,
        0x09,
        0x08,
        0x0A,
        0x0C,
        0x14,
        0x0D,
        0x0C,
        0x0B,
        0x0B,
        0x0C,
        0x19,
        0x12,
        0x13,
        0x0F,
        0x14,
        0x1D,
        0x1A,
        0x1F,
        0x1E,
        0x1D,
        0x1A,
        0x1C,
        0x1C,
        0x20,
        0x24,
        0x2E,
        0x27,
        0x20,
        0x22,
        0x2C,
        0x23,
        0x1C,
        0x1C,
        0x28,
        0x37,
        0x29,
        0x2C,
        0x30,
        0x31,
        0x34,
        0x34,
        0x34,
        0x1F,
        0x27,
        0x39,
        0x3D,
        0x38,
        0x32,
        0x3C,
        0x2E,
        0x33,
        0x34,
        0x32,
        0xFF,
        0xC0,
        0x00,
        0x0B,
        0x08,
        0x00,
        0x01,
        0x00,
        0x01,
        0x01,
        0x01,
        0x11,
        0x00,
        0xFF,
        0xC4,
        0x00,
        0x1F,
        0x00,
        0x00,
        0x01,
        0x05,
        0x01,
        0x01,
        0x01,
        0x01,
        0x01,
        0x01,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x01,
        0x02,
        0x03,
        0x04,
        0x05,
        0x06,
        0x07,
        0x08,
        0x09,
        0x0A,
        0x0B,
        0xFF,
        0xC4,
        0x00,
        0xB5,
        0x10,
        0x00,
        0x02,
        0x01,
        0x03,
        0x03,
        0x02,
        0x04,
        0x03,
        0x05,
        0x05,
        0x04,
        0x04,
        0x00,
        0x00,
        0x01,
        0x7D,
        0x01,
        0x02,
        0x03,
        0x00,
        0x04,
        0x11,
        0x05,
        0x12,
        0x21,
        0x31,
        0x41,
        0x06,
        0x13,
        0x51,
        0x61,
        0x07,
        0x22,
        0x71,
        0x14,
        0x32,
        0x81,
        0x91,
        0xA1,
        0x08,
        0x23,
        0x42,
        0xB1,
        0xC1,
        0x15,
        0x52,
        0xD1,
        0xF0,
        0x24,
        0x33,
        0x62,
        0x72,
        0x82,
        0x09,
        0x0A,
        0x16,
        0x17,
        0x18,
        0x19,
        0x1A,
        0x25,
        0x26,
        0x27,
        0x28,
        0x29,
        0x2A,
        0x34,
        0x35,
        0x36,
        0x37,
        0x38,
        0x39,
        0x3A,
        0x43,
        0x44,
        0x45,
        0x46,
        0x47,
        0x48,
        0x49,
        0x4A,
        0x53,
        0x54,
        0x55,
        0x56,
        0x57,
        0x58,
        0x59,
        0x5A,
        0x63,
        0x64,
        0x65,
        0x66,
        0x67,
        0x68,
        0x69,
        0x6A,
        0x73,
        0x74,
        0x75,
        0x76,
        0x77,
        0x78,
        0x79,
        0x7A,
        0x83,
        0x84,
        0x85,
        0x86,
        0x87,
        0x88,
        0x89,
        0x8A,
        0x92,
        0x93,
        0x94,
        0x95,
        0x96,
        0x97,
        0x98,
        0x99,
        0x9A,
        0xA2,
        0xA3,
        0xA4,
        0xA5,
        0xA6,
        0xA7,
        0xA8,
        0xA9,
        0xAA,
        0xB2,
        0xB3,
        0xB4,
        0xB5,
        0xB6,
        0xB7,
        0xB8,
        0xB9,
        0xBA,
        0xC2,
        0xC3,
        0xC4,
        0xC5,
        0xC6,
        0xC7,
        0xC8,
        0xC9,
        0xCA,
        0xD2,
        0xD3,
        0xD4,
        0xD5,
        0xD6,
        0xD7,
        0xD8,
        0xD9,
        0xDA,
        0xE1,
        0xE2,
        0xE3,
        0xE4,
        0xE5,
        0xE6,
        0xE7,
        0xE8,
        0xE9,
        0xEA,
        0xF1,
        0xF2,
        0xF3,
        0xF4,
        0xF5,
        0xF6,
        0xF7,
        0xF8,
        0xF9,
        0xFA,
        0xFF,
        0xDA,
        0x00,
        0x08,
        0x01,
        0x01,
        0x00,
        0x00,
        0x3F,
        0x00,
        0x7B,
        0xDF,
        0xFF,
        0xD9,
    ]
)


def _find_ffmpeg() -> str | None:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        return bundled if Path(bundled).is_file() else None
    except (ImportError, OSError):
        return None


def convert_to_ogg(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".ogg":
        dest.write_bytes(src.read_bytes())
        return
    ffmpeg = _find_ffmpeg()
    ffmpeg_error: Exception | None = None
    if ffmpeg:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-c:a",
            "libvorbis",
            "-q:a",
            "6",
            str(dest),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return
        except (OSError, subprocess.CalledProcessError) as error:
            ffmpeg_error = error

    try:
        import soundfile as sf

        audio, sample_rate = sf.read(str(src), always_2d=True)
        sf.write(
            str(dest),
            audio,
            sample_rate,
            format="OGG",
            subtype="VORBIS",
        )
        if dest.is_file() and dest.stat().st_size > 0:
            return
        raise RuntimeError("libsndfile created an empty OGG file")
    except Exception as soundfile_error:
        if isinstance(ffmpeg_error, subprocess.CalledProcessError):
            detail = (ffmpeg_error.stderr or b"").decode(
                "utf-8", errors="replace"
            )
        elif ffmpeg_error is not None:
            detail = str(ffmpeg_error)
        else:
            detail = "not found on PATH"
        raise RuntimeError(
            f"audio conversion failed with ffmpeg ({detail}) and "
            f"libsndfile ({soundfile_error})"
        ) from soundfile_error


def render_cover(src: Path, dest: Path) -> bool:
    """Waveform cover art via ffmpeg showwavespic; False on any failure."""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return False
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-filter_complex",
        "showwavespic=s=512x512:colors=#e84d4d|#3d8bfd:split_channels=1",
        "-frames:v",
        "1",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        return dest.is_file() and dest.stat().st_size > 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def _preview_start(chart: Chart) -> float:
    """Preview the first energetic drop; fall back to 25% into the song."""
    for sec in chart.sections:
        if sec.label == "drop" and chart.duration * 0.15 <= sec.t <= chart.duration * 0.75:
            return round(min(max(sec.t, 0.0), max(0.0, chart.duration - 12.0)), 3)
    return round(min(12.0, max(0.0, chart.duration * 0.25)), 3)


def build_info_dat(chart: Chart, song_filename: str = "song.ogg") -> dict:
    beatmaps = []
    for name, diff in chart.difficulties.items():
        budget = DIFFICULTIES.get(name)
        if not budget:
            continue
        beatmaps.append(
            {
                "_difficulty": name,
                "_difficultyRank": budget.rank,
                "_beatmapFilename": f"{name}.dat",
                "_noteJumpMovementSpeed": budget.njs,
                "_noteJumpStartBeatOffset": budget.offset,
            }
        )
    return {
        "_version": "2.0.0",
        "_songName": chart.title,
        "_songSubName": "",
        "_songAuthorName": chart.artist,
        "_levelAuthorName": chart.level_author,
        "_beatsPerMinute": round(chart.bpm, 3),
        "_songTimeOffset": 0,
        "_shuffle": 0,
        "_shufflePeriod": 0.5,
        "_previewStartTime": _preview_start(chart),
        "_previewDuration": 10,
        "_songFilename": song_filename,
        "_coverImageFilename": "cover.jpg",
        "_environmentName": "DefaultEnvironment",
        "_allDirectionsEnvironmentName": "GlassDesertEnvironment",
        "_difficultyBeatmapSets": [
            {
                "_beatmapCharacteristicName": "Standard",
                "_difficultyBeatmaps": beatmaps,
            }
        ],
    }


def _build_lightshow(chart: Chart) -> tuple[list[dict], list[dict]]:
    """Section-aware lighting: calm rings in intros, bar-synced lasers in drops."""
    from beatforge.analyze import section_at

    events: list[dict] = []
    boosts: list[dict] = []
    boosted = False
    for idx, bt in enumerate(chart.beats):
        if bt < 3.0:
            continue
        b = round(time_to_beat(bt, chart.beats, chart.bpm), 3)
        sec = section_at(chart.sections, bt)
        wants_boost = sec.label == "drop" and sec.energy >= 0.6
        if wants_boost != boosted:
            boosts.append({"b": b, "o": wants_boost, "f": 1.0})
            boosted = wants_boost
        bar_start = idx % 4 == 0
        half = idx % 2 == 0
        if sec.label in ("intro", "outro"):
            if bar_start:
                events.append({"b": b, "et": 1, "i": 1, "f": 0.6})
        elif sec.label == "verse":
            if bar_start:
                events.append(
                    {"b": b, "et": 0, "i": 5 if (idx // 4) % 2 else 1, "f": 0.8}
                )
            if half:
                events.append({"b": b, "et": 1, "i": 1, "f": 0.6})
        else:  # build / drop / body
            if bar_start:
                events.append({"b": b, "et": 2, "i": 7, "f": 1.0})
                events.append({"b": b, "et": 3, "i": 3, "f": 1.0})
                if sec.label == "drop":
                    events.append({"b": b, "et": 4, "i": 1, "f": 1.0})
            elif half:
                events.append({"b": b, "et": 2, "i": 3, "f": 0.6})
                events.append({"b": b, "et": 3, "i": 7, "f": 0.6})
    return events, boosts


def build_difficulty_dat(chart: Chart, diff_name: str) -> dict:
    diff = chart.difficulties[diff_name]
    color_notes = []
    for n in diff.notes:
        color_notes.append(
            {
                "b": round(n.beat, 3),
                "x": int(n.lane),
                "y": int(n.row),
                "c": n.color,
                "d": n.cut_dir,
                "a": 0,
            }
        )
    obstacles = []
    for o in diff.obstacles:
        obstacles.append(
            {
                "b": round(o.beat, 3),
                "x": int(o.lane),
                "y": int(o.row),
                "d": round(o.duration_beats, 3),
                "w": int(o.width),
                "h": int(o.height),
            }
        )
    sliders = []
    for arc in diff.arcs:
        sliders.append(
            {
                "c": arc.color,
                "b": round(arc.beat, 3),
                "x": int(arc.lane),
                "y": int(arc.row),
                "d": arc.cut_dir,
                "mu": round(float(arc.head_control), 3),
                "tb": round(arc.tail_beat, 3),
                "tx": int(arc.tail_lane),
                "ty": int(arc.tail_row),
                "tc": arc.tail_cut_dir,
                "tmu": round(float(arc.tail_control), 3),
                "m": int(arc.mid_anchor_mode),
            }
        )
    bombs = []
    for b in diff.bombs:
        bombs.append(
            {
                "b": round(b.beat, 3),
                "x": int(b.lane),
                "y": int(b.row),
            }
        )

    # Section-aware beat-synced lightshow
    basic_events, boost_events = _build_lightshow(chart)

    return {
        "version": "3.3.0",
        "bpmEvents": [],
        "rotationEvents": [],
        "colorNotes": color_notes,
        "bombNotes": bombs,
        "obstacles": obstacles,
        "sliders": sliders,
        "burstSliders": [],
        "waypoints": [],
        "basicBeatmapEvents": basic_events,
        "colorBoostBeatmapEvents": boost_events,
        "lightColorEventBoxGroups": [],
        "lightRotationEventBoxGroups": [],
        "lightTranslationEventBoxGroups": [],
        "vfxEventBoxGroups": [],
        "_fxEventsCollection": {"_fl": [], "_il": []},
        "basicEventTypesWithKeywords": {},
        "useNormalEventsAsCompatibleEvents": True,
    }


def export_zip(
    chart: Chart,
    audio_path: Path,
    zip_path: Path,
    *,
    cover_path: Path | None = None,
    keep_workdir: Path | None = None,
) -> Path:
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    work = keep_workdir or zip_path.parent / f"_work_{zip_path.stem}"
    work.mkdir(parents=True, exist_ok=True)
    ogg = work / "song.ogg"
    convert_to_ogg(Path(audio_path), ogg)

    cover = work / "cover.jpg"
    if cover_path and Path(cover_path).is_file():
        cover.write_bytes(Path(cover_path).read_bytes())
    elif not render_cover(Path(audio_path), cover):
        cover.write_bytes(_PLACEHOLDER_JPEG)

    info = build_info_dat(chart)
    (work / "Info.dat").write_text(json.dumps(info, indent=2), encoding="utf-8")
    (work / "chart.json").write_text(
        json.dumps(chart.to_dict(), indent=2), encoding="utf-8"
    )

    for name in chart.difficulties:
        dat = build_difficulty_dat(chart, name)
        (work / f"{name}.dat").write_text(
            json.dumps(dat, separators=(",", ":")), encoding="utf-8"
        )

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(work / "Info.dat", "Info.dat")
        zf.write(ogg, "song.ogg")
        zf.write(cover, "cover.jpg")
        for name in chart.difficulties:
            zf.write(work / f"{name}.dat", f"{name}.dat")

    if keep_workdir is None:
        import shutil

        shutil.rmtree(work, ignore_errors=True)

    return zip_path


def export_zip_bytes(chart: Chart, audio_path: Path) -> bytes:
    buf = io.BytesIO()
    # Use temp dir beside audio
    tmp_dir = Path(audio_path).parent / "_zip_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_zip = tmp_dir / "map.zip"
    export_zip(chart, audio_path, tmp_zip, keep_workdir=tmp_dir / "work")
    data = tmp_zip.read_bytes()
    return data
