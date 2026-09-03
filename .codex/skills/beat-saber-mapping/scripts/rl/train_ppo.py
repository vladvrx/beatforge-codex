#!/usr/bin/env python3
"""Proximal Policy Optimization (PPO) training pipeline with Action Masking."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

try:
    from .environment import BeatSaberEnv, NUM_HAND_ACTIONS
    from .models import ActorCriticPolicy
except ImportError:
    from rl.environment import BeatSaberEnv, NUM_HAND_ACTIONS
    from rl.models import ActorCriticPolicy


class RolloutBuffer:
    """Stores trajectories collected during policy rollout."""

    def __init__(self, buffer_size: int, obs_dim: int, device: str = "cpu"):
        self.buffer_size = buffer_size
        self.device = device
        self.ptr = 0

        self.observations = torch.zeros((buffer_size, obs_dim), dtype=torch.float32, device=device)
        self.actions_red = torch.zeros(buffer_size, dtype=torch.long, device=device)
        self.actions_blue = torch.zeros(buffer_size, dtype=torch.long, device=device)
        self.masks_red = torch.zeros((buffer_size, NUM_HAND_ACTIONS), dtype=torch.bool, device=device)
        self.masks_blue = torch.zeros((buffer_size, NUM_HAND_ACTIONS), dtype=torch.bool, device=device)
        self.log_probs = torch.zeros(buffer_size, dtype=torch.float32, device=device)
        self.rewards = torch.zeros(buffer_size, dtype=torch.float32, device=device)
        self.values = torch.zeros(buffer_size, dtype=torch.float32, device=device)
        self.dones = torch.zeros(buffer_size, dtype=torch.float32, device=device)
        self.advantages = torch.zeros(buffer_size, dtype=torch.float32, device=device)
        self.returns = torch.zeros(buffer_size, dtype=torch.float32, device=device)

    def add(
        self,
        obs: np.ndarray,
        act_red: int,
        act_blue: int,
        mask_red: np.ndarray,
        mask_blue: np.ndarray,
        log_prob: float,
        reward: float,
        value: float,
        done: bool,
    ) -> None:
        if self.ptr >= self.buffer_size:
            return
        self.observations[self.ptr] = torch.tensor(obs, dtype=torch.float32, device=self.device)
        self.actions_red[self.ptr] = act_red
        self.actions_blue[self.ptr] = act_blue
        self.masks_red[self.ptr] = torch.tensor(mask_red, dtype=torch.bool, device=self.device)
        self.masks_blue[self.ptr] = torch.tensor(mask_blue, dtype=torch.bool, device=self.device)
        self.log_probs[self.ptr] = log_prob
        self.rewards[self.ptr] = reward
        self.values[self.ptr] = value
        self.dones[self.ptr] = float(done)
        self.ptr += 1

    def compute_gae(self, last_value: float, gamma: float = 0.99, gae_lambda: float = 0.95) -> None:
        """Compute Generalized Advantage Estimation (GAE)."""
        last_gae = 0.0
        for t in reversed(range(self.ptr)):
            if t == self.ptr - 1:
                next_val = last_value
                next_done = 0.0
            else:
                next_val = self.values[t + 1]
                next_done = self.dones[t]

            delta = self.rewards[t] + gamma * next_val * (1.0 - next_done) - self.values[t]
            last_gae = delta + gamma * gae_lambda * (1.0 - next_done) * last_gae
            self.advantages[t] = last_gae

        self.returns[: self.ptr] = self.advantages[: self.ptr] + self.values[: self.ptr]
        # Normalize advantages
        valid_adv = self.advantages[: self.ptr]
        self.advantages[: self.ptr] = (valid_adv - valid_adv.mean()) / (valid_adv.std() + 1e-8)

    def reset(self) -> None:
        self.ptr = 0


def train_ppo(
    policy: ActorCriticPolicy,
    env: BeatSaberEnv,
    total_timesteps: int = 1000,
    rollout_steps: int = 256,
    ppo_epochs: int = 4,
    batch_size: int = 64,
    lr: float = 3e-4,
    gamma: float = 0.99,
    gae_lambda: float = 0.95,
    clip_range: float = 0.2,
    ent_coef: float = 0.01,
    vf_coef: float = 0.5,
    device: str = "cpu",
    save_path: Optional[Path] = None,
) -> Dict[str, float]:
    policy.to(device)
    optimizer = optim.Adam(policy.parameters(), lr=lr, eps=1e-5)
    buffer = RolloutBuffer(buffer_size=rollout_steps, obs_dim=env.obs_dim, device=device)

    obs, _ = env.reset()
    timesteps_done = 0
    iteration = 0
    history: Dict[str, List[float]] = {"ep_return": [], "policy_loss": [], "value_loss": []}

    while timesteps_done < total_timesteps:
        buffer.reset()
        ep_rewards = []
        current_ep_reward = 0.0

        policy.eval()
        for step in range(rollout_steps):
            mask_red, mask_blue = env.action_masks()
            t_obs = torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
            t_mask_r = torch.tensor(mask_red, dtype=torch.bool, device=device).unsqueeze(0)
            t_mask_b = torch.tensor(mask_blue, dtype=torch.bool, device=device).unsqueeze(0)

            with torch.no_grad():
                (act_red, act_blue), log_prob, _, val = policy.get_action_and_value(
                    t_obs, mask_red=t_mask_r, mask_blue=t_mask_b
                )

            a_r = int(act_red.item())
            a_b = int(act_blue.item())

            next_obs, reward, terminated, truncated, info = env.step((a_r, a_b))
            current_ep_reward += reward

            buffer.add(
                obs=obs,
                act_red=a_r,
                act_blue=a_b,
                mask_red=mask_red,
                mask_blue=mask_blue,
                log_prob=float(log_prob.item()),
                reward=reward,
                value=float(val.item()),
                done=terminated or truncated,
            )

            obs = next_obs
            timesteps_done += 1

            if terminated or truncated:
                ep_rewards.append(current_ep_reward)
                current_ep_reward = 0.0
                obs, _ = env.reset()

        with torch.no_grad():
            t_obs = torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
            _, _, _, last_val = policy.get_action_and_value(t_obs)
            last_value = float(last_val.item())

        buffer.compute_gae(last_value=last_value, gamma=gamma, gae_lambda=gae_lambda)

        # Optimize policy & value network
        policy.train()
        total_p_loss = 0.0
        total_v_loss = 0.0
        n_updates = 0

        indices = np.arange(buffer.ptr)
        for epoch in range(ppo_epochs):
            np.random.shuffle(indices)
            for start in range(0, len(indices), batch_size):
                end = start + batch_size
                mb_idx = indices[start:end]

                mb_obs = buffer.observations[mb_idx]
                mb_act = (buffer.actions_red[mb_idx], buffer.actions_blue[mb_idx])
                mb_mask_r = buffer.masks_red[mb_idx]
                mb_mask_b = buffer.masks_blue[mb_idx]
                mb_old_log_prob = buffer.log_probs[mb_idx]
                mb_adv = buffer.advantages[mb_idx]
                mb_returns = buffer.returns[mb_idx]

                _, new_log_prob, entropy, new_value = policy.get_action_and_value(
                    mb_obs,
                    action=mb_act,
                    mask_red=mb_mask_r,
                    mask_blue=mb_mask_b,
                )

                # Policy Loss (PPO Clipped surrogate)
                ratio = torch.exp(new_log_prob - mb_old_log_prob)
                surr1 = ratio * mb_adv
                surr2 = torch.clamp(ratio, 1.0 - clip_range, 1.0 + clip_range) * mb_adv
                policy_loss = -torch.min(surr1, surr2).mean()

                # Value Loss
                value_loss = 0.5 * ((new_value - mb_returns) ** 2).mean()

                # Entropy Loss
                entropy_loss = -entropy.mean()

                total_loss = policy_loss + vf_coef * value_loss + ent_coef * entropy_loss

                optimizer.zero_grad()
                total_loss.backward()
                torch.nn.utils.clip_grad_norm_(policy.parameters(), max_norm=0.5)
                optimizer.step()

                total_p_loss += float(policy_loss.item())
                total_v_loss += float(value_loss.item())
                n_updates += 1

        iteration += 1
        avg_p_loss = total_p_loss / max(1, n_updates)
        avg_v_loss = total_v_loss / max(1, n_updates)
        mean_reward = np.mean(ep_rewards) if ep_rewards else current_ep_reward

        print(
            f"Iter {iteration:3d} | Timesteps: {timesteps_done:5d}/{total_timesteps:5d} | "
            f"Mean Ep Reward: {mean_reward:6.2f} | Policy Loss: {avg_p_loss:.4f} | Value Loss: {avg_v_loss:.4f}",
            flush=True,
        )

    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(policy.state_dict(), save_path)
        print(f"Saved trained PPO policy to {save_path}", flush=True)

    return {
        "final_mean_reward": float(np.mean(ep_rewards) if ep_rewards else current_ep_reward),
        "policy_loss": avg_p_loss,
        "value_loss": avg_v_loss,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train BeatForge Actor-Critic Policy via PPO")
    parser.add_argument("--timesteps", type=int, default=1024)
    parser.add_argument("--rollout-steps", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--resume", action="store_true", default=True, help="Resume from existing checkpoint if available")
    parser.add_argument("--out", type=Path, default=Path("data/models/ppo_policy.pt"))
    args = parser.parse_args()

    env = BeatSaberEnv(difficulty="Expert")
    policy = ActorCriticPolicy()
    if args.resume and args.out.is_file():
        try:
            policy.load_state_dict(torch.load(args.out, map_location="cpu"))
            print(f"Resumed policy weights from {args.out}", flush=True)
        except Exception as e:
            print(f"Could not load checkpoint: {e}, starting from scratch", flush=True)

    train_ppo(
        policy=policy,
        env=env,
        total_timesteps=args.timesteps,
        rollout_steps=args.rollout_steps,
        ppo_epochs=args.epochs,
        lr=args.lr,
        save_path=args.out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
