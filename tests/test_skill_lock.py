"""CI contract: portable skill copies and skill-lock stay aligned."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_portable_skill_lock_matches_canonical() -> None:
    path = ROOT / "tools" / "sync_portable_skill.py"
    spec = importlib.util.spec_from_file_location("sync_portable_skill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check() == []
