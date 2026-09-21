"""Small deterministic RL regressions; no downloads or long training run."""

import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from rl.checkpoints import load_checkpoint, save_checkpoint
from rl.environment import BeatSaberEnv, decode_hand_action
from rl.evaluate import chart_metrics, evaluate_checkpoints
from rl.features import load_analysis_features
from rl.models import ActorCriticPolicy
from rl.policy_generator import RLMapGenerator
from rl.train_custom_tracks import extract_track_features, train_on_custom_library
from rl.train_ppo import RolloutBuffer, train_ppo
from test_rl_features import write_analysis
from kinematics import cross_hand_finding, vision_blocking_double


def test_terminal_last_slot_does_not_bootstrap_reset_episode_value():
    buffer = RolloutBuffer(1, 69)
    buffer.ptr = 1
    buffer.rewards[0] = 3
    buffer.values[0] = 2
    buffer.dones[0] = 1
    buffer.compute_gae(last_value=100, gamma=.9)
    assert buffer.returns[0].item() == pytest.approx(3)
    assert torch.isfinite(buffer.advantages).all()


def test_gae_cuts_internal_episode_boundary_but_bootstraps_nonterminal_tail():
    buffer = RolloutBuffer(3, 69)
    buffer.ptr = 3
    buffer.rewards[:] = torch.tensor([1, 2, 5])
    buffer.values[:] = torch.tensor([0, 0, 0])
    buffer.dones[:] = torch.tensor([0, 1, 0])
    buffer.compute_gae(last_value=10, gamma=.5, gae_lambda=1)
    assert buffer.returns.tolist() == pytest.approx([2, 2, 10])


def test_inference_requires_checkpoint_and_rejects_corrupt_weights(tmp_path):
    with pytest.raises(ValueError, match="explicit trained checkpoint"):
        RLMapGenerator()
    with pytest.raises(FileNotFoundError):
        RLMapGenerator(model_path=tmp_path / "missing.pt")
    path = tmp_path / "broken.pt"
    path.write_bytes(b"not a checkpoint")
    with pytest.raises(ValueError, match="Invalid RL checkpoint"):
        RLMapGenerator(model_path=path)


def test_checkpoint_hash_and_feature_provenance(tmp_path):
    policy = ActorCriticPolicy(hidden_dim=32)
    path = tmp_path / "policy.pt"
    save_checkpoint(policy, path, {"seed": 4, "trainingSourceSha256": ["a" * 64]})
    loaded, metadata = load_checkpoint(path)
    assert metadata["seed"] == 4
    assert len(metadata["checkpointSha256"]) == 64
    assert all(torch.equal(tensor, loaded.state_dict()[key]) for key, tensor in policy.state_dict().items())
    bad = policy.state_dict()
    bad["encoder.0.weight"][0, 0] = float("nan")
    torch.save(bad, path)
    with pytest.raises(ValueError, match="non-finite"):
        load_checkpoint(path)


def test_training_and_inference_load_identical_features(tmp_path):
    path = write_analysis(tmp_path / "analysis")
    audio, grid, bpm = extract_track_features(path)
    bundle = load_analysis_features(path)
    assert audio == bundle.audio_features
    assert grid == bundle.beat_grid
    assert bpm == bundle.bpm
    assert audio["sections"][-1] == "chorus"


def test_short_ppo_run_stops_at_requested_steps_and_repeats_with_seed():
    states = []
    for _ in range(2):
        torch.manual_seed(23)
        policy = ActorCriticPolicy(hidden_dim=16)
        env = BeatSaberEnv(beat_grid=[0, .25])
        metrics = train_ppo(policy, env, total_timesteps=3, rollout_steps=2, ppo_epochs=1, batch_size=2, seed=23)
        assert metrics["timesteps"] == 3
        assert metrics["completed_episodes"] == 1
        assert np.isfinite(metrics["policy_loss"])
        states.append({key: value.clone() for key, value in policy.state_dict().items()})
    assert all(torch.equal(states[0][key], states[1][key]) for key in states[0])


