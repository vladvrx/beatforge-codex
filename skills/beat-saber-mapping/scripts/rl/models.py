#!/usr/bin/env python3
"""PyTorch Actor-Critic Architectures with Autoregressive Dual-Hand Policy & Masking."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions.categorical import Categorical
try:
    from .environment import NUM_HAND_ACTIONS
except ImportError:
    from rl.environment import NUM_HAND_ACTIONS


class BeatForgeAudioEncoder(nn.Module):
    """Encodes stem energy, transients, phase, and lookahead into dense features."""

    def __init__(self, input_dim: int, latent_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.LayerNorm(256),
            nn.GELU(),
            nn.Linear(256, latent_dim),
            nn.LayerNorm(latent_dim),
            nn.GELU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class ActorCriticPolicy(nn.Module):
    """Dual-Hand Autoregressive Actor-Critic Network with Action Masking."""

    def __init__(
        self,
        obs_dim: int = 69,
        action_dim: int = NUM_HAND_ACTIONS,
        hidden_dim: int = 256,
    ):
        super().__init__()
        self.obs_dim = obs_dim
        self.action_dim = action_dim

        # Shared feature extractor / torso
        self.encoder = nn.Sequential(
            nn.Linear(obs_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
        )

        # Actor head for Left Hand (Red)
        self.actor_red = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Linear(hidden_dim // 2, action_dim),
        )

        # Autoregressive Actor head for Right Hand (Blue) conditioned on Left Hand action
        self.red_action_embed = nn.Embedding(action_dim, 32)
        self.actor_blue = nn.Sequential(
            nn.Linear(hidden_dim + 32, hidden_dim // 2),
            nn.GELU(),
            nn.Linear(hidden_dim // 2, action_dim),
        )

        # Value / Critic Head
        self.critic = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Linear(hidden_dim // 2, 1),
        )

    def forward(
        self,
        obs: torch.Tensor,
        action_red: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = self.encoder(obs)
        logits_red = self.actor_red(features)

        if action_red is None:
            # If red action not supplied, use argmax red action to condition blue
            action_red = torch.argmax(logits_red, dim=-1)

        red_emb = self.red_action_embed(action_red)
        blue_in = torch.cat([features, red_emb], dim=-1)
        logits_blue = self.actor_blue(blue_in)

        value = self.critic(features)
        return logits_red, logits_blue, value.squeeze(-1)

    def get_action_and_value(
        self,
        obs: torch.Tensor,
        action: Optional[Tuple[torch.Tensor, torch.Tensor]] = None,
        mask_red: Optional[torch.Tensor] = None,
        mask_blue: Optional[torch.Tensor] = None,
    ) -> Tuple[Tuple[torch.Tensor, torch.Tensor], torch.Tensor, torch.Tensor, torch.Tensor]:
        """Compute action sampling, log probability, entropy, and state value."""
        features = self.encoder(obs)
        logits_red = self.actor_red(features)

        # Apply Action Masking for Red Hand
        if mask_red is not None:
            logits_red = torch.where(mask_red, logits_red, torch.tensor(-1e8, device=logits_red.device))

        dist_red = Categorical(logits=logits_red)

        if action is None:
            act_red = dist_red.sample()
        else:
            act_red = action[0]

        # Condition Blue Hand on sampled Red Action
        red_emb = self.red_action_embed(act_red)
        blue_in = torch.cat([features, red_emb], dim=-1)
        logits_blue = self.actor_blue(blue_in)

        # Apply Action Masking for Blue Hand
        if mask_blue is not None:
            logits_blue = torch.where(mask_blue, logits_blue, torch.tensor(-1e8, device=logits_blue.device))

        dist_blue = Categorical(logits=logits_blue)

        if action is None:
            act_blue = dist_blue.sample()
        else:
            act_blue = action[1]

        log_prob = dist_red.log_prob(act_red) + dist_blue.log_prob(act_blue)
        entropy = dist_red.entropy() + dist_blue.entropy()
        value = self.critic(features).squeeze(-1)

        return (act_red, act_blue), log_prob, entropy, value

    def predict(
        self,
        obs: torch.Tensor,
        mask_red: Optional[torch.Tensor] = None,
        mask_blue: Optional[torch.Tensor] = None,
        deterministic: bool = False,
    ) -> Tuple[int, int]:
        """Inference action selection."""
        self.eval()
        with torch.no_grad():
            if obs.ndim == 1:
                obs = obs.unsqueeze(0)
            if mask_red is not None and mask_red.ndim == 1:
                mask_red = mask_red.unsqueeze(0)
            if mask_blue is not None and mask_blue.ndim == 1:
                mask_blue = mask_blue.unsqueeze(0)

            features = self.encoder(obs)
            logits_red = self.actor_red(features)
            if mask_red is not None:
                logits_red = torch.where(mask_red, logits_red, torch.tensor(-1e8, device=logits_red.device))

            if deterministic:
                act_red = torch.argmax(logits_red, dim=-1)
            else:
                dist_red = Categorical(logits=logits_red)
                act_red = dist_red.sample()

            red_emb = self.red_action_embed(act_red)
            blue_in = torch.cat([features, red_emb], dim=-1)
            logits_blue = self.actor_blue(blue_in)
            if mask_blue is not None:
                logits_blue = torch.where(mask_blue, logits_blue, torch.tensor(-1e8, device=logits_blue.device))

            if deterministic:
                act_blue = torch.argmax(logits_blue, dim=-1)
            else:
                dist_blue = Categorical(logits=logits_blue)
                act_blue = dist_blue.sample()

            return int(act_red.item()), int(act_blue.item())
