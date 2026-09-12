"""BeatForge Reinforcement Learning (RL) Framework for Beat Saber Mapping."""

from __future__ import annotations

def __getattr__(name):
    # Feature extraction and benchmark metric helpers work without PyTorch.
    if name in {"ActorCriticPolicy", "BeatForgeAudioEncoder"}:
        from . import models
        return getattr(models, name)
    if name == "BeatSaberEnv":
        from .environment import BeatSaberEnv
        return BeatSaberEnv
    if name in {"CompositeReward", "KinematicReward", "MusicalReward", "StyleReward"}:
        from . import rewards
        return getattr(rewards, name)
    raise AttributeError(name)

__all__ = [
    "BeatSaberEnv",
    "ActorCriticPolicy",
    "BeatForgeAudioEncoder",
    "CompositeReward",
    "KinematicReward",
    "MusicalReward",
    "StyleReward",
]
