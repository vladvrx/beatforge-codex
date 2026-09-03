#!/usr/bin/env python3
"""Behavioral Cloning (Supervised Pre-Training) for Beat Saber RL Policy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader

try:
    from .dataset import BeatSaberTrajectoryDataset
    from .models import ActorCriticPolicy
except ImportError:
    from rl.dataset import BeatSaberTrajectoryDataset
    from rl.models import ActorCriticPolicy


def train_bc(
    policy: ActorCriticPolicy,
    dataset: BeatSaberTrajectoryDataset,
    epochs: int = 5,
    batch_size: int = 32,
    lr: float = 1e-3,
    device: str = "cpu",
    save_path: Optional[Path] = None,
) -> Dict[str, float]:
    policy.to(device)
    policy.train()

    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    optimizer = optim.AdamW(policy.parameters(), lr=lr, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss()

    metrics = {"loss": 0.0, "acc_red": 0.0, "acc_blue": 0.0}

    for epoch in range(epochs):
        total_loss = 0.0
        correct_red = 0
        correct_blue = 0
        total_samples = 0

        for batch in loader:
            obs = batch["obs"].to(device)
            target_red = batch["act_red"].to(device)
            target_blue = batch["act_blue"].to(device)
            mask_red = batch["mask_red"].to(device)
            mask_blue = batch["mask_blue"].to(device)

            optimizer.zero_grad()

            logits_red, logits_blue, _ = policy(obs, action_red=target_red)

            # Apply masking
            logits_red = torch.where(mask_red, logits_red, torch.tensor(-1e8, device=device))
            logits_blue = torch.where(mask_blue, logits_blue, torch.tensor(-1e8, device=device))

            loss_red = loss_fn(logits_red, target_red)
            loss_blue = loss_fn(logits_blue, target_blue)
            loss = loss_red + loss_blue

            loss.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), max_norm=1.0)
            optimizer.step()

            total_loss += float(loss.item()) * len(obs)
            pred_red = torch.argmax(logits_red, dim=-1)
            pred_blue = torch.argmax(logits_blue, dim=-1)

            correct_red += int((pred_red == target_red).sum().item())
            correct_blue += int((pred_blue == target_blue).sum().item())
            total_samples += len(obs)

        avg_loss = total_loss / max(1, total_samples)
        acc_red = correct_red / max(1, total_samples)
        acc_blue = correct_blue / max(1, total_samples)

        metrics = {"loss": avg_loss, "acc_red": acc_red, "acc_blue": acc_blue}
        print(f"Epoch {epoch+1}/{epochs} | Loss: {avg_loss:.4f} | Acc Red: {acc_red:.3f} | Acc Blue: {acc_blue:.3f}")

    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(policy.state_dict(), save_path)
        print(f"Saved policy checkpoint to {save_path}")

    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-train BeatForge policy via Behavioral Cloning")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--out", type=Path, default=Path("data/models/bc_policy.pt"))
    parser.add_argument("--synthetic", action="store_true", default=True, help="Use synthetic dataset for verification")
    args = parser.parse_args()

    print("Generating training dataset...")
    dataset = BeatSaberTrajectoryDataset.generate_synthetic(num_songs=8, beats_per_song=64)
    print(f"Dataset ready with {len(dataset)} samples.")

    policy = ActorCriticPolicy()
    train_bc(
        policy=policy,
        dataset=dataset,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        save_path=args.out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
