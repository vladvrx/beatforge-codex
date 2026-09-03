#!/usr/bin/env python3
"""Shared primitives for the premium Beat Saber mapping pipeline.

The module deliberately keeps timing in integer PCM samples. Beat numbers are
derived views, never the source of truth.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

_managed_base = os.environ.get("LOCALAPPDATA")
_managed_dependencies = (
    Path(_managed_base) / "Codex" / "beat-saber-mapping" / "python"
    if _managed_base
    else Path.home() / ".cache" / "beat-saber-mapping" / "python"
)
_managed_readable = False
if _managed_dependencies.is_dir():
    try:
        with (_managed_dependencies / "numpy" / "__init__.py").open("rb") as _probe:
            _probe.read(1)
        _managed_readable = True
    except OSError:
        # Sandboxed callers can sometimes see the cache directory but cannot
        # read its files. In that case, use the active environment instead.
        _managed_readable = False
if _managed_readable and str(_managed_dependencies) not in sys.path:
    sys.path.append(str(_managed_dependencies))

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - dependency failure path
    raise SystemExit("beat-saber-mapping requires numpy; run scripts/bootstrap.py") from exc


TOOL_VERSION = "2.0.0"
SAMPLE_RATE = 44_100
GRID_STEP = 0.25
DIFFICULTIES = ("Easy", "Normal", "Hard", "Expert", "ExpertPlus")
DIFFICULTY_ALIASES = {
    "easy": "Easy",
    "normal": "Normal",
    "hard": "Hard",
    "expert": "Expert",
    "expertplus": "ExpertPlus",
    "expert+": "ExpertPlus",
}


def parse_difficulties(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return DIFFICULTIES
    selected: list[str] = []
    for raw in values:
        for part in str(raw).replace(";", ",").split(","):
            token = part.strip()
            if not token:
                continue
            name = DIFFICULTY_ALIASES.get(token.casefold())
            if name is None:
                raise ValueError(f"Unknown difficulty {token!r}. Choose from: {', '.join(DIFFICULTIES)}")
            if name not in selected:
                selected.append(name)
    if not selected:
        raise ValueError("Select at least one difficulty")
    return tuple(name for name in DIFFICULTIES if name in selected)
INTERNAL_LEVEL_IDS = {"artteamtest", "metronome", "performancetest"}


@dataclass(frozen=True)
class Anchor:
    beat: float
    sample: int
    kind: str = "beat"


@dataclass
class AudioBuffer:
    samples: np.ndarray
    sample_rate: int
    channels: int
    duration_samples: int
    source_sha256: str


@dataclass
class HandState:
    color: int
    x: int
    y: int
    direction: int
    parity: int
    occupied_until: float = -math.inf
    recovery_until: float = -math.inf
    momentum_x: float = 0.0
    momentum_y: float = 0.0
    body_lean: float = 0.0
    exit_x: int | None = None
    exit_y: int | None = None
    exit_direction: int | None = None


MOTION_FINDING_FIELDS = (
    "difficulty",
    "previousBeat",
    "currentBeat",
    "color",
    "previousPosition",
    "currentPosition",
    "previousDirection",
    "currentDirection",
    "availableRecoveryBeats",
    "requiredRecoveryBeats",
    "failedConstraint",
)


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    file: str | None = None
    beat: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    status: str = "structurally_valid"
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add(self, severity: str, code: str, message: str, **location: Any) -> None:
        file = location.pop("file", None)
        beat = location.pop("beat", None)
        issue = ValidationIssue(severity, code, message, file, beat, location)
        (self.errors if severity == "error" else self.warnings).append(issue)
        if severity == "error":
            self.status = "invalid"

    def add_motion(
        self,
        code: str,
        message: str,
        *,
        file: str,
        beat: float | None,
        **fields: Any,
    ) -> None:
        missing = [key for key in MOTION_FINDING_FIELDS if key not in fields]
        if missing:
            raise ValueError(f"{code} is missing required motion fields: {', '.join(missing)}")
        self.add("error", code, message, file=file, beat=beat, **fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "errors": [asdict(x) for x in self.errors],
            "warnings": [asdict(x) for x in self.warnings],
            "metrics": self.metrics,
        }


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_bytes(raw: bytes, label: str = "data") -> Any:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{label}: invalid JSON or gzip data: {exc}") from exc


def load_json(path: Path) -> Any:
    return load_json_bytes(path.read_bytes(), str(path))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def find_ffmpeg() -> str | None:
    configured = os.environ.get("BEATFORGE_FFMPEG")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def ffmpeg_clock_to_seconds(value: str) -> float | None:
    text = str(value).strip()
    if not text or text == "N/A":
        return None
    if text.isdigit():
        return int(text) / 1_000_000.0
    match = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        try:
            return float(text)
        except ValueError:
            return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600.0 + minutes * 60.0 + seconds


def _run_ffmpeg(command: list[str], *, label: str, failure: str) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    duration: float | None = None
    last_percent = -1.0
    stderr_chunks: list[str] = []

    def consume_stderr() -> None:
        nonlocal duration
        assert process.stderr is not None
        for raw in process.stderr:
            stderr_chunks.append(raw)
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", raw)
            if match and duration is None:
                duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))

    reader = threading.Thread(target=consume_stderr, daemon=True)
    reader.start()
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"out_time", "out_time_ms", "out_time_us"}:
            if key == "out_time":
                seconds = ffmpeg_clock_to_seconds(value)
            elif key == "out_time_us":
                seconds = int(value) / 1_000_000.0 if value.isdigit() else None
            else:
                seconds = int(value) / 1_000_000.0 if value.isdigit() else ffmpeg_clock_to_seconds(value)
            if seconds is None or not duration or duration <= 0:
                continue
            percent = min(100.0, max(0.0, 100.0 * seconds / duration))
            if percent - last_percent < 1 and percent < 100:
                continue
            last_percent = percent
            emit_progress("decode", f"{label} {percent:.0f}%", percent)
        if key == "progress" and value.strip() == "end":
            emit_progress("decode", f"{label} 100%", 100.0)
            last_percent = 100.0
    returncode = process.wait()
    reader.join()
    if returncode:
        raise RuntimeError(f"{failure}: {''.join(stderr_chunks).strip() or returncode}")
    if last_percent < 100:
        emit_progress("decode", f"{label} 100%", 100.0)


def decode_to_wav(source: Path, destination: Path, sample_rate: int = SAMPLE_RATE) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg is unavailable; run scripts/bootstrap.py")
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-progress",
        "pipe:1",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "2",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    _run_ffmpeg(command, label="decoded", failure=f"FFmpeg could not decode {source}")


def encode_ogg(source: Path, destination: Path) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg is unavailable; run scripts/bootstrap.py")
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-progress",
        "pipe:1",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-c:a",
        "libvorbis",
        "-q:a",
        "6",
        str(destination),
    ]
    _run_ffmpeg(command, label="encoded", failure=f"FFmpeg could not encode {destination}")


def _read_wave(path: Path) -> tuple[np.ndarray, int, int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        rate = source.getframerate()
        width = source.getsampwidth()
        frames = source.readframes(source.getnframes())
    if width == 1:
        values = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        values = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        values = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported PCM width: {width}")
    values = values.reshape(-1, channels)
    return values, rate, channels


def load_audio(path: Path, sample_rate: int = SAMPLE_RATE) -> AudioBuffer:
    path = path.resolve()
    try:
        import soundfile as sf

        data, rate = sf.read(path, dtype="float32", always_2d=True)
        channels = int(data.shape[1])
        if rate != sample_rate:
            raise RuntimeError("resample")
    except Exception:
        if path.suffix.lower() == ".wav":
            data, rate, channels = _read_wave(path)
            if rate == sample_rate:
                mono = data.mean(axis=1, dtype=np.float32)
                return AudioBuffer(mono, rate, channels, len(mono), sha256_file(path))
        with tempfile.TemporaryDirectory(prefix="beatforge-audio-") as temp:
            decoded = Path(temp) / "decoded.wav"
            decode_to_wav(path, decoded, sample_rate)
            data, rate, channels = _read_wave(decoded)
    mono = data.mean(axis=1, dtype=np.float32)
    return AudioBuffer(mono, int(rate), channels, int(len(mono)), sha256_file(path))


def robust_normalize(values: np.ndarray) -> np.ndarray:
    if not len(values):
        return values.astype(np.float32)
    median = float(np.median(values))
    deviation = float(np.median(np.abs(values - median))) * 1.4826
    if deviation < 1e-9:
        deviation = float(np.std(values)) or 1.0
    return np.clip((values - median) / deviation, 0.0, 12.0).astype(np.float32)


def frame_features(samples: np.ndarray, frame_size: int = 2048, hop: int = 256) -> dict[str, np.ndarray]:
    if len(samples) < frame_size:
        samples = np.pad(samples, (0, frame_size - len(samples)))
    count = 1 + (len(samples) - frame_size) // hop
    window = np.hanning(frame_size).astype(np.float32)
    rms = np.empty(count, dtype=np.float32)
    flux = np.empty(count, dtype=np.float32)
    previous_spectrum: np.ndarray | None = None
    block_frames = 1024
    for first in range(0, count, block_frames):
        block_count = min(block_frames, count - first)
        sample_start = first * hop
        source = samples[sample_start : sample_start + (block_count - 1) * hop + frame_size]
        shape = (block_count, frame_size)
        strides = (source.strides[0] * hop, source.strides[0])
        frames = np.lib.stride_tricks.as_strided(source, shape=shape, strides=strides)
        windowed = frames * window
        rms[first : first + block_count] = np.sqrt(np.mean(windowed * windowed, axis=1) + 1e-12)
        spectrum = np.abs(np.fft.rfft(windowed, axis=1)).astype(np.float32)
        if previous_spectrum is None:
            difference = np.diff(spectrum, axis=0, prepend=spectrum[:1])
        else:
            difference = np.diff(spectrum, axis=0, prepend=previous_spectrum[None, :])
        flux[first : first + block_count] = np.maximum(difference, 0.0).sum(axis=1)
        previous_spectrum = spectrum[-1].copy()
    energy_delta = np.maximum(np.diff(rms, prepend=rms[:1]), 0.0)
    return {
        "rms": rms.astype(np.float32),
        "flux": robust_normalize(flux),
        "energy_delta": robust_normalize(energy_delta),
        "combined": robust_normalize(0.75 * robust_normalize(flux) + 0.25 * robust_normalize(energy_delta)),
        "hop": np.asarray([hop], dtype=np.int32),
    }


def local_peaks(values: np.ndarray, minimum: float = 1.0, distance: int = 1) -> np.ndarray:
    if len(values) < 3:
        return np.asarray([], dtype=np.int64)
    candidate = np.flatnonzero((values[1:-1] > values[:-2]) & (values[1:-1] >= values[2:])) + 1
    candidate = candidate[values[candidate] >= minimum]
    if distance <= 1 or len(candidate) < 2:
        return candidate
    chosen: list[int] = []
    for index in candidate[np.argsort(values[candidate])[::-1]]:
        if all(abs(int(index) - other) >= distance for other in chosen):
            chosen.append(int(index))
    return np.asarray(sorted(chosen), dtype=np.int64)


def tempo_hypotheses(envelope: np.ndarray, sample_rate: int, hop: int) -> list[dict[str, float]]:
    centered = envelope.astype(np.float64) - float(np.mean(envelope))
    if not np.any(centered):
        return []
    min_lag = max(1, int(round(60.0 * sample_rate / (200.0 * hop))))
    max_lag = min(len(centered) - 1, int(round(60.0 * sample_rate / (60.0 * hop))))
    if max_lag <= min_lag:
        return []
    window = np.asarray(
        [float(np.dot(centered[:-lag], centered[lag:])) for lag in range(min_lag, max_lag + 1)],
        dtype=np.float64,
    )
    peaks = local_peaks(robust_normalize(window), minimum=0.1, distance=2)
    if not len(peaks):
        peaks = np.asarray([int(np.argmax(window))])
    ranked = peaks[np.argsort(window[peaks])[::-1]][:8]
    maximum = float(max(window[int(x)] for x in ranked)) or 1.0
    result: list[dict[str, float]] = []
    seen: list[float] = []
    for relative in ranked:
        lag = int(relative) + min_lag
        bpm = 60.0 * sample_rate / (hop * lag)
        if any(abs(bpm - old) < 0.25 for old in seen):
            continue
        seen.append(bpm)
        result.append({"bpm": float(bpm), "score": float(window[int(relative)] / maximum)})
    return result


def best_phase(envelope: np.ndarray, bpm: float, sample_rate: int, hop: int) -> tuple[int, float]:
    period = max(1, int(round(60.0 * sample_rate / (bpm * hop))))
    scores = np.asarray([float(envelope[offset::period].sum()) for offset in range(period)])
    offset = int(np.argmax(scores))
    denominator = float(np.mean(scores) + 1e-9)
    return offset * hop, float(scores[offset] / denominator)


def _candidate_track(envelope: np.ndarray, sample_rate: int, hop: int) -> dict[str, Any] | None:
    hypotheses = tempo_hypotheses(envelope, sample_rate, hop)
    if not hypotheses:
        return None
    best = hypotheses[0]
    phase_sample, phase_score = best_phase(envelope, best["bpm"], sample_rate, hop)
    return {
        "bpm": best["bpm"],
        "score": best["score"],
        "phaseSample": phase_sample,
        "phaseScore": phase_score,
        "hypotheses": hypotheses,
    }


def load_anchors(path: Path | None, sample_rate: int) -> list[Anchor]:
    if not path:
        return []
    payload = load_json(path)
    entries = payload.get("anchors", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("anchors must be an array or an object containing an anchors array")
    anchors: list[Anchor] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict) or "beat" not in item:
            raise ValueError(f"anchor {index} must contain beat")
        if "sample" in item:
            sample = int(item["sample"])
        elif "timeSeconds" in item:
            sample = int(round(float(item["timeSeconds"]) * sample_rate))
        else:
            raise ValueError(f"anchor {index} must contain sample or timeSeconds")
        anchors.append(Anchor(float(item["beat"]), sample, str(item.get("kind", "beat"))))
    anchors.sort(key=lambda x: x.beat)
    for left, right in zip(anchors, anchors[1:]):
        if right.beat <= left.beat or right.sample <= left.sample:
            raise ValueError("anchors must increase strictly in beat and sample")
    return anchors


def piecewise_sample(beat: float, anchors: Sequence[Anchor]) -> float:
    if len(anchors) < 2:
        raise ValueError("at least two anchors are required")
    if beat <= anchors[0].beat:
        left, right = anchors[0], anchors[1]
    elif beat >= anchors[-1].beat:
        left, right = anchors[-2], anchors[-1]
    else:
        left, right = anchors[0], anchors[1]
        for candidate_left, candidate_right in zip(anchors, anchors[1:]):
            if candidate_left.beat <= beat <= candidate_right.beat:
                left, right = candidate_left, candidate_right
                break
    ratio = (beat - left.beat) / (right.beat - left.beat)
    return left.sample + ratio * (right.sample - left.sample)


def build_grid_from_anchors(anchors: Sequence[Anchor], duration_samples: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(anchors) < 2:
        raise ValueError("two or more anchors are required to fit a beat grid")
    first_slope = (anchors[1].sample - anchors[0].sample) / (anchors[1].beat - anchors[0].beat)
    estimated_first = anchors[0].beat - anchors[0].sample / first_slope
    start_beat = math.floor(estimated_first / GRID_STEP) * GRID_STEP
    grid: list[dict[str, Any]] = []
    beat = start_beat
    while True:
        sample = int(round(piecewise_sample(beat, anchors)))
        if sample >= duration_samples:
            break
        if sample >= 0:
            grid.append({"beat": round(beat, 6), "sample": sample, "timeSeconds": sample / SAMPLE_RATE})
        beat += GRID_STEP
    regions: list[dict[str, Any]] = []
    for left, right in zip(anchors, anchors[1:]):
        samples_per_beat = (right.sample - left.sample) / (right.beat - left.beat)
        regions.append(
            {
                "startBeat": left.beat,
                "endBeat": right.beat,
                "startSample": left.sample,
                "endSample": right.sample,
                "bpm": 60.0 * SAMPLE_RATE / samples_per_beat,
            }
        )
    return grid, regions


def build_constant_grid(bpm: float, offset_sample: int, duration_samples: int) -> list[dict[str, Any]]:
    samples_per_step = SAMPLE_RATE * 60.0 / bpm * GRID_STEP
    beat = 0.0
    sample = float(offset_sample)
    grid: list[dict[str, Any]] = []
    while sample < duration_samples:
        if sample >= 0:
            rounded = int(round(sample))
            grid.append({"beat": round(beat, 6), "sample": rounded, "timeSeconds": rounded / SAMPLE_RATE})
        beat += GRID_STEP
        sample += samples_per_step
    return grid


def sample_to_beat(sample: int, grid: Sequence[dict[str, Any]]) -> float:
    if len(grid) < 2:
        return 0.0
    samples = np.asarray([entry["sample"] for entry in grid], dtype=np.int64)
    index = int(np.searchsorted(samples, sample))
    index = min(max(index, 1), len(grid) - 1)
    left, right = grid[index - 1], grid[index]
    denominator = right["sample"] - left["sample"]
    if denominator <= 0:
        return float(left["beat"])
    ratio = (sample - left["sample"]) / denominator
    return float(left["beat"] + ratio * (right["beat"] - left["beat"]))


def consensus_metrics(tracks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    valid = [track for track in tracks if track]
    if len(valid) < 2:
        return {"trackerCount": len(valid), "bpmSpreadPercent": math.inf, "phaseSpreadMs": math.inf}
    bpms = np.asarray([track["bpm"] for track in valid], dtype=np.float64)
    phases = np.asarray([track["phaseSample"] for track in valid], dtype=np.float64)
    return {
        "trackerCount": len(valid),
        "bpmMedian": float(np.median(bpms)),
        "bpmSpreadPercent": float((np.max(bpms) - np.min(bpms)) / np.median(bpms) * 100.0),
        "phaseSpreadMs": float((np.max(phases) - np.min(phases)) / SAMPLE_RATE * 1000.0),
    }


def propose_and_verify_anchors(
    audio: AudioBuffer,
    events: Sequence[dict[str, Any]],
    bpm: float,
    *,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[list[Anchor], dict[str, Any]] | None:
    """Propose piecewise anchors from local strong onsets and verify against 10/20 ms gates.

    Strictly preserves the 10 ms median / 20 ms p95 gates. Returns None if candidate fails.
    """
    strong = [ev for ev in events if float(ev.get("strength", 0.0)) >= 1.5]
    if len(strong) < 3:
        return None
    total_samples = audio.duration_samples
    proposals: list[Anchor] = []
    for fraction, label in ((0.03, "opening-downbeat"), (0.50, "middle-downbeat"), (0.97, "ending-downbeat")):
        target_sample = int(total_samples * fraction)
        nearest = min(strong, key=lambda ev: abs(int(ev["sample"]) - target_sample))
        sample = int(nearest["sample"])
        beat = round(float(nearest.get("snappedBeat", nearest.get("beat", 0.0))))
        if not proposals or (beat > proposals[-1].beat and sample > proposals[-1].sample):
            proposals.append(Anchor(float(beat), sample, label))
    if len(proposals) < 2:
        return None
    try:
        cand_grid, _regions = build_grid_from_anchors(proposals, total_samples)
    except ValueError:
        return None
    quarter_samples = np.asarray([entry["sample"] for entry in cand_grid], dtype=np.int64)
    residuals_ms: list[float] = []
    if len(quarter_samples):
        for ev in strong:
            sample = int(ev["sample"])
            position = int(np.searchsorted(quarter_samples, sample))
            neighbors = quarter_samples[max(0, position - 1) : min(len(quarter_samples), position + 1)]
            if len(neighbors):
                residuals_ms.append(float(np.min(np.abs(neighbors - sample)) / sample_rate * 1000.0))
    if not residuals_ms:
        return None
    median_ms = float(np.median(residuals_ms))
    p95_ms = float(np.percentile(residuals_ms, 95))
    metrics = {
        "medianMs": round(median_ms, 4),
        "p95Ms": round(p95_ms, 4),
        "sampleCount": len(residuals_ms),
    }
    if median_ms <= 10.0 and p95_ms <= 20.0:
        return proposals, metrics
    return None


def analyze_audio_buffer(
    audio: AudioBuffer,
    anchors: Sequence[Anchor] = (),
    explicit_bpm: float | None = None,
    explicit_offset_seconds: float | None = None,
) -> dict[str, Any]:
    features = frame_features(audio.samples)
    hop = int(features["hop"][0])
    tracks = [
        _candidate_track(features["flux"], audio.sample_rate, hop),
        _candidate_track(features["energy_delta"], audio.sample_rate, hop),
        _candidate_track(features["combined"], audio.sample_rate, hop),
    ]
    tracks = [track for track in tracks if track]
    consensus = consensus_metrics(tracks)
    anchor_source = "none"
    tempo_regions: list[dict[str, Any]] = []
    if len(anchors) >= 2:
        grid, tempo_regions = build_grid_from_anchors(anchors, audio.duration_samples)
        bpm = float(np.median([region["bpm"] for region in tempo_regions]))
        anchor_source = "confirmed"
    else:
        if explicit_bpm is not None:
            bpm = float(explicit_bpm)
        elif tracks:
            bpm = float(np.median([track["bpm"] for track in tracks]))
        else:
            bpm = 120.0
        if explicit_offset_seconds is not None:
            offset_sample = int(round(explicit_offset_seconds * audio.sample_rate))
            anchor_source = "explicit-bpm-offset"
        elif tracks:
            offset_sample = int(np.median([track["phaseSample"] for track in tracks]))
            anchor_source = "model-consensus"
        else:
            offset_sample = 0
        grid = build_constant_grid(bpm, offset_sample, audio.duration_samples)
        tempo_regions = [
            {
                "startBeat": 0.0,
                "endBeat": grid[-1]["beat"] if grid else 0.0,
                "startSample": offset_sample,
                "endSample": audio.duration_samples,
                "bpm": bpm,
            }
        ]

    hop_distance = max(1, int(0.055 * audio.sample_rate / hop))
    peaks = local_peaks(features["combined"], minimum=1.25, distance=hop_distance)
    opening_frames = max(1, int(10.0 * audio.sample_rate / hop))
    early_peaks = local_peaks(features["combined"][:opening_frames], minimum=0.35, distance=hop_distance)
    if len(early_peaks):
        peaks = np.unique(np.concatenate([peaks, early_peaks]))
    onset_samples = (peaks * hop).astype(np.int64)
    onset_strengths = features["combined"][peaks] if len(peaks) else np.asarray([], dtype=np.float32)
    events: list[dict[str, Any]] = []
    for index, sample in enumerate(onset_samples):
        beat = sample_to_beat(int(sample), grid)
        snapped = round(beat * 8.0) / 8.0
        next_sample = int(onset_samples[index + 1]) if index + 1 < len(onset_samples) else audio.duration_samples
        gap_beats = max(0.0, sample_to_beat(next_sample, grid) - beat)
        sustain = min(4.0, max(0.0, gap_beats - 0.25)) if gap_beats >= 1.0 else 0.0
        events.append(
            {
                "sample": int(sample),
                "timeSeconds": int(sample) / audio.sample_rate,
                "beat": round(beat, 6),
                "snappedBeat": round(snapped, 6),
                "strength": float(onset_strengths[index]),
                "sustainBeats": round(sustain, 3),
            }
        )

    residuals_ms: list[float] = []
    quarter_samples = np.asarray([entry["sample"] for entry in grid], dtype=np.int64)
    if len(quarter_samples):
        for sample in onset_samples[onset_strengths >= 2.0]:
            position = int(np.searchsorted(quarter_samples, sample))
            neighbors = quarter_samples[max(0, position - 1) : min(len(quarter_samples), position + 1)]
            if len(neighbors):
                residuals_ms.append(float(np.min(np.abs(neighbors - sample)) / audio.sample_rate * 1000.0))
    median_residual = float(np.median(residuals_ms)) if residuals_ms else math.inf
    p95_residual = float(np.percentile(residuals_ms, 95)) if residuals_ms else math.inf

    confirmed = anchor_source in {"confirmed", "explicit-bpm-offset"}
    automatic_pass = (
        consensus.get("trackerCount", 0) >= 3
        and consensus.get("bpmSpreadPercent", math.inf) <= 0.05
        and consensus.get("phaseSpreadMs", math.inf) <= 20.0
        and median_residual <= 10.0
        and p95_residual <= 20.0
    )
    status = "timing_verified" if confirmed or automatic_pass else "needs_anchors"
    anchor_suggestions: list[dict[str, Any]] = []
    if status == "needs_anchors":
        local_proposal = propose_and_verify_anchors(audio, events, bpm, sample_rate=audio.sample_rate)
        for fraction, label in ((0.03, "opening downbeat"), (0.5, "middle downbeat"), (0.97, "ending downbeat")):
            sample = int(audio.duration_samples * fraction)
            beat = sample_to_beat(sample, grid) if grid else 0.0
            anchor_suggestions.append(
                {
                    "nearSample": sample,
                    "nearTimeSeconds": sample / audio.sample_rate,
                    "suggestedBeat": round(float(beat), 3),
                    "purpose": label,
                    "unconfirmed": True,
                    "verifiedCandidate": local_proposal is not None,
                }
            )

    return {
        "schemaVersion": 1,
        "toolVersion": TOOL_VERSION,
        "status": status,
        "sampleRate": audio.sample_rate,
        "durationSamples": audio.duration_samples,
        "durationSeconds": audio.duration_samples / audio.sample_rate,
        "sourceSha256": audio.source_sha256,
        "bpm": bpm,
        "gridSource": anchor_source,
        "tempoRegions": tempo_regions,
        "beatGrid": grid,
        "events": events,
        "consensus": consensus,
        "residuals": {
            "medianMs": median_residual if math.isfinite(median_residual) else None,
            "p95Ms": p95_residual if math.isfinite(p95_residual) else None,
            "sampleCount": len(residuals_ms),
        },
        "trackers": tracks,
        "anchors": [asdict(anchor) for anchor in anchors],
        "anchorSuggestions": anchor_suggestions,
        "clickTrackEvidence": click_track_evidence(grid, audio.duration_samples, audio.sample_rate),
        "featureFrames": {
            "hopSamples": hop,
            "rms": features["rms"].astype(float).tolist(),
            "combinedOnset": features["combined"].astype(float).tolist(),
        },
    }


def section_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    grid = analysis.get("beatGrid", [])
    frames = analysis.get("featureFrames", {})
    rms = np.asarray(frames.get("rms", []), dtype=np.float64)
    hop = int(frames.get("hopSamples", 256))
    if not grid or not len(rms):
        return {"schemaVersion": 1, "sections": []}
    max_beat = float(grid[-1]["beat"])
    boundaries = list(np.arange(0.0, max_beat + 16.0, 16.0))
    sections: list[dict[str, Any]] = []
    energies: list[float] = []
    for start, end in zip(boundaries, boundaries[1:]):
        start_sample = int(round(np.interp(start, [x["beat"] for x in grid], [x["sample"] for x in grid])))
        end_sample = int(round(np.interp(end, [x["beat"] for x in grid], [x["sample"] for x in grid])))
        left = max(0, start_sample // hop)
        right = min(len(rms), max(left + 1, end_sample // hop))
        energies.append(float(np.mean(rms[left:right])))
    normalized = np.asarray(energies)
    if len(normalized):
        low, high = float(np.min(normalized)), float(np.max(normalized))
        normalized = (normalized - low) / (high - low + 1e-9)
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        intensity = float(normalized[index]) if index < len(normalized) else 0.0
        if index == 0:
            label = "intro"
        elif index == len(boundaries) - 2:
            label = "outro"
        elif intensity >= 0.72:
            label = "peak"
        elif intensity <= 0.28:
            label = "break"
        else:
            label = "body"
        sections.append(
            {
                "index": index,
                "startBeat": round(start, 6),
                "endBeat": round(min(end, max_beat), 6),
                "label": label,
                "intensity": round(intensity, 6),
            }
        )
    return {"schemaVersion": 1, "sections": sections}


def click_track_evidence(
    grid: Sequence[dict[str, Any]],
    duration_samples: int,
    sample_rate: int,
) -> dict[str, Any]:
    """Start, middle, and end click-track sample checkpoints for human listening."""

    if not grid:
        return {"file": "click_track.wav", "status": "missing", "checkpoints": []}
    start = grid[0]
    end = grid[-1]
    middle_target = duration_samples / 2.0
    middle = min(grid, key=lambda entry: abs(float(entry["sample"]) - middle_target))

    def checkpoint(label: str, entry: dict[str, Any]) -> dict[str, Any]:
        sample = int(entry["sample"])
        return {
            "label": label,
            "beat": float(entry["beat"]),
            "sample": sample,
            "timeSeconds": round(sample / float(sample_rate), 6),
        }

    return {
        "file": "click_track.wav",
        "status": "recorded",
        "checkpoints": [checkpoint("start", start), checkpoint("middle", middle), checkpoint("end", end)],
    }


def write_click_track(path: Path, audio: AudioBuffer, analysis: dict[str, Any]) -> None:
    output = np.clip(audio.samples * 0.55, -1.0, 1.0).astype(np.float32).copy()
    length = int(0.035 * audio.sample_rate)
    envelope = np.exp(-np.linspace(0.0, 7.0, length)).astype(np.float32)
    for entry in analysis.get("beatGrid", []):
        beat = float(entry["beat"])
        if abs(beat - round(beat)) > 1e-6:
            continue
        start = int(entry["sample"])
        if start < 0 or start >= len(output):
            continue
        frequency = 1320.0 if int(round(beat)) % 4 == 0 else 880.0
        count = min(length, len(output) - start)
        tone = np.sin(2.0 * math.pi * frequency * np.arange(count) / audio.sample_rate).astype(np.float32)
        output[start : start + count] += 0.35 * tone * envelope[:count]
    pcm = (np.clip(output, -1.0, 1.0) * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(audio.sample_rate)
        target.writeframes(pcm.tobytes())


def difficulty_rank(name: str) -> int:
    return {"Easy": 1, "Normal": 3, "Hard": 5, "Expert": 7, "ExpertPlus": 9}[name]


def quantile(values: Iterable[float], q: float, default: float = 0.0) -> float:
    materialized = list(values)
    return float(np.quantile(materialized, q)) if materialized else default


def direction_vector(direction: int) -> tuple[int, int]:
    return {
        0: (0, 1),
        1: (0, -1),
        2: (-1, 0),
        3: (1, 0),
        4: (-1, 1),
        5: (1, 1),
        6: (-1, -1),
        7: (1, -1),
        8: (0, 0),
    }.get(direction, (0, 0))


def circular_direction_distance(left: int, right: int) -> int:
    if left == 8 or right == 8:
        return 0
    order = [0, 5, 3, 7, 1, 6, 2, 4]
    li, ri = order.index(left), order.index(right)
    delta = abs(li - ri)
    return min(delta, 8 - delta)


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "Codex" / "beat-saber-mapping"
    return Path.home() / ".cache" / "beat-saber-mapping"


PROGRESS_PREFIX = "BEATFORGE_PROGRESS"


def emit_progress(stage: str, detail: str, percent: float | None = None) -> None:
    """Write one machine-readable progress line for the studio run panel."""

    clean_stage = " ".join(str(stage).split())
    clean_detail = " ".join(str(detail).split())
    line = f"{PROGRESS_PREFIX}\t{clean_stage}\t{clean_detail}"
    if percent is not None:
        line += f"\t{float(percent):.1f}"
    print(line, file=sys.stderr, flush=True)