def test_environment_seed_does_not_mutate_numpy_global_rng():
    np.random.seed(321)
    expected = np.random.random()
    np.random.seed(321)
    env = BeatSaberEnv(beat_grid=[0, .25])
    env.reset(seed=77)
    assert np.random.random() == expected
    assert not any(env.audio_features["stemAvailability"].values())


def test_blue_mask_rejects_every_unsafe_simultaneous_pair():
    env = BeatSaberEnv(beat_grid=[0, .25])
    for red_action in range(1, env.action_dim):
        red_pose = decode_hand_action(red_action)
        red = {"b": 0, "c": 0, "x": red_pose[0], "y": red_pose[1], "d": red_pose[2]}
        mask = env.blue_action_mask(red_action)
        assert mask[0]
        for blue_action in np.flatnonzero(mask[1:]) + 1:
            x, y, direction = decode_hand_action(int(blue_action))
            blue = {"b": 0, "c": 1, "x": x, "y": y, "d": direction}
            assert cross_hand_finding(red, blue) is None
            assert not vision_blocking_double(red, blue)


def test_conditional_sampling_and_ppo_replay_have_identical_log_probabilities():
    torch.manual_seed(42)
    env = BeatSaberEnv(beat_grid=[0, .25])
    policy = ActorCriticPolicy(hidden_dim=16)
    obs = torch.as_tensor(env.reset()[0]).unsqueeze(0)
    mask_red, mask_blue = env.action_masks()
    conditional = lambda chosen: torch.as_tensor(env.blue_action_mask(int(chosen.item()), mask_blue)).unsqueeze(0)
    action, sampled_log_prob, _, _ = policy.get_action_and_value(
        obs, mask_red=torch.as_tensor(mask_red).unsqueeze(0),
        mask_blue=torch.as_tensor(mask_blue).unsqueeze(0), blue_mask_fn=conditional,
    )
    effective_blue = conditional(action[0])
    assert effective_blue[0, int(action[1].item())]
    _, replay_log_prob, _, _ = policy.get_action_and_value(
        obs, action=action, mask_red=torch.as_tensor(mask_red).unsqueeze(0), mask_blue=effective_blue,
    )
    assert sampled_log_prob.item() == pytest.approx(replay_log_prob.item())


def test_benchmark_uses_held_out_hashes_and_reports_real_validation(tmp_path):
    write_analysis(tmp_path / "train", source="a" * 64)
    write_analysis(tmp_path / "held-out", source="b" * 64)
    manifest = tmp_path / "library.json"
    manifest.write_text(json.dumps({"schemaVersion": 1, "tracks": [
        {"id": "train", "analysisDir": "train", "split": "train"},
        {"id": "held-out", "analysisDir": "held-out", "split": "validation"},
    ]}))
    policy = ActorCriticPolicy(hidden_dim=16)
    for parameter in policy.parameters():
        parameter.data.zero_()  # Deterministic idle policy, only for the fixture.
    path = tmp_path / "policy.pt"
    save_checkpoint(policy, path, {"trainingSourceSha256": ["a" * 64]})
    result = evaluate_checkpoints(manifest, [path], seed=3)
    assert result["heldOutVerifiedAgainstRecordedTraining"]
    assert result["results"][0]["tracks"][0]["id"] == "held-out"
    assert result["results"][0]["tracks"][0]["metrics"]["notes"] == 0
    assert result["results"][0]["tracks"][0]["metrics"]["headsetPlaytest"] == "not_performed"
    save_checkpoint(policy, path, {"trainingSourceSha256": ["b" * 64]})
    with pytest.raises(ValueError, match="used to train"):
        evaluate_checkpoints(manifest, [path])


def test_structural_benchmark_detects_colliding_notes(tmp_path):
    bundle = load_analysis_features(write_analysis(tmp_path / "analysis"))
    chart = {"version": "3.3.0", "colorNotes": [
        {"b": 1, "c": 0, "x": 1, "y": 0, "d": 1},
        {"b": 1, "c": 1, "x": 1, "y": 0, "d": 1},
    ]}
    result = chart_metrics(chart, bundle, "Expert")
    assert result["hardErrors"] > 0
    assert result["nps"] == pytest.approx(.8)
