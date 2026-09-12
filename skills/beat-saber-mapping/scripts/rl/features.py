"""One sample-aligned feature contract for RL training, evaluation, and generation.

Consumes the existing analyzer artifacts. It never estimates or verifies timing.
Missing separated stems are zeros with availability recorded, never mix copies.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

FEATURE_SCHEMA = "beatforge-rl-sample-grid-v2"
STEMS = ("drums", "bass", "guitar", "piano", "vocals", "other")


@dataclass
class AnalysisFeatures:
    audio_features: dict[str, Any]
    beat_grid: list[float]
    bpm: float
    provenance: dict[str, Any]
    analysis: dict[str, Any]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unit(values: np.ndarray) -> np.ndarray:
    values = np.maximum(values, 0.0)
    scale = max(1.0, float(np.max(values))) if values.size else 1.0
    return np.clip(values / scale, 0.0, 1.0)


def build_analysis_features(
    analysis: dict[str, Any],
    grid_document: dict[str, Any],
    sections_document: dict[str, Any],
    frames: dict[str, Any],
    *,
    require_verified: bool = True,
) -> AnalysisFeatures:
    """Project frames and onset stem annotations onto actual quarter-beat samples."""
    if not all(isinstance(document, dict) for document in (analysis, grid_document, sections_document, frames)):
        raise ValueError("RL analysis artifacts must contain objects")
    status = str(analysis.get("status", "unknown"))
    if require_verified and status != "timing_verified":
        raise ValueError(f"RL requires verified analysis; timing status is {status}")
    if grid_document.get("status", status) != status:
        raise ValueError("Analysis and beat grid timing statuses disagree")
    sample_rate = int(analysis["sampleRate"])
    if float(analysis["sampleRate"]) != sample_rate or float(grid_document.get("sampleRate", sample_rate)) != sample_rate:
        raise ValueError("Analysis sample rate must be an integer")
    if sample_rate != 44100 or int(grid_document.get("sampleRate", sample_rate)) != sample_rate:
        raise ValueError("RL analysis must use the canonical 44100 Hz sample clock")
    duration = int(analysis["durationSamples"])
    if float(analysis["durationSamples"]) != duration:
        raise ValueError("Analysis durationSamples must be an integer")
    bpm = float(analysis["bpm"])
    if duration <= 0 or not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("Analysis has invalid duration or BPM")
    rows = grid_document.get("beats", [])
    if len(rows) < 2 or not all(isinstance(row, dict) for row in rows):
        raise ValueError("beat_grid.json must contain at least two beat/sample rows")
    beats = np.asarray([row["beat"] for row in rows], dtype=float)
    raw_samples = np.asarray([row["sample"] for row in rows], dtype=float)
    if (not np.isfinite(beats).all() or not np.isfinite(raw_samples).all()
            or np.any(raw_samples != np.round(raw_samples))
            or np.any(np.diff(beats) <= 0) or np.any(np.diff(raw_samples) <= 0)):
        raise ValueError("Beat/sample rows must be finite, integer-sampled, and strictly increasing")
    if raw_samples[0] < 0 or raw_samples[-1] >= duration:
        raise ValueError("Beat samples fall outside the analyzed audio")
    if "beatGrid" in analysis:
        recorded = [(row["beat"], row["sample"]) for row in analysis["beatGrid"]]
        if recorded != [(row["beat"], row["sample"]) for row in rows]:
            raise ValueError("Analysis and beat_grid.json contain different adopted timing grids")

    # The analyzer may provide finer subdivisions. Select the quarter-beat domain
    # through interpolation in the adopted sample clock, including pickups.
    first_tick = math.ceil(beats[0] * 4 - 1e-7)
    last_tick = math.floor(beats[-1] * 4 + 1e-7)
    steps = np.arange(first_tick, last_tick + 1, dtype=float) / 4.0
    if not len(steps):
        raise ValueError("Beat grid contains no quarter-beat decision steps")
    samples = np.rint(np.interp(steps, beats, raw_samples)).astype(np.int64)
    if np.any(np.diff(samples) <= 0):
        raise ValueError("Quarter-beat sample positions must increase")
    times = samples / sample_rate

    def frame_values(key: str) -> np.ndarray:
        values = np.asarray(frames.get(key, []), dtype=float)
        if values.ndim != 1:
            raise ValueError(f"Audio feature {key} must be a one-dimensional envelope")
        if not np.isfinite(values).all():
            raise ValueError(f"Non-finite audio feature {key}")
        return values

    def align(key: str) -> list[float]:
        values = frame_values(key)
        if not len(values):
            return [0.0] * len(steps)
        if "samples" in frames:
            positions = np.asarray(frames["samples"], dtype=float).reshape(-1)
            if np.any(positions != np.round(positions)):
                raise ValueError("Audio frame sample positions must be integers")
        elif "times" in frames or "timeSeconds" in frames:
            positions = np.asarray(frames.get("times", frames.get("timeSeconds")), dtype=float).reshape(-1) * sample_rate
        else:
            hops = np.asarray(frames.get("hopSamples", frames.get("hop", 0))).reshape(-1)
            if len(hops) != 1:
                raise ValueError("Audio feature frames require a single hopSamples value")
            hop = float(hops[0])
            if not math.isfinite(hop) or hop <= 0 or hop != round(hop):
                raise ValueError("Audio feature frames require sample positions, times, or integer hopSamples")
            positions = np.arange(len(values)) * hop
        if len(positions) != len(values) or not np.isfinite(positions).all() or np.any(np.diff(positions) <= 0):
            raise ValueError(f"Invalid frame clock for {key}")
        return np.interp(samples, positions, _unit(values), left=0.0, right=0.0).tolist()

    onset_key = next((key for key in ("combinedOnset", "combined", "onsets") if key in frames), None)
    if onset_key is None:
        raise ValueError("Audio feature artifact is missing its onset envelope")
    if not len(frame_values(onset_key)):
        raise ValueError("Audio feature artifact contains an empty onset envelope")
    onsets = align(onset_key)
    flux_available = "flux" in frames and bool(len(frame_values("flux")))
    flux = align("flux") if flux_available else [0.0] * len(steps)

    stems = {stem: [0.0] * len(steps) for stem in STEMS}
    available = {stem: False for stem in STEMS}
    stem_sources: dict[str, str] = {}
    sustains = [0.0] * len(steps)
    # Demucs is persisted as event annotations after temporary WAVs are removed.
    # Assign an event to its nearest step only; never extend it across silence.
    for event in analysis.get("events", []):
        energies = event.get("stemEnergy", {})
        sample_value = float(event["sample"])
        if not math.isfinite(sample_value) or sample_value != round(sample_value) or not 0 <= sample_value < duration:
            raise ValueError("Analysis onset samples must be integers within the audio")
        sample = int(sample_value)
        right = min(int(np.searchsorted(samples, sample)), len(samples) - 1)
        left = max(0, right - 1)
        index = left if abs(int(samples[left]) - sample) <= abs(int(samples[right]) - sample) else right
        sustain = float(event.get("sustainBeats", 0.0))
        if not math.isfinite(sustain) or sustain < 0:
            raise ValueError("Analysis sustain duration must be finite and nonnegative")
        sustains[index] = max(sustains[index], sustain)
        for stem in STEMS:
            if stem not in energies:
                continue
            value = float(energies[stem])
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid separated {stem} energy")
            available[stem] = True
            stem_sources[stem] = "analysis.events.stemEnergy"
            stems[stem][index] = max(stems[stem][index], min(value, 1.0))

    labels = ["unknown"] * len(steps)
    for section in sections_document.get("sections", []):
        # External segment beat fields may have used a constant BPM conversion.
        # Prefer sample/second boundaries to retain offset and tempo changes.
        if "startSample" in section and "endSample" in section:
            selected = (samples >= int(section["startSample"])) & (samples < int(section["endSample"]))
        elif "startSeconds" in section and "endSeconds" in section:
            selected = (times >= float(section["startSeconds"])) & (times < float(section["endSeconds"]))
        elif "startBeat" in section and "endBeat" in section:
            selected = (steps >= float(section["startBeat"])) & (steps < float(section["endBeat"]))
        else:
            raise ValueError("Section requires a start/end interval")
        label = str(section.get("label", section.get("type", "unknown")))
        for index in np.flatnonzero(selected):
            labels[int(index)] = label

    intervals = np.searchsorted(beats, steps, side="right") - 1
    intervals = np.clip(intervals, 0, len(beats) - 2)
    bpms = 60.0 * sample_rate * np.diff(beats)[intervals] / np.diff(raw_samples)[intervals]
    provenance = {
        "featureSchema": FEATURE_SCHEMA, "timingStatus": status,
        "gridSource": grid_document.get("source", analysis.get("gridSource", "unknown")),
        "sourceSha256": analysis.get("sourceSha256"), "sampleRate": sample_rate,
        "durationSamples": duration, "decisionSteps": len(steps),
        "firstSample": int(samples[0]), "lastSample": int(samples[-1]),
        "stemAvailability": available, "stemSources": stem_sources,
        "fluxAvailable": flux_available,
        "sourceAudioVerified": False,
        "sectionSource": sections_document.get("source", "analysis"),
    }
    audio_features = {
        "onsets": onsets, "flux": flux, "stems": stems, "sections": labels,
        "sustainBeats": sustains,
        "samplePositions": samples.tolist(), "timesSeconds": times.tolist(),
        "stepBpms": bpms.tolist(), "stemAvailability": available,
        "provenance": provenance,
    }
    return AnalysisFeatures(audio_features, steps.tolist(), bpm, provenance, {**analysis, "beatGrid": rows})


def load_analysis_features(analysis_dir: Path, *, require_verified: bool = True) -> AnalysisFeatures:
    analysis_dir = Path(analysis_dir)
    names = ("analysis.json", "beat_grid.json", "sections.json", "audio_features.npz")
    for name in names:
        if not (analysis_dir / name).is_file():
            raise FileNotFoundError(f"Missing RL analysis artifact: {analysis_dir / name}. Run analyze_audio.py first.")
    documents = [json.loads((analysis_dir / name).read_text(encoding="utf-8")) for name in names[:3]]
    with np.load(analysis_dir / names[3], allow_pickle=False) as archive:
        frames = {key: archive[key] for key in archive.files}
    result = build_analysis_features(*documents, frames, require_verified=require_verified)
    result.provenance["artifactSha256"] = {name: file_sha256(analysis_dir / name) for name in names}
    return result
