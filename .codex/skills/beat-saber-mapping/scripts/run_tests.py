#!/usr/bin/env python3
"""Run the bundled regression suite with the skill's private dependencies."""

from __future__ import annotations

from pathlib import Path

import beatforge_core  # noqa: F401 - activates the managed dependency path
import pytest


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", str(Path(__file__).resolve().parent.parent / "tests")]))
