"""LEGACY — provenance for the pre-premium pipeline. The studio pipeline writes
its own provenance with releaseGate flags inside generate_map.py (see
docs/ARCHITECTURE.md).

Provenance: report which curated corpus material shaped a generated chart.

Principles only, never note copying. The lineage report names:
- the style family whose measured medians drove placement parameters
- the closest curated relatives by rhythm-profile distance (NN over the
  full Standard-diff index built by tools/corpus/build_index.py)
- how key metrics compare against curated Expert-level medians
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from beatforge.chart import Chart
from beatforge.styles import DIFFICULTIES

ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "data" / "corpus" / "nn_index.json"
FAMILY_PATH = ROOT / "data" / "corpus" / "style_family_medians.json"
MEDIANS_PATH = ROOT / "data" / "corpus" / "style_recommendations.json"

_INDEX: dict[str, Any] | None = None


def _vec_from_metrics(m: dict[str, Any]) -> list[float]:
    """Same feature order as tools/corpus/fingerprint.feature_vector."""
    row = m.get("row_hist") or [0, 0, 0]
    col = m.get("col_hist") or [0, 0, 0, 0]
    ioi = m.get("ioi_hist") or {}
    return [
        float(m.get("nps") or 0),
        float(m.get("jump_ratio") or 0),
        float(m.get("reset_ratio") or 0),
        float(m.get("flow_ratio") or 0),
        float(m.get("cross_ratio") or 0),
        float(m.get("stream_ratio") or 0),
        float(m.get("section_contrast") or 0),
        float(row[2] if len(row) > 2 else 0),
        float(m.get("motif_reuse") or 0),
        float(m.get("bomb_rate") or 0),
        float(m.get("wall_rate") or 0),
        float(m.get("dot_ratio") or 0),
        float(m.get("hand_alt_ratio") or 0),
        float(m.get("spatial_travel") or 0),
        float(col[0] if col else 0),
        float(col[3] if len(col) > 3 else 0),
        float(ioi.get("0.25") or 0),
        float(ioi.get("0.5") or 0),
        float(ioi.get("1.0") or 0),
    ]


def load_index() -> dict[str, Any] | None:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    if not INDEX_PATH.is_file():
        return None
    try:
        _INDEX = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _INDEX


def nearest_relatives(metrics: dict[str, Any], k: int = 5) -> list[dict[str, Any]]:
    """Top-k curated Standard diffs closest to these chart metrics."""
    idx = load_index()
    if not idx or not idx.get("rows"):
        return []
    mean, std = idx["mean"], idx["std"]
    v = _vec_from_metrics(metrics)
    q = [(v[d] - mean[d]) / std[d] for d in range(min(len(v), len(mean)))]
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in idx["rows"]:
        rv = r["vec"]
        dist = 0.0
        for d in range(len(q)):
            diff = (rv[d] - mean[d]) / std[d] - q[d]
            dist += diff * diff
        scored.append((math.sqrt(dist), r))
    scored.sort(key=lambda t: t[0])
    out = []
    for dist, r in scored[:k]:
        out.append(
            {
                "map_id": r.get("map_id"),
                "title": r.get("title"),
                "artist": r.get("artist"),
                "difficulty": r.get("difficulty"),
                "nps": r.get("nps"),
                "tags": r.get("tags") or [],
                "distance": round(dist, 3),
            }
        )
    return out


def build_lineage(chart: Chart, critic_report: dict[str, Any]) -> dict[str, Any]:
    ref_name = None
    best_rank = -1
    for name in chart.difficulties:
        rank = DIFFICULTIES[name].rank if name in DIFFICULTIES else -1
        if rank > best_rank and critic_report.get("difficulties", {}).get(name):
            best_rank, ref_name = rank, name
    if ref_name is None:
        return {}

    metrics = critic_report["difficulties"][ref_name].get("metrics") or {}
    lineage: dict[str, Any] = {
        "style": chart.style,
        "reference_difficulty": ref_name,
    }

    fam_file: dict[str, Any] | None = None
    if FAMILY_PATH.is_file():
        try:
            fam_file = json.loads(FAMILY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fam_file = None
    fam = (fam_file or {}).get(chart.style)
    if fam:
        lineage["style_source"] = {
            "family_size": fam.get("n"),
            "note": (
                f"{chart.style} profile tuned to {fam.get('n')} curated "
                f"Expert+ diffs in that style family"
            ),
            "family_medians": {
                k: fam.get(k)
                for k in (
                    "nps",
                    "jump_ratio",
                    "cross_ratio",
                    "reset_ratio",
                    "stream_ratio",
                    "cut_entropy",
                    "lane_change_rate",
                )
            },
        }

    lineage["relatives"] = nearest_relatives(metrics, k=5)

    med_file: dict[str, Any] | None = None
    if MEDIANS_PATH.is_file():
        try:
            med_file = json.loads(MEDIANS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            med_file = None
    em = (med_file or {}).get("expert_medians")
    if em:
        compare_keys = (
            "flow_ratio",
            "reset_ratio",
            "jump_ratio",
            "cross_ratio",
            "dot_ratio",
            "section_contrast",
            "motif_reuse",
        )
        lineage["vs_corpus"] = [
            {
                "metric": k,
                "chart": round(float(metrics.get(k) or 0), 3),
                "curated_median": em.get(k),
            }
            for k in compare_keys
        ]
    return lineage
