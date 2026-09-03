#!/usr/bin/env python3
"""Create a sample-accurate Beat Saber timing and structure analysis."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
import os
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np

from beatforge_core import (
    Anchor,
    SAMPLE_RATE,
    analyze_audio_buffer,
    build_grid_from_anchors,
    consensus_metrics,
    decode_to_wav,
    default_cache_dir,
    emit_progress,
    load_anchors,
    load_audio,
    sample_to_beat,
    section_analysis,
    write_click_track,
    write_json,
)


BEATNET_PLUS_COMMIT = "bb90eb0a9065b101a4b4c4cb2b2061950266cb4b"
PRIMARY_CLOCK = "beat-this"
CHALLENGER_CLOCK = "beatnet-plus"
CLOCK_MODELS = frozenset({PRIMARY_CLOCK, CHALLENGER_CLOCK})


def configure_madmom_compatibility() -> None:
    """Expose the maintained madmom-infer fork under BeatNet+'s legacy name."""
    if importlib.util.find_spec("madmom") is not None:
        return
    if importlib.util.find_spec("madmom_infer") is None:
        return
    madmom = importlib.import_module("madmom_infer")
    features = importlib.import_module("madmom_infer.features")
    downbeats = importlib.import_module("madmom_infer.features.downbeats")
    if not hasattr(features, "DBNDownBeatTrackingProcessor"):
        features.DBNDownBeatTrackingProcessor = downbeats.DBNDownBeatTrackingProcessor
    sys.modules.setdefault("madmom", madmom)
    sys.modules.setdefault("madmom.features", features)


def configure_beatnet_plus(weights: Path | None) -> Path | None:
    configure_madmom_compatibility()
    # BeatNet+ still calls numpy.in1d, which NumPy 2 removed.
    if not hasattr(np, "in1d"):
        np.in1d = np.isin
    source_root = default_cache_dir() / "models" / f"BeatNet-Plus-{BEATNET_PLUS_COMMIT[:12]}"
    source = source_root / "src"
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0, str(source))
    if weights:
        return weights
    candidate = source / "BeatNetPlus" / "models" / "generic_weights.pt"
    return candidate if candidate.is_file() else None


def model_track(name: str, beats: list[float], downbeats: list[float] | None = None) -> dict[str, Any] | None:
    materialized = sorted(float(value) for value in beats if math.isfinite(float(value)) and float(value) >= 0)
    if len(materialized) < 3:
        return None
    intervals = np.diff(materialized)
    bpm = 60.0 / float(np.median(intervals))
    while bpm < 60:
        bpm *= 2
    while bpm > 200:
        bpm /= 2
    return {
        "name": name,
        "bpm": bpm,
        "phaseSample": int(round(materialized[0] * SAMPLE_RATE)),
        "score": 1.0,
        "phaseScore": 1.0,
        "beatsSeconds": materialized,
        "downbeatsSeconds": sorted(float(value) for value in (downbeats or [])),
    }


def run_beat_this(wav_path: Path, device: str) -> dict[str, Any]:
    from beat_this.inference import File2Beats

    estimator = File2Beats(checkpoint_path="final0", device=device, dbn=False)
    beats, downbeats = estimator(str(wav_path))
    return model_track("beat-this", list(beats), list(downbeats)) or {}


