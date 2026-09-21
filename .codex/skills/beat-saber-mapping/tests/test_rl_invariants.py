"""Independent serialization, episode-boundary, and reproducibility regressions."""

from collections import Counter

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from beatforge_core import ValidationReport
from rl.checkpoints import save_checkpoint
from rl.environment import BeatSaberEnv
from rl.models import ActorCriticPolicy
from rl.policy_generator import RLMapGenerator
from rl.safety import safe_hold_candidates
from rl.train_ppo import RolloutBuffer, train_ppo
from timing_export import project_map_timing
from validate_map import validate_v3


def fixture_audio(length=96, bpm=160, variable=False):
    grid = [index / 4 for index in range(length)]
    tempos = [90 if index < length // 2 else 220 for index in range(length)] if variable else [bpm] * length
    samples = [11025]
    for index in range(length - 1):
        samples.append(samples[-1] + round(44100 * 60 / tempos[index] / 4))
    audio = {
        "onsets": [.95] * length, "flux": [.8] * length, "sustainBeats": [2.] * length,
        "sections": ["chorus"] * length,
        "stems": {name: [.9] * length for name in ("drums", "bass", "guitar", "piano", "vocals", "other")},
        "timesSeconds": [sample / 44100 for sample in samples], "stepBpms": tempos,
    }
    analysis = {"bpm": bpm, "sampleRate": 44100,
                "beatGrid": [{"beat": beat, "sample": sample} for beat, sample in zip(grid, samples)]}
    return audio, grid, analysis


@pytest.mark.parametrize("difficulty", ["Easy", "Hard", "ExpertPlus"])
@pytest.mark.parametrize("bpm,variable", [(90, False), (220, False), (120, True)])
@pytest.mark.parametrize("seed", [0, 3])
def test_dense_adversarial_rl_output_passes_independent_exported_validator(difficulty, bpm, variable, seed):
    torch.manual_seed(seed)
    audio, grid, analysis = fixture_audio(bpm=bpm, variable=variable)
    chart = RLMapGenerator(policy=ActorCriticPolicy(hidden_dim=16)).generate_difficulty(
        audio, grid, bpm, difficulty=difficulty, deterministic=False, seed=seed,
    )
    report = ValidationReport()
    validate_v3(project_map_timing(chart, analysis), "fixture.dat", report, bpm=bpm, difficulty=difficulty)
    assert len(chart["colorNotes"]) > len(grid) // 8, "Passing by emitting an empty or nearly empty chart is not acceptable"
    assert not report.errors, dict(Counter(issue.code for issue in report.errors))


@pytest.mark.parametrize("deterministic", [True, False])
def test_checkpoint_generation_is_reproducible_and_preserves_caller_rng(tmp_path, deterministic):
    torch.manual_seed(17)
    checkpoint = tmp_path / "policy.pt"
    save_checkpoint(ActorCriticPolicy(hidden_dim=16), checkpoint, {"trainingSourceSha256": ["a" * 64]})
    audio, grid, _ = fixture_audio(length=40)
    state = torch.random.get_rng_state().clone()
    first = RLMapGenerator(model_path=checkpoint).generate_difficulty(audio, grid, 160, deterministic=deterministic, seed=42)
    assert torch.equal(state, torch.random.get_rng_state())
    torch.rand(13)  # Unrelated application use must not change the map's random stream.
    second = RLMapGenerator(model_path=checkpoint).generate_difficulty(audio, grid, 160, deterministic=deterministic, seed=42)
    assert first == second
    if not deterministic:
        other = RLMapGenerator(model_path=checkpoint).generate_difficulty(audio, grid, 160, deterministic=False, seed=43)
        assert other["colorNotes"] != first["colorNotes"]


def test_safe_holds_survive_without_erasing_notes_or_overlapping_ownership():
    notes = [{"b": beat, "c": 0, "x": 1, "y": 0, "d": direction} for beat, direction in [(0, 1), (1, 0), (2, 1), (4, 0)]]
    arc = {"b": 0, "c": 0, "x": 1, "y": 0, "d": 1, "tb": 1, "tx": 1, "ty": 0, "tc": 0, "mu": 1, "tmu": 1, "m": 0}
    chain = {"b": 2, "c": 0, "x": 1, "y": 0, "d": 1, "tb": 2.25, "tx": 1, "ty": 0, "sc": 5, "s": 1}
    conflicting = {**chain, "b": 0, "tb": .25}
    grid = [index / 4 for index in range(17)]
    arcs, chains, rejected = safe_hold_candidates(notes, [arc], [chain, conflicting], {}, grid, 120, "Easy")
    assert arcs == [arc] and chains == [chain]
    assert rejected["hold_occupancy"] == 1
    report = ValidationReport()
    validate_v3({"version": "3.3.0", "colorNotes": notes, "sliders": arcs, "burstSliders": chains},
                "fixture.dat", report, bpm=120, difficulty="Easy")
    assert not report.errors
    assert len(notes) == 4


@pytest.mark.parametrize("terminal,expected", [(True, 2.), (False, 8.3)])
def test_actual_ppo_rollout_distinguishes_terminal_and_time_limit(monkeypatch, terminal, expected):
    class BoundaryEnv(BeatSaberEnv):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            return np.zeros(self.obs_dim, dtype=np.float32), {}

        def step(self, action):
            return np.ones(self.obs_dim, dtype=np.float32), 2., terminal, not terminal, {}

    policy = ActorCriticPolicy(hidden_dim=16)
    # Final observation has value 7; reset observation has deliberately huge value
    # 123. Neither a true terminal nor the new episode may bootstrap from 123.
    monkeypatch.setattr(policy, "forward", lambda obs: (None, None, torch.where(obs[:, 0] > 0, 7., 123.)))
    recorded = {}
    original = RolloutBuffer.compute_gae

    def inspect(self, *args, **kwargs):
        original(self, *args, **kwargs)
        recorded["return"] = self.returns[0].item()
        recorded["reward"] = self.rewards[0].item()
        recorded["done"] = self.dones[0].item()

    monkeypatch.setattr(RolloutBuffer, "compute_gae", inspect)
    result = train_ppo(policy, BoundaryEnv(beat_grid=[0, .25]), total_timesteps=1,
                       rollout_steps=1, ppo_epochs=1, batch_size=1, gamma=.9, seed=4)
    assert recorded == pytest.approx({"return": expected, "reward": expected, "done": 1.})
    assert result["final_mean_reward"] == 2., "Reported episode reward must exclude the bootstrap value"
