"""Validated policy weights and portable, non-pickle provenance metadata."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

import torch

from .features import FEATURE_SCHEMA, file_sha256
from .models import ActorCriticPolicy


def load_checkpoint(path: Path, device: str = "cpu") -> tuple[ActorCriticPolicy, dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"RL checkpoint does not exist: {path}")
    try:
        payload = torch.load(path, map_location=device, weights_only=True)
        if not isinstance(payload, dict):
            raise ValueError("checkpoint must contain policy weights")
        wrapped = "state_dict" in payload
        weights = payload["state_dict"] if wrapped else payload
        metadata = dict(payload.get("metadata", {})) if wrapped else {"featureSchema": "legacy-unrecorded"}
        json.dumps(metadata, allow_nan=False)
        for name in ("trainingSourceSha256", "heldOutSourceSha256"):
            if name in metadata:
                hashes = metadata[name]
                if (not isinstance(hashes, list) or not all(isinstance(value, str) and len(value) == 64
                        and all(character in "0123456789abcdef" for character in value) for value in hashes)):
                    raise ValueError(f"Invalid checkpoint lineage field: {name}")
        schema = metadata.get("featureSchema", "legacy-unrecorded")
        if schema not in {FEATURE_SCHEMA, "legacy-unrecorded"}:
            raise ValueError(f"unsupported feature schema: {schema}")
        first = weights["encoder.0.weight"]
        if first.ndim != 2 or first.shape[1] != 69:
            raise ValueError("checkpoint observation dimensions are incompatible with the RL environment")
        with torch.random.fork_rng(devices=[]):
            policy = ActorCriticPolicy(obs_dim=int(first.shape[1]), hidden_dim=int(first.shape[0]))
        if not all(isinstance(value, torch.Tensor) and torch.is_floating_point(value) and torch.isfinite(value).all() for value in weights.values()):
            raise ValueError("checkpoint contains non-finite or invalid weights")
        policy.load_state_dict(weights, strict=True)
    except Exception as exc:
        raise ValueError(f"Invalid RL checkpoint {path.name}: {exc}") from exc
    policy.to(device)
    policy.eval()
    metadata.update({"checkpointFile": path.name, "checkpointSha256": file_sha256(path)})
    return policy, metadata


def save_checkpoint(policy: ActorCriticPolicy, path: Path, metadata: dict[str, Any]) -> None:
    """Replace checkpoints atomically so interruption cannot destroy a prior save."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    torch.save({"state_dict": policy.state_dict(), "metadata": {**metadata, "featureSchema": FEATURE_SCHEMA}}, temporary)
    temporary.replace(path)