def run_all_in_one(wav_path: Path, device: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        import allin1_infer as allin1
    except ImportError:
        import allin1

    if os.name == "nt":
        # Some Windows configurations report symlink capability even though the
        # current process lacks SeCreateSymbolicLinkPrivilege. Force the Hub's
        # documented copy fallback so first-use checkpoint downloads are reliable.
        import huggingface_hub.file_download as hub_download

        hub_download.are_symlinks_supported = lambda *_args, **_kwargs: False
    result = allin1.analyze(str(wav_path), device=device, keep_byproducts=False, multiprocess=False)
    track = model_track("all-in-one", list(result.beats), list(result.downbeats)) or {}
    segments = [
        {
            "startSeconds": float(segment.start),
            "endSeconds": float(segment.end),
            "label": str(segment.label),
        }
        for segment in result.segments
    ]
    return track, segments


def run_beatnet_plus(wav_path: Path, device: str, weights: Path) -> dict[str, Any]:
    from BeatNetPlus.inference import BeatNetPlusInference

    estimator = BeatNetPlusInference(str(weights), mode="online", inference_model="PF", device=device)
    output = estimator.process(str(wav_path))
    array = np.asarray(output)
    if array.ndim != 2 or array.shape[1] < 2:
        raise RuntimeError("BeatNet+ returned an unknown output shape")
    beats = array[:, 0].astype(float).tolist()
    downbeats = array[array[:, 1] == 1, 0].astype(float).tolist()
    return model_track("beatnet-plus", beats, downbeats) or {}


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def read_canonical_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise RuntimeError(f"canonical WAV must be PCM16, got sampwidth={width}")
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    audio = pcm.reshape(-1, channels).T.copy()
    return audio, rate


def write_pcm16_wav(path: Path, waveform: np.ndarray, samplerate: int) -> None:
    """Serialize float audio as PCM16 without torchaudio or TorchCodec."""
    array = np.asarray(waveform, dtype=np.float32)
    if array.ndim == 1:
        array = array[None, :]
    if array.size == 0:
        raise RuntimeError("refusing to write an empty stem")
    peak = float(np.max(np.abs(array)))
    if peak > 0.99:
        array = array * (0.99 / peak)
    pcm = np.clip(np.round(array.T * 32767.0), -32768, 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(int(pcm.shape[1]))
        handle.setsampwidth(2)
        handle.setframerate(int(samplerate))
        handle.writeframes(pcm.tobytes())


def separate_stems(wav_path: Path, output: Path):
    """Run pinned Demucs ``htdemucs_6s`` (six instrument stems) in-process."""
    from demucs_stems import separate_stems as run_demucs

    return run_demucs(wav_path, output)


def analyze_stem_models(
    stems: dict[str, Path],
    model_names: set[str],
    device: str,
    beatnet_weights: Path | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    tracks: list[dict[str, Any]] = []
    errors: list[str] = []
    for stem_name, path in sorted(stems.items()):
        runners = []
        if "beat-this" in model_names and importlib.util.find_spec("beat_this"):
            runners.append(("beat-this", lambda p=path: run_beat_this(p, device)))
        # All-In-One already performs its own four-stem analysis on the full mix.
        # Re-running it on a single stem would separate that stem again and distort
        # the model input, so only the direct beat trackers run per external stem.
        if "beatnet-plus" in model_names and importlib.util.find_spec("BeatNetPlus") and beatnet_weights and beatnet_weights.is_file():
            runners.append(("beatnet-plus", lambda p=path: run_beatnet_plus(p, device, beatnet_weights)))
        for model_name, runner in runners:
            try:
                emit_progress("timing", f"running {model_name} on the {stem_name} stem")
                track = runner()
                if track:
                    track["name"] = f"{model_name}:{stem_name}"
                    track["stem"] = stem_name
                    tracks.append(track)
            except Exception as exc:
                errors.append(f"{model_name} failed on {stem_name} stem: {exc}")
    return tracks, errors


def pairwise_beat_agreement(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    with_beats = [track for track in tracks if track.get("beatsSeconds")]
    residuals: list[float] = []
    count_ratios: list[float] = []
    cumulative_drift_ms: list[float] = []
    for index, left in enumerate(with_beats):
        left_beats = np.asarray(left["beatsSeconds"], dtype=np.float64)
        for right in with_beats[index + 1 :]:
            right_beats = np.asarray(right["beatsSeconds"], dtype=np.float64)
            if not len(left_beats) or not len(right_beats):
                continue
            count_ratios.append(min(len(left_beats), len(right_beats)) / max(len(left_beats), len(right_beats)))
            cumulative_drift_ms.append(abs((left_beats[-1] - left_beats[0]) - (right_beats[-1] - right_beats[0])) * 1000.0)
            positions = np.searchsorted(right_beats, left_beats)
            for beat, position in zip(left_beats, positions):
                neighbors = right_beats[max(0, position - 1) : min(len(right_beats), position + 1)]
                if len(neighbors):
                    residuals.append(float(np.min(np.abs(neighbors - beat)) * 1000.0))
    return {
        "trackCount": len(with_beats),
        "medianMs": float(np.median(residuals)) if residuals else None,
        "p95Ms": float(np.percentile(residuals, 95)) if residuals else None,
        "minimumCountRatio": min(count_ratios) if count_ratios else None,
        "maximumCumulativeDriftMs": max(cumulative_drift_ms) if cumulative_drift_ms else None,
    }


def clock_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Beat This and BeatNet+ only. All-In-One is structure, not a beat clock."""
    return [
        track
        for track in tracks
        if track.get("name") in CLOCK_MODELS and len(track.get("beatsSeconds") or []) >= 3
    ]


def select_primary_track(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    named = {str(track.get("name")): track for track in tracks if len(track.get("beatsSeconds") or []) >= 3}
    if PRIMARY_CLOCK in named:
        return named[PRIMARY_CLOCK]
    if CHALLENGER_CLOCK in named:
        return named[CHALLENGER_CLOCK]
    return None


def fold_double_time_track(
    track: dict[str, Any],
    *,
    consensus: dict[str, Any],
    dual_clock_passes: bool,
) -> dict[str, Any]:
    """When clocks disagree, drop a 2x pulse so mapping uses the groove, not 8th-notes as beats."""

    beats = [float(item) for item in (track.get("beatsSeconds") or track.get("beatsSeconds") or [])]
    bpm = float(track.get("bpm") or 0.0)
    if dual_clock_passes or bpm < 155.0 or len(beats) < 8:
        return track
    median = consensus.get("bpmMedian", consensus.get("bpmMedian"))
    spread = float(consensus.get("bpmSpreadPercent", consensus.get("bpmSpreadPercent")) or 0.0)
    median_bpm = float(median) if isinstance(median, (int, float)) else bpm
    if median_bpm >= 150.0 and spread < 20.0:
        return track
    closer_to_half = abs((bpm / 2.0) - median_bpm) <= abs(bpm - median_bpm)
    if not closer_to_half and spread < 25.0 and bpm < 170.0:
        return track
    even = beats[0::2]
    odd = beats[1::2]
    downs = [float(item) for item in (track.get("downbeatsSeconds") or track.get("downbeatsSeconds") or [])]

    def alignment(sequence: list[float]) -> float:
        if not downs or not sequence:
            return 0.0
        return sum(min(abs(beat - down) for beat in sequence) for down in downs[:16])

    chosen = odd if downs and alignment(odd) < alignment(even) else even
    folded = dict(track)
    folded["beatsSeconds"] = chosen
    folded["beatsSeconds"] = chosen
    folded["bpm"] = bpm / 2.0
    folded["foldedDoubleTime"] = True
    return folded


def resnap_events_to_grid(analysis: dict[str, Any]) -> None:
    """Keep onset times in samples; rewrite beat fields after the mix clock changes."""

    grid = analysis.get("beatGrid") or analysis.get("beatGrid") or []
    if len(grid) < 2:
        return
    for event in analysis.get("events") or []:
        sample = event.get("sample")
        if sample is None:
            continue
        beat = sample_to_beat(int(sample), grid)
        event["beat"] = round(beat, 6)
        event["snappedBeat"] = round(round(beat * 8.0) / 8.0, 6)


def dual_clock_check(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    clocks = clock_tracks(tracks)
    agreement = pairwise_beat_agreement(clocks)
    return {
        "primary": PRIMARY_CLOCK,
        "challenger": CHALLENGER_CLOCK,
        "present": [str(track.get("name")) for track in clocks],
        "agreement": agreement,
        "passes": external_agreement_passes(agreement),
    }


def dual_clock_vs_grid(analysis: dict[str, Any]) -> dict[str, Any]:
    """After an AI timing pass, compare both clocks to the adopted mix grid."""
    sample_rate = float(analysis.get("sampleRate") or SAMPLE_RATE)
    grid = [
        int(item["sample"]) / sample_rate
        for item in analysis.get("beatGrid") or []
        if "sample" in item
    ]
    grid_track = {"name": "adopted-grid", "beatsSeconds": grid}
    per_model: dict[str, Any] = {}
    for track in clock_tracks(analysis.get("trackers") or []):
        agreement = pairwise_beat_agreement([grid_track, track])
        per_model[str(track.get("name"))] = {
            "agreement": agreement,
            "passes": external_agreement_passes(agreement),
        }
    both = PRIMARY_CLOCK in per_model and CHALLENGER_CLOCK in per_model
    return {
        "primary": PRIMARY_CLOCK,
        "challenger": CHALLENGER_CLOCK,
        "stage": "post-ai",
        "models": per_model,
        "passes": both and all(item["passes"] for item in per_model.values()),
    }


def external_agreement_passes(agreement: dict[str, Any]) -> bool:
    """Beat This vs BeatNet+ mix consensus. Do not lower these constants if a model fails."""

    return (
        int(agreement.get("trackCount") or 0) >= 2
        and agreement.get("medianMs") is not None
        and float(agreement["medianMs"]) <= 10.0
        and agreement.get("p95Ms") is not None
        and float(agreement["p95Ms"]) <= 20.0
        and agreement.get("minimumCountRatio") is not None
        and float(agreement["minimumCountRatio"]) >= 0.98
        and agreement.get("maximumCumulativeDriftMs") is not None
        and float(agreement["maximumCumulativeDriftMs"]) <= 20.0
    )


def onset_grid_residuals(analysis: dict[str, Any], grid: list[dict[str, Any]]) -> dict[str, Any]:
    samples = np.asarray([int(item["sample"]) for item in grid], dtype=np.int64)
    residuals: list[float] = []
    if len(samples):
        for event in analysis.get("events", []):
            if float(event.get("strength", 0.0)) < 2.0:
                continue
            sample = int(event["sample"])
            position = int(np.searchsorted(samples, sample))
            neighbors = samples[max(0, position - 1) : min(len(samples), position + 1)]
            if len(neighbors):
                residuals.append(float(np.min(np.abs(neighbors - sample)) / SAMPLE_RATE * 1000.0))
    return {
        "medianMs": float(np.median(residuals)) if residuals else None,
        "p95Ms": float(np.percentile(residuals, 95)) if residuals else None,
        "sampleCount": len(residuals),
    }


def grid_from_primary_model(track: dict[str, Any], duration_samples: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    beats = track.get("beatsSeconds", [])
    downbeats = track.get("downbeatsSeconds", [])
    if len(beats) < 2:
        raise ValueError("primary model did not return enough beats")
    if downbeats:
        origin_time = downbeats[0]
        origin_index = int(np.argmin(np.abs(np.asarray(beats) - origin_time)))
    else:
        origin_index = 0
    anchors = [Anchor(float(index - origin_index), int(round(seconds * SAMPLE_RATE)), "model-beat") for index, seconds in enumerate(beats)]
    return build_grid_from_anchors(anchors, duration_samples)


def merge_external_models(
    analysis: dict[str, Any],
    audio_path: Path,
    model_names: set[str],
    device: str,
    beatnet_weights: Path | None,
    stems_mode: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    segments: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    beatnet_weights = configure_beatnet_plus(beatnet_weights)
    with tempfile.TemporaryDirectory(prefix="beatforge-models-") as temp:
        wav_path = Path(temp) / "canonical.wav"
        emit_progress("timing", "decoding a 44.1 kHz canonical WAV for the beat models")
        decode_to_wav(audio_path, wav_path, SAMPLE_RATE)
        if "beat-this" in model_names:
            if importlib.util.find_spec("beat_this"):
                try:
                    emit_progress("timing", "running Beat This on the mix")
                    external.append(run_beat_this(wav_path, device))
                except Exception as exc:
                    errors.append(f"Beat This failed: {exc}")
            else:
                errors.append("Beat This is not installed")
        if "all-in-one" in model_names:
            if importlib.util.find_spec("allin1_infer") or importlib.util.find_spec("allin1"):
                try:
                    emit_progress("timing", "running All-In-One structure and beat tracking")
                    track, segments = run_all_in_one(wav_path, device)
                    external.append(track)
                except Exception as exc:
                    errors.append(f"All-In-One failed: {exc}")
            else:
                errors.append("All-In-One is not installed")
        if "beatnet-plus" in model_names:
            if not importlib.util.find_spec("BeatNetPlus"):
                errors.append("BeatNet+ is not installed")
            elif not beatnet_weights or not beatnet_weights.is_file():
                errors.append("BeatNet+ weights were not provided")
            else:
                try:
                    emit_progress("timing", "running BeatNet+ on the mix")
                    external.append(run_beatnet_plus(wav_path, device, beatnet_weights))
                except Exception as exc:
                    errors.append(f"BeatNet+ failed: {exc}")
        stem_tracks: list[dict[str, Any]] = []
        analysis["demucs"] = {
            "package": "demucs==4.0.1",
            "requiredModel": "htdemucs_6s",
            "model": None,
            "stems": [],
            "status": "skipped",
        }
        demucs_available = importlib.util.find_spec("demucs") is not None
        if stems_mode == "on" and not demucs_available:
            errors.append("Demucs stem separation was required but is not installed")
            analysis["demucs"]["status"] = "missing"
        elif stems_mode != "off" and demucs_available:
            try:
                emit_progress("timing", "separating drums, bass, guitar, piano, vocals, and other with Demucs htdemucs_6s")
                from demucs_stems import annotate_events_with_stem_energy

                separation = separate_stems(wav_path, Path(temp) / "stems")
                annotate_events_with_stem_energy(analysis, separation.stems)
                stem_tracks, stem_errors = analyze_stem_models(separation.stems, model_names, device, beatnet_weights)
                errors.extend(stem_errors)
                analysis["demucs"] = {
                    "package": separation.package,
                    "requiredModel": separation.required_model,
                    "model": separation.model,
                    "stems": sorted(separation.stems),
                    "status": "ok" if separation.used_required_model else "fallback",
                    "note": "Use htdemucs_6s (six stems). Do not use Demucs CLI --two-stems karaoke mode.",
                }
                if not separation.used_required_model:
                    errors.append(
                        f"Demucs loaded {separation.model} instead of required {separation.required_model}"
                    )
            except Exception as exc:
                errors.append(f"Demucs separation failed: {exc}")
                analysis["demucs"]["status"] = "failed"
                analysis["demucs"]["error"] = str(exc)
        analysis["stemTrackers"] = stem_tracks
    external = [track for track in external if track]
    analysis["trackers"].extend(external)
    analysis["consensus"] = consensus_metrics(analysis["trackers"])
    clocks = clock_tracks(external)
    agreement = pairwise_beat_agreement(clocks)
    analysis["externalAgreement"] = agreement
    analysis["dualClockCheck"] = dual_clock_check(external)
    analysis["dualClockCheck"]["stage"] = "main-run"
    primary = select_primary_track(external)
    if primary:
        primary = fold_double_time_track(
            primary,
            consensus=analysis.get("consensus") or {},
            dual_clock_passes=bool(analysis["dualClockCheck"].get("passes", analysis["dualClockCheck"].get("passes"))),
        )
        grid, regions = grid_from_primary_model(primary, int(analysis["durationSamples"]))
        analysis["beatGrid"] = grid
        analysis["tempoRegions"] = regions
        analysis["bpm"] = float(primary["bpm"])
        analysis["primaryTracker"] = str(primary["name"])
        analysis["foldedDoubleTime"] = bool(primary.get("foldedDoubleTime"))
        resnap_events_to_grid(analysis)
        onset_residuals = onset_grid_residuals(analysis, grid)
        analysis["externalGridOnsetResiduals"] = onset_residuals
        onset_ok = (
            onset_residuals["medianMs"] is not None
            and onset_residuals.get("p95Ms", onset_residuals.get("p95Ms")) is not None
            and onset_residuals["medianMs"] <= 10.0
            and float(onset_residuals.get("p95Ms", onset_residuals.get("p95Ms"))) <= 20.0
        )
        if str(primary["name"]) == PRIMARY_CLOCK and analysis["dualClockCheck"].get("passes", analysis["dualClockCheck"].get("passes")) and onset_ok:
            analysis["gridSource"] = "beat-this-primary"
            analysis["status"] = "timing_verified"
        else:
            analysis["gridSource"] = f"{primary['name']}-unconfirmed"
            analysis["dualClockCheck"]["stage"] = "pending-ai"
    analysis["modelErrors"] = errors
    return analysis, segments, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="analysis output directory")
    parser.add_argument("--anchors", type=Path)
    parser.add_argument("--bpm", type=float)
    parser.add_argument("--offset-seconds", type=float)
    parser.add_argument("--models", default="beat-this,beatnet-plus,all-in-one", help="comma-separated external ensemble; use none for internal analysis only")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--beatnet-weights", type=Path)
    parser.add_argument("--stems", choices=("auto", "on", "off"), default="auto")
    args = parser.parse_args()
    args.beatnet_weights = configure_beatnet_plus(args.beatnet_weights)
    if not args.audio.is_file():
        print(f"ERROR: audio file does not exist: {args.audio}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        emit_progress("timing", f"loading {args.audio.name} at 44.1 kHz")
        audio = load_audio(args.audio)
        anchors = load_anchors(args.anchors, audio.sample_rate)
        emit_progress("timing", "fitting the internal onset and tempo hypothesis")
        analysis = analyze_audio_buffer(audio, anchors, args.bpm, args.offset_seconds)
        model_names = {name.strip() for name in args.models.split(",") if name.strip() and name.strip() != "none"}
        external_segments: list[dict[str, Any]] = []
        if model_names:
            analysis, external_segments, _errors = merge_external_models(
                analysis,
                args.audio,
                model_names,
                resolve_device(args.device),
                args.beatnet_weights,
                args.stems,
            )
        sections = section_analysis(analysis)
        if external_segments:
            for segment in external_segments:
                segment["startBeat"] = round(float(segment["startSeconds"]) * float(analysis["bpm"]) / 60.0, 6)
                segment["endBeat"] = round(float(segment["endSeconds"]) * float(analysis["bpm"]) / 60.0, 6)
            sections = {"schemaVersion": 1, "source": "all-in-one", "sections": external_segments}
        feature_frames = analysis.pop("featureFrames", {})
        write_json(args.out / "analysis.json", analysis)
        write_json(
            args.out / "beat_grid.json",
            {
                "schemaVersion": 1,
                "status": analysis["status"],
                "sampleRate": analysis["sampleRate"],
                "source": analysis["gridSource"],
                "tempoRegions": analysis["tempoRegions"],
                "beats": analysis["beatGrid"],
            },
        )
        write_json(args.out / "sections.json", sections)
        if feature_frames:
            np.savez_compressed(args.out / "audio_features.npz", **{key: np.asarray(value) for key, value in feature_frames.items()})
        emit_progress("timing", "writing analysis files and the click-track WAV")
        write_click_track(args.out / "click_track.wav", audio, analysis)
        print(json.dumps({"status": analysis["status"], "bpm": analysis["bpm"], "output": str(args.out.resolve()), "modelErrors": analysis.get("modelErrors", [])}, indent=2))
        return 3 if analysis["status"] == "needs_anchors" else 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
