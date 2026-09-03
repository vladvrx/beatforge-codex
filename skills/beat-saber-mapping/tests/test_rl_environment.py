#!/usr/bin/env python3
"""Tests for BeatForge Reinforcement Learning (RL) Framework."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
import torch

from rl.dataset import BeatSaberTrajectoryDataset
from rl.environment import (
    ACTION_IDLE,
    BeatSaberEnv,
    decode_hand_action,
    encode_hand_action,
    NUM_HAND_ACTIONS,
)
from rl.models import ActorCriticPolicy, BeatForgeAudioEncoder
from rl.policy_generator import RLMapGenerator
from rl.rewards import (
    CompositeReward,
    KinematicReward,
    MusicalReward,
    StyleReward,
)
from rl.train_bc import train_bc
from rl.train_ppo import train_ppo


def test_action_encoding_decoding():
    for x in range(4):
        for y in range(3):
            for d in range(9):
                act_id = encode_hand_action(x, y, d)
                assert 1 <= act_id <= 108
                decoded = decode_hand_action(act_id)
                assert decoded is not None
                dx, dy, dd = decoded
                assert (dx, dy, dd) == (x, y, d)

    assert decode_hand_action(ACTION_IDLE) is None


def test_kinematic_reward_flow_and_penalties():
    reward_calc = KinematicReward(difficulty="Expert", bpm=120.0)

    # Initial note
    r_flow, r_pen = reward_calc.evaluate_transition(None, {"b": 0.0, "c": 0, "x": 1, "y": 0, "d": 1})
    assert r_flow > 0
    assert r_pen == 0.0

    # Opposite cut direction (Down followed by Up: natural pendulum return)
    note_down = {"b": 0.0, "c": 0, "x": 1, "y": 0, "d": 1}  # Down
    note_up = {"b": 0.5, "c": 0, "x": 1, "y": 0, "d": 0}    # Up
    flow_rew, flow_pen = reward_calc.evaluate_transition(note_down, note_up)
    assert flow_rew >= 0.8
    assert flow_pen == 0.0

    # Rapid repeated cut without reset (Down followed by Down at 0.1 beat: physical reset break)
    note_down_rapid = {"b": 0.1, "c": 0, "x": 1, "y": 0, "d": 1}
    bad_rew, bad_pen = reward_calc.evaluate_transition(note_down, note_down_rapid)
    assert bad_pen < 0.0

    # 135-degree wrist twist
    note_twist = {"b": 0.25, "c": 0, "x": 1, "y": 0, "d": 4}  # Down to UpLeft = 135 deg
    twist_rew, twist_pen = reward_calc.evaluate_transition(note_down, note_twist)
    assert twist_pen < 0.0


def test_simultaneous_hand_safety():
    reward_calc = KinematicReward(difficulty="Expert", bpm=120.0)

    # Simultaneous safe double (Left at col 1, Right at col 2)
    red = {"b": 1.0, "c": 0, "x": 1, "y": 0, "d": 1}
    blue = {"b": 1.0, "c": 1, "x": 2, "y": 0, "d": 1}
    sim_rew, sim_pen = reward_calc.evaluate_simultaneous(red, blue)
    assert sim_rew > 0.0
    assert sim_pen == 0.0

    # Exact coordinate collision (Left and Right at x=1, y=0 on same beat)
    blue_colliding = {"b": 1.0, "c": 1, "x": 1, "y": 0, "d": 1}
    col_rew, col_pen = reward_calc.evaluate_simultaneous(red, blue_colliding)
    assert col_pen <= -10.0


def test_environment_step_and_action_masking():
    env = BeatSaberEnv(difficulty="Expert", bpm=120.0)
    obs, info = env.reset()
    assert obs.shape[0] == env.obs_dim

    # Get action masks
    mask_red, mask_blue = env.action_masks()
    assert len(mask_red) == NUM_HAND_ACTIONS
    assert len(mask_blue) == NUM_HAND_ACTIONS
    assert mask_red[0]  # Idle action is always allowed

    # Take step
    act_red = encode_hand_action(1, 0, 1)
    act_blue = encode_hand_action(2, 0, 1)
    next_obs, reward, term, trunc, info = env.step((act_red, act_blue))

    assert next_obs.shape[0] == env.obs_dim
    assert isinstance(reward, float)
    assert not term
    assert "breakdown" in info


def test_actor_critic_policy():
    obs_dim = 69
    policy = ActorCriticPolicy(obs_dim=obs_dim)

    dummy_obs = torch.randn(4, obs_dim)
    logits_red, logits_blue, value = policy(dummy_obs)

    assert logits_red.shape == (4, NUM_HAND_ACTIONS)
    assert logits_blue.shape == (4, NUM_HAND_ACTIONS)
    assert value.shape == (4,)

    # Action sampling with masks
    dummy_mask_r = torch.ones(4, NUM_HAND_ACTIONS, dtype=torch.bool)
    dummy_mask_b = torch.ones(4, NUM_HAND_ACTIONS, dtype=torch.bool)
    dummy_mask_r[:, 10] = False  # Mask action 10

    (act_r, act_b), log_p, ent, val = policy.get_action_and_value(
        dummy_obs, mask_red=dummy_mask_r, mask_blue=dummy_mask_b
    )
    assert act_r.shape == (4,)
    assert act_b.shape == (4,)
    assert log_p.shape == (4,)
    assert ent.shape == (4,)
    assert val.shape == (4,)

    # Masked action must not be sampled
    assert not (act_r == 10).any()


def test_bc_training_loop():
    dataset = BeatSaberTrajectoryDataset.generate_synthetic(num_songs=2, beats_per_song=16)
    assert len(dataset) > 0

    policy = ActorCriticPolicy()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "test_bc_policy.pt"
        metrics = train_bc(
            policy=policy,
            dataset=dataset,
            epochs=2,
            batch_size=16,
            save_path=save_path,
        )
        assert metrics["loss"] >= 0.0
        assert save_path.exists()


def test_ppo_training_loop():
    env = BeatSaberEnv(difficulty="Expert", bpm=120.0)
    policy = ActorCriticPolicy()

    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "test_ppo_policy.pt"
        res = train_ppo(
            policy=policy,
            env=env,
            total_timesteps=128,
            rollout_steps=64,
            ppo_epochs=2,
            batch_size=32,
            save_path=save_path,
        )
        assert "final_mean_reward" in res
        assert save_path.exists()


def test_rl_map_generator_v3():
    generator = RLMapGenerator()
    bpm = 128.0
    grid = [round(i * 0.25, 4) for i in range(32 * 4)]  # 32 beats
    audio = {
        "onsets": [0.5] * len(grid),
        "flux": [0.5] * len(grid),
        "stems": {
            stem: [0.5] * len(grid)
            for stem in ("drums", "bass", "guitar", "piano", "vocals", "other")
        },
        "sections": ["verse"] * len(grid),
    }

    result = generator.generate_difficulty(
        audio_features=audio,
        beat_grid=grid,
        bpm=bpm,
        difficulty="Expert",
        deterministic=True,
    )

    assert result["version"] == "3.3.0"
    assert "colorNotes" in result
    assert "bombNotes" in result
    assert "obstacles" in result
    assert "sliders" in result
    assert "burstSliders" in result
    assert "basicBeatmapEvents" in result
    assert "colorBoostBeatmapEvents" in result
    assert result["customData"]["_generator"] == "BeatForge-RL-v1"
