"""In-process Facebook Demucs stem split for BeatForge timing diagnostics.

Upstream: https://github.com/facebookresearch/demucs (pinned ``demucs==4.0.1``).
This module does not vendor that repository. It loads published Hybrid Transformer
weights through the installed package and writes every source the model emits.

Demucs is not a karaoke (vocals vs instrumental) tool in this pipeline, and it
cannot isolate every live instrument. The published ceiling is:

- ``htdemucs_6s``: drums, bass, guitar, piano, vocals, other
- ``htdemucs``: drums, bass, vocals, other

Kick vs snare vs hats, stacked synths, and similar splits are out of scope.
Stem trackers remain diagnostic; they cannot pass the 10 ms / 20 ms mix gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np

SIX_STEM_MODEL = "htdemucs_6s"
FOUR_STEM_MODEL = "htdemucs"
PREFERRED_MODELS = (SIX_STEM_MODEL, FOUR_STEM_MODEL)
REQUIRED_MODEL = SIX_STEM_MODEL
PACKAGE = "demucs==4.0.1"
CORE_STEMS = frozenset({"drums", "bass", "vocals", "other"})
SIX_STEMS = ("drums", "bass", "other", "vocals", "guitar", "piano")


class StemSeparation(NamedTuple):
    model: str
    stems: dict[str, Path]
    package: str = PACKAGE
    required_model: str = REQUIRED_MODEL

    @property
    def used_required_model(self) -> bool:
        return self.model == self.required_model


def _load_model():
    from demucs.pretrained import get_model

    last_error: Exception | None = None
    for name in PREFERRED_MODELS:
        try:
            model = get_model(name)
            model.cpu()
            model.eval()
            return model, name
        except Exception as error:
            last_error = error
    raise RuntimeError(f"Could not load Demucs ({', '.join(PREFERRED_MODELS)}): {last_error}")


def separate_stems(wav_path: Path, output: Path) -> StemSeparation:
    """Apply HTDemucs in-process and write one PCM16 WAV per model source."""
    import torch
    from demucs.apply import apply_model
    from demucs.audio import convert_audio

    from analyze_audio import read_canonical_wav, resolve_device, write_pcm16_wav

    model, model_name = _load_model()
    mix_np, mix_rate = read_canonical_wav(wav_path)
    mix = torch.from_numpy(mix_np)
    if mix.shape[0] != model.audio_channels or mix_rate != model.samplerate:
        mix = convert_audio(mix, mix_rate, model.samplerate, model.audio_channels)
    ref = mix.mean(0)
    mix = mix - ref.mean()
    scale = float(ref.std())
    if scale > 0:
        mix = mix / scale
    sources = apply_model(
        model,
        mix[None],
        device=resolve_device("auto"),
        shifts=1,
        split=True,
        overlap=0.25,
        progress=False,
        num_workers=0,
    )[0]
    if scale > 0:
        sources = sources * scale
    sources = sources + ref.mean()
    names = list(model.sources)
    if len(names) != int(sources.shape[0]):
        raise RuntimeError(f"{model_name} source count {len(names)} does not match tensor {tuple(sources.shape)}")
    output.mkdir(parents=True, exist_ok=True)
    stems: dict[str, Path] = {}
    for tensor, name in zip(sources, names):
        key = str(name).strip().casefold()
        path = output / f"{key}.wav"
        write_pcm16_wav(path, tensor.detach().cpu().numpy(), int(model.samplerate))
        stems[key] = path
    missing = sorted(CORE_STEMS - set(stems))
    if missing:
        raise RuntimeError(f"Demucs {model_name} omitted required stems {missing}; found {sorted(stems)}")
    return StemSeparation(model=model_name, stems=stems)


def annotate_events_with_stem_energy(analysis: dict, stems: dict[str, Path], window_seconds: float = 0.03) -> None:
    """Tag mix onsets with per-stem energy while Demucs WAVs still exist.

    Choreography reads ``events[].layer`` / ``stemEnergy``. Stem files are temp
    and must not be required later. Mix consensus timing is unchanged.
    """
    from analyze_audio import read_canonical_wav, SAMPLE_RATE

    envelopes: dict[str, np.ndarray] = {}
    stem_rate = SAMPLE_RATE
    for name, path in stems.items():
        waveform, stem_rate = read_canonical_wav(path)
        envelopes[name] = np.max(np.abs(np.asarray(waveform, dtype=np.float32)), axis=0)
    half = max(1, int(window_seconds * float(stem_rate)))
    mix_rate = float(analysis.get("sampleRate") or SAMPLE_RATE)
    for event in analysis.get("events") or []:
        mix_sample = int(event.get("sample") or 0)
        sample = int(round(mix_sample * float(stem_rate) / mix_rate)) if mix_rate else mix_sample
        energies: dict[str, float] = {}
        for name, envelope in envelopes.items():
            lo = max(0, sample - half)
            hi = min(int(envelope.shape[0]), sample + half + 1)
            energies[name] = float(np.max(envelope[lo:hi])) if hi > lo else 0.0
        event["stemEnergy"] = energies
        event["layer"] = max(energies, key=energies.get) if energies else "mix"
