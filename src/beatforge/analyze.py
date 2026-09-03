"""LEGACY — pre-premium analyzer (librosa). The studio pipeline never imports
this module; timing comes from skills/beat-saber-mapping/scripts/analyze_audio.py
(see docs/ARCHITECTURE.md). Kept only for the legacy CLI and its tests.

Audio DSP: BPM, beats, onsets, energy, sections."""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

from beatforge.chart import AnalysisResult, Section, SustainRegion


def analyze_audio(
    path: str | Path, sr: int = 22050, progress=None
) -> AnalysisResult:
    path = Path(path)

    def rep(stage: str, detail: str) -> None:
        if progress:
            progress(stage, detail)

    rep("load", "decoding audio")
    y, sr = librosa.load(str(path), sr=sr, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    y_harm, y_perc = librosa.effects.hpss(y)

    rep("tempo", "finding tempo and beats")
    tempo, beat_frames = librosa.beat.beat_track(y=y_perc, sr=sr, units="frames")
    bpm = float(np.atleast_1d(tempo)[0])
    if bpm <= 0:
        bpm = 120.0
    # Prefer common double/half if wildly off dance range
    while bpm < 70:
        bpm *= 2
    while bpm > 200:
        bpm /= 2

    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    if not beat_times:
        step = 60.0 / bpm
        beat_times = list(np.arange(0.0, duration, step))

    rep("onsets", "detecting onsets")
    onset_env = librosa.onset.onset_strength(y=y_perc, sr=sr)
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr,
        units="frames",
        backtrack=True,
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    strengths = onset_env[onset_frames] if len(onset_frames) else np.array([])
    if len(strengths):
        strengths = strengths / (float(np.max(strengths)) + 1e-8)
    else:
        strengths = np.array([])

    # Pass RAW onset times/strengths through; placement snaps them to the
    # beat-tick grid itself. Pre-quantizing here destroyed phase accuracy.
    q_times: list[float] = []
    q_strengths: list[float] = []
    for t, s in zip(onset_times.tolist(), strengths.tolist() if len(strengths) else []):
        q_times.append(float(t))
        q_strengths.append(float(s))

    # Energy per beat (RMS around each beat)
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    beat_energy: list[float] = []
    for bt in beat_times:
        i = int(np.argmin(np.abs(rms_times - bt)))
        beat_energy.append(float(rms[i]))
    if beat_energy:
        mx = max(beat_energy) + 1e-8
        beat_energy = [e / mx for e in beat_energy]

    rep("sections", "mapping song sections")
    sections = _segment_sections(duration, beat_times, beat_energy)
    sustains = _detect_sustains(y_harm, sr, beat_times, sections, duration)

    return AnalysisResult(
        bpm=bpm,
        duration=duration,
        beats=beat_times,
        onset_times=q_times,
        onset_strengths=q_strengths,
        beat_energy=beat_energy,
        sections=sections,
        sustains=sustains,
    )


def _detect_sustains(
    y_harm: np.ndarray,
    sr: int,
    beat_times: list[float],
    sections: list[Section],
    duration: float,
) -> list[SustainRegion]:
    """Find a small set of stable harmonic windows suitable for arcs.

    HPSS keeps drum hits from masquerading as held tones. We score two-to-four
    beat windows by harmonic activity, stability, and the lack of repeated
    harmonic attacks, then keep well-spaced phrases.
    """

    if len(beat_times) < 3 or not len(y_harm):
        return []

    hop = 512
    rms = librosa.feature.rms(y=y_harm, hop_length=hop)[0]
    flux = librosa.onset.onset_strength(y=y_harm, sr=sr, hop_length=hop)
    if not len(rms):
        return []

    rms_scale = float(np.percentile(rms, 95))
    if rms_scale <= 1e-8:
        return []
    rms = np.clip(rms / rms_scale, 0.0, 1.0)
    if len(flux):
        flux_scale = float(np.percentile(flux, 95))
        if flux_scale > 1e-8:
            flux = np.clip(flux / flux_scale, 0.0, 1.0)
        else:
            flux = np.zeros_like(flux)
    frame_times = librosa.frames_to_time(
        np.arange(len(rms)), sr=sr, hop_length=hop
    )

    candidates: list[tuple[float, float, float, int, int]] = []
    for i, start in enumerate(beat_times[:-2]):
        if start < 0.0 or start >= duration - 1.0:
            continue
        sec = section_at(sections, start)
        if sec.label == "outro":
            continue
        for span in (4, 3, 2):
            j = i + span
            if j >= len(beat_times):
                continue
            end = min(float(beat_times[j]), duration - 0.25)
            if end - start < 0.65:
                continue
            mask = (frame_times >= start) & (frame_times <= end)
            vals = rms[mask]
            if len(vals) < 3:
                continue
            mean_energy = float(np.mean(vals))
            active_fraction = float(np.mean(vals >= 0.22))
            stability = 1.0 - min(1.0, float(np.std(vals)) / (mean_energy + 1e-8))
            interior = (frame_times >= start + 0.2) & (frame_times <= end)
            common = min(len(flux), len(interior))
            flux_vals = flux[:common][interior[:common]] if common else np.array([])
            repeated_attacks = float(np.mean(flux_vals)) if len(flux_vals) else 0.0
            score = (
                mean_energy
                * active_fraction
                * (0.55 + 0.45 * stability)
                * (1.0 - 0.45 * repeated_attacks)
            )
            if mean_energy >= 0.2 and active_fraction >= 0.65 and stability >= 0.25:
                candidates.append((score, start, end, i, j))
                break

    candidates.sort(key=lambda item: item[0], reverse=True)
    chosen: list[tuple[float, float, float, int, int]] = []
    limit = min(12, max(1, int(duration / 12.0)))
    for candidate in candidates:
        _, start, end, i, j = candidate
        if any(not (j + 2 <= other_i or i >= other_j + 2) for *_, other_i, other_j in chosen):
            continue
        chosen.append(candidate)
        if len(chosen) >= limit:
            break

    return [
        SustainRegion(t=float(start), end_t=float(end), strength=round(score, 4))
        for score, start, end, _, _ in sorted(chosen, key=lambda item: item[1])
    ]


def _segment_sections(
    duration: float,
    beat_times: list[float],
    beat_energy: list[float],
) -> list[Section]:
    if not beat_times or not beat_energy:
        return [Section(t=0.0, label="body", energy=0.5)]

    energy = np.array(beat_energy, dtype=float)
    # Smooth and find novelty
    # Keep the smoothing local enough to preserve arrangement changes in long
    # songs.  The previous 1/16-song window blurred a six-minute track into a
    # few giant sections and made density ignore the music for minutes at once.
    win = max(4, len(energy) // 64)
    kernel = np.ones(win) / win
    smooth = np.convolve(energy, kernel, mode="same")
    novelty = np.abs(np.diff(smooth, prepend=smooth[0]))

    # Give long arrangements enough change points without slicing every phrase.
    n_cuts = min(15, max(3, len(energy) // 64))
    candidates = np.argsort(novelty)[::-1]
    cuts: list[int] = [0]
    min_gap = max(8, len(energy) // 24)
    for idx in candidates:
        if all(abs(idx - c) >= min_gap for c in cuts):
            cuts.append(int(idx))
        if len(cuts) >= n_cuts + 1:
            break
    cuts = sorted(cuts)
    if cuts[-1] != len(energy) - 1:
        cuts.append(len(energy) - 1)

    sections: list[Section] = []
    seg_means: list[float] = []
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        seg = energy[a : b + 1]
        seg_means.append(float(np.mean(seg)) if len(seg) else 0.5)

    # Relative-energy labeling: loudest segments are drops, quietest breathe.
    n_segs = len(seg_means)
    med_e = float(np.median(seg_means)) if seg_means else 0.5
    hi_e = max(seg_means) if seg_means else 1.0
    for i in range(n_segs):
        mean_e = seg_means[i]
        t = float(beat_times[min(cuts[i], len(beat_times) - 1)])
        r = mean_e / hi_e if hi_e > 1e-6 else 0.5
        if r >= 0.82:
            base = "drop"
        elif r >= 0.55:
            base = "verse"
        elif r >= 0.35:
            base = "build"
        else:
            base = "body"
        # Edge segments only get intro/outro when genuinely quieter than typical.
        if n_segs > 2 and i == 0 and mean_e < med_e:
            label = "intro"
        elif n_segs > 2 and i == n_segs - 1 and mean_e < med_e:
            label = "outro"
        elif n_segs <= 2:
            label = base if base != "build" else "verse"
        else:
            label = base
        sections.append(Section(t=t, label=label, energy=mean_e))

    if not sections:
        sections = [Section(t=0.0, label="body", energy=float(np.mean(energy)))]
    elif len(sections) == 1:
        sections[0].label = "body"
    return sections


def section_at(sections: list[Section], t: float) -> Section:
    current = sections[0]
    for s in sections:
        if s.t <= t:
            current = s
        else:
            break
    return current
