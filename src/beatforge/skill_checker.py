"""SUPERSEDED — no importer in the current tree; the Codex reviewer in
beatforge.providers replaced this bridge (see docs/ARCHITECTURE.md).

Bridge generated maps to the bundled premium Beat Saber validation skill."""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "skills" / "beat-saber-mapping" / "scripts" / "validate_map.py"
CONNECTOR_LABELS = {
    "codex": "OpenAI Codex",
}


@lru_cache(maxsize=1)
def _validator() -> ModuleType:
    if not VALIDATOR_PATH.is_file():
        raise RuntimeError("The premium Beat Saber validation skill is missing")
    scripts_dir = str(VALIDATOR_PATH.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "beatforge_premium_validator", VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("The premium Beat Saber validator could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _critic_findings(critic: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for difficulty, details in critic.get("difficulties", {}).items():
        for check in details.get("checks", []):
            if check.get("passed") is False:
                findings.append(
                    {
                        "severity": "advisory",
                        "code": "CRITIC_CHECK_FAILED",
                        "message": f"{check.get('name', 'Critic check')}: {check.get('detail', 'failed')}",
                        "file": difficulty,
                        "beat": None,
                    }
                )
    spread = critic.get("spread") or {}
    for issue in spread.get("issues", []):
        findings.append(
            {
                "severity": "advisory",
                "code": "DIFFICULTY_SPREAD_GAP",
                "message": (
                    f"{issue.get('lower', 'Lower difficulty')} contains "
                    f"{issue.get('missing_count', 0)} beat positions missing from "
                    f"{issue.get('higher', 'the next difficulty')}"
                ),
                "file": None,
                "beat": None,
            }
        )
    return findings


def check_generated_map(
    map_folder: Path,
    critic_path: Path | None = None,
    connector: str = "codex",
) -> dict[str, Any]:
    if connector not in CONNECTOR_LABELS:
        raise ValueError(f"Unknown AI connector: {connector}")
    validator = _validator()
    report = validator.validate_package(Path(map_folder)).to_dict()
    critic: dict[str, Any] = {}
    if critic_path and critic_path.is_file():
        critic = json.loads(critic_path.read_text(encoding="utf-8"))
    advisories = _critic_findings(critic)
    errors = list(report.get("errors", []))
    warnings = list(report.get("warnings", []))
    report.update(
        {
            "checker": "beat-saber-mapping",
            "checkerLabel": f"{CONNECTOR_LABELS[connector]} premium Beat Saber checker",
            "connector": connector,
            "connectorLabel": CONNECTOR_LABELS[connector],
            "advisories": advisories,
            "summary": {
                "errors": len(errors),
                "warnings": len(warnings),
                "advisories": len(advisories),
                "totalFindings": len(errors) + len(warnings) + len(advisories),
            },
        }
    )
    return report
