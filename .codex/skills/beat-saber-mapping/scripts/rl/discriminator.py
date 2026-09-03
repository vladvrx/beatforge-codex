#!/usr/bin/env python3
"""Style Discriminator & Preference Reward Model for Modern Official Maps."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
try:
    from .environment import NUM_HAND_ACTIONS
except ImportError:
    from rl.environment import NUM_HAND_ACTIONS


class StyleDiscriminator(nn.Module):
    """Discriminates post-2022 official style sequences from amateur/unrated patterns."""

    def __init__(
        self,
        obs_dim: int = 69,
        action_dim: int = NUM_HAND_ACTIONS,
        hidden_dim: int = 128,
    ):
        super().__init__()
        self.obs_dim = obs_dim
        self.action_dim = action_dim

        self.red_embed = nn.Embedding(action_dim, 32)
        self.blue_embed = nn.Embedding(action_dim, 32)

        input_dim = obs_dim + 32 + 32
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.LeakyReLU(0.2),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.LayerNorm(hidden_dim // 2),
            nn.LeakyReLU(0.2),
            nn.Linear(hidden_dim // 2, 1),
        )

    def forward(
        self,
        obs: torch.Tensor,
        act_red: torch.Tensor,
        act_blue: torch.Tensor,
    ) -> torch.Tensor:
        """Return raw classification logit (positive = official post-2022 style)."""
        r_emb = self.red_embed(act_red)
        b_emb = self.blue_embed(act_blue)
        x = torch.cat([obs, r_emb, b_emb], dim=-1)
        return self.net(x).squeeze(-1)

    def compute_style_reward(
        self,
        obs: torch.Tensor,
        act_red: torch.Tensor,
        act_blue: torch.Tensor,
    ) -> torch.Tensor:
        """Compute GAN-style reward: log D(s, a) - log (1 - D(s, a)) = logit(s, a)."""
        self.eval()
        with torch.no_grad():
            logit = self.forward(obs, act_red, act_blue)
            return torch.clamp(logit, min=-3.0, max=3.0)
