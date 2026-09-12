"""Build an original 30-second score and real, validated browser demo charts.

This fixture invokes canonical choreography directly with a mathematically
authored sample timeline. It does not pretend to analyze a recording, run the
official-corpus pipeline, use a trained model, or provide headset evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "beat-saber-mapping" / "scripts"
for directory in (ROOT / "src", SCRIPTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from choreography import generate_all
from beatforge_core import encode_ogg
from generate_map import DAFT_PUNK_COLOR_SCHEME, info_dat, write_default_cover
from mapping_plan import build_section_plan, normalize_mapping_plan
from timing_export import project_map_timing, project_plan_timing
from validate_map import validate_package
from beatforge.premium import package_map, summarize_map
from beatforge.preview import preview_payload

SAMPLE_RATE = 44100
BPM = 120.0
DURATION = 30.0
SEED = 20260911
DIFFICULTIES = ("Easy", "Normal", "Hard", "Expert", "ExpertPlus")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def synthesize_score() -> tuple[np.ndarray, dict[str, Any], dict[str, Any]]:
    """Synthesize every instrument on the same integer sample clock as the score."""
    length = int(SAMPLE_RATE * DURATION)
    stems = {name: np.zeros(length, dtype=np.float64) for name in ("drums", "bass", "piano")}
    rng = np.random.default_rng(SEED)
    timeline: dict[float, dict[str, Any]] = {}

    def add(beat: float, instrument: str, samples: np.ndarray, strength: float, sustain: float = 0) -> None:
        start = round(beat * 60 / BPM * SAMPLE_RATE)
        count = min(len(samples), length - start)
        if count <= 0:
            return
        stems[instrument][start:start + count] += samples[:count]
        item = timeline.setdefault(beat, {"beat": beat, "snappedBeat": beat, "sample": start, "timeSeconds": start / SAMPLE_RATE, "strength": 0.0, "sustainBeats": 0.0, "instruments": []})
        item["instruments"].append(instrument)
        if strength > item["strength"]:
            item.update(strength=strength, layer=instrument)
        item["sustainBeats"] = max(item["sustainBeats"], sustain)

    for half_beat in range(120):
        beat = half_beat / 2
        chorus = 24 <= beat < 40 or 48 <= beat < 58
        if half_beat % 2 == 0:
            t = np.arange(round(0.22 * SAMPLE_RATE)) / SAMPLE_RATE
            # A pitched kick settles from its transient onto the bass root.
            phase = 2 * np.pi * (48 * t + 6.5 * (1 - np.exp(-t * 28)))
            kick = np.sin(phase) * np.exp(-t * 21) * 0.65
            add(beat, "drums", kick, 1.8 if beat % 4 == 0 else 1.2)
            if int(beat) % 2 == 1:
                t = np.arange(round(0.13 * SAMPLE_RATE)) / SAMPLE_RATE
                snare = (rng.normal(0, 1, len(t)) * 0.16 + np.sin(2 * np.pi * 185 * t) * 0.12) * np.exp(-t * 28)
                add(beat, "drums", snare, 1.4)
        if beat >= 8:
            t = np.arange(round(0.035 * SAMPLE_RATE)) / SAMPLE_RATE
            noise = rng.normal(0, 1, len(t))
            hat = (noise - np.concatenate(([0], noise[:-1]))) * np.exp(-t * 125) * (0.035 if chorus else 0.024)
            add(beat, "drums", hat, 0.72 if chorus else 0.5)
        if half_beat % 2 == 0 and beat >= 4:
            root = (55.0, 65.4064, 48.9994, 58.2705)[int(beat // 8) % 4]
            t = np.arange(round(0.31 * SAMPLE_RATE)) / SAMPLE_RATE
            bass = (np.sin(2 * np.pi * root * t) + 0.22 * np.sin(2 * np.pi * root * 2 * t)) * np.minimum(t / 0.004, 1) * np.exp(-t * 11) * 0.25
            add(beat, "bass", bass, 1.0, 0.5)
        if beat >= 16 and half_beat % (1 if chorus else 4) == 0 and beat < 58:
            degree = (0, 7, 12, 3, 10, 7, 3, 12)[half_beat % 8]
            frequency = 220 * 2 ** (degree / 12)
            t = np.arange(round(0.2 * SAMPLE_RATE)) / SAMPLE_RATE
            note = (np.sin(2 * np.pi * frequency * t) + 0.2 * np.sin(2 * np.pi * frequency * 2 * t)) * np.minimum(t / 0.003, 1) * np.exp(-t * 18) * 0.095
            add(beat, "piano", note, 0.88 if chorus else 0.7, 0.25)

    events = []
    for beat, item in sorted(timeline.items()):
        start = item["sample"]
        energies = {name: float(np.sqrt(np.mean(stem[start:min(start + 2048, length)] ** 2))) for name, stem in stems.items()}
        scale = max(energies.values(), default=1.0) or 1.0
        item["stemEnergy"] = {name: round(value / scale, 6) for name, value in energies.items()}
        events.append(item)
    mix = sum(stems.values())
    mix /= max(1.0, float(np.max(np.abs(mix))) / 0.85)
    fade = round(SAMPLE_RATE * 0.3)
    mix[-fade:] *= np.linspace(1, 0, fade)
    stereo = np.column_stack((mix, mix)).astype(np.float32)
    checkpoints = [{"label": label, "beat": beat, "sample": round(beat * 60 / BPM * SAMPLE_RATE), "timeSeconds": beat * 60 / BPM, "source": "synthetic-authored-score"} for label, beat in (("start", 4), ("middle", 30), ("end", 56))]
    analysis = {
        "schemaVersion": 1, "status": "timing_verified", "source": "synthetic-authored-score",
        "sampleRate": SAMPLE_RATE, "durationSeconds": DURATION, "durationSamples": length, "bpm": BPM,
        "gridSource": "synthetic-authored-score",
        "beatGrid": [
            # The score is authored on half beats, exactly 11025 samples apart.
            # Finer decisions interpolate these anchors without accumulated rounding drift.
            {"beat": tick / 2, "sample": round(tick / 2 * 60 / BPM * SAMPLE_RATE), "timeSeconds": round(tick / 2 * 60 / BPM * SAMPLE_RATE) / SAMPLE_RATE}
            for tick in range(round(DURATION * BPM / 60 * 2))
        ],
        "tempoRegions": [{"startBeat": 0, "endBeat": DURATION * BPM / 60, "startSample": 0, "endSample": length, "bpm": BPM}],
        "offsetSeconds": 0.0, "events": events,
        "timingEvidence": {"method": "Every oscillator and drum is placed at round(beat * 60 / bpm * sampleRate)", "authoredSampleClock": True, "trackerAnalysisPerformed": False, "humanListeningVerified": False},
        "clickTrackEvidence": {"source": "authored-score-checkpoints", "checkpoints": checkpoints},
    }
    sections = {"source": "synthetic-authored-score", "sections": [
        {"id": "section-000", "label": "Intro", "startBeat": 0, "endBeat": 8, "intensity": 0.3},
        {"id": "section-001", "label": "Verse", "startBeat": 8, "endBeat": 24, "intensity": 0.5},
        {"id": "section-002", "label": "Chorus", "startBeat": 24, "endBeat": 40, "intensity": 0.82},
        {"id": "section-003", "label": "Verse", "startBeat": 40, "endBeat": 48, "intensity": 0.5},
        {"id": "section-004", "label": "Chorus", "startBeat": 48, "endBeat": 60, "intensity": 0.82},
    ]}
    return stereo, analysis, sections


def build(output: Path = ROOT / "web" / "assets" / "demo") -> dict[str, Any]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # All temporary map files live beneath the selected output, never in a game/cache folder.
    with tempfile.TemporaryDirectory(prefix="demo-build-", dir=output) as temporary:
        folder = Path(temporary)
        reports = folder / "_beatforge"
        reports.mkdir()
        audio, analysis, sections = synthesize_score()
        wav_path = folder / "source.wav"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
        encode_ogg(wav_path, folder / "song.ogg")
        wav_path.unlink()
        controls = normalize_mapping_plan({"brief": "Flowing patterns, lighter verses, denser choruses", "style": "flow", "verseDensity": 0.85, "chorusDensity": 1.15, "candidateCount": 2})
        maps, choreography = generate_all(analysis, sections, SEED, corpus_database=folder / "no-corpus.sqlite3", difficulties=DIFFICULTIES, mapping_plan=controls)
        maps = {difficulty: project_map_timing(chart, analysis) for difficulty, chart in maps.items()}
        planned_sections = project_plan_timing(build_section_plan(analysis, sections, controls), analysis)
        choreography["mappingPlan"] = planned_sections
        for difficulty, chart in maps.items():
            write_json(folder / f"{difficulty}Standard.dat", chart)
        scheme = copy.deepcopy(DAFT_PUNK_COLOR_SCHEME)
        scheme["colorScheme"]["colorSchemeId"] = "BeatForgeSyntheticDemo"
        write_json(folder / "Info.dat", info_dat("Neon Rain", "BeatForge synthetic score", "BeatForge", BPM, color_scheme=scheme, difficulties=DIFFICULTIES))
        write_default_cover(folder / "cover.png", "Neon Rain")
        write_json(reports / "analysis.json", analysis)
        write_json(reports / "beat_grid.json", {"schemaVersion": 1, "status": analysis["status"], "source": analysis["gridSource"], "sampleRate": SAMPLE_RATE, "beats": analysis["beatGrid"], "tempoRegions": analysis["tempoRegions"]})
        write_json(reports / "sections.json", sections)
        write_json(reports / "mapping_plan.json", planned_sections)
        write_json(reports / "choreography_report.json", choreography)
        provenance = {
            "schemaVersion": 1, "source": "synthetic-authored-score", "seed": SEED,
            "engine": "canonical-choreography", "model": None, "mappingPlan": planned_sections,
            "timing": analysis["timingEvidence"], "officialCorpusUsed": False,
            "audio": {"originalSynthesis": True, "sampleRate": SAMPLE_RATE, "samples": len(audio), "durationSeconds": DURATION, "sha256": hashlib.sha256((folder / "song.ogg").read_bytes()).hexdigest()},
            "releaseGate": {"structuralInspection": False, "fullSpeedVrPlaytest": False, "slowVrPlaytest": False, "freshSightRead": False},
            "humanPlaytests": [],
        }
        write_json(reports / "provenance.json", provenance)
        qa = validate_package(folder)
        write_json(reports / "qa_report.json", qa.to_dict())
        if qa.errors:
            raise RuntimeError("Synthetic demo failed canonical validation: " + ", ".join(issue.code for issue in qa.errors))
        provenance["releaseGate"]["structuralInspection"] = True
        write_json(reports / "provenance.json", provenance)
        preview = preview_payload(folder, "Hard")
        preview.update({
            "source": "synthetic-authored-score", "charts": maps, "qa": qa.to_dict(),
            "summary": summarize_map(folder), "provenance": provenance, "timingEvidence": analysis["timingEvidence"],
            "audioUrl": "assets/demo/song.ogg", "downloadUrl": "assets/demo/map.zip", "canRevise": False,
        })
        package_map(folder, output / "map.zip")
        archive_qa = validate_package(output / "map.zip")
        if archive_qa.errors:
            raise RuntimeError("Packaged synthetic demo did not validate")
        shutil.copy2(folder / "song.ogg", output / "song.ogg")
        (output / "preview.json").write_text(json.dumps(preview, separators=(",", ":")) + "\n", encoding="utf-8")
    return preview


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "web" / "assets" / "demo")
    args = parser.parse_args()
    result = build(args.output)
    print(json.dumps({"output": str(args.output.resolve()), "duration": result["duration"], "summary": result["summary"], "qaErrors": len(result["qa"]["errors"])}))
