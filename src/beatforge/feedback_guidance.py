"""Bounded, opt-in creative suggestions from distinct human playtest reports.

These helpers do not train a model, verify a headset session, or change a plan.
The caller persists human reports and asks the user to apply suggestedPlan.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Any

from .mapping_plan import normalize_mapping_plan


def _timestamp(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    except (ValueError, OverflowError):
        return 0.0


def _section_key(record: dict[str, Any]) -> tuple[float | None, float | None] | None:
    start, end = record.get("startBeat"), record.get("endBeat")
    if start is None and end is None:
        return (None, None)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (start, end)):
        return None
    if start < 0 or end <= start:
        return None
    return (float(start), float(end))


def derive_feedback_adjustment(
    records: list[dict[str, Any]], current_plan: dict[str, Any] | None,
    *, tester: str = "local", difficulty: str | None = None,
) -> dict[str, Any]:
    """One vote per distinct chart, using each chart/section's latest human report."""
    plan = normalize_mapping_plan(current_plan)
    latest: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("source") != "human" or record.get("schemaVersion", 1) != 1:
            continue
        if record.get("tester", "local") != tester or (difficulty is not None and record.get("difficulty") != difficulty):
            continue
        chart_hash = record.get("chartHash")
        section = _section_key(record)
        if not isinstance(chart_hash, str) or not chart_hash or section is None:
            continue
        key = (chart_hash, tester, *section)
        if key not in latest or _timestamp(record.get("createdAt")) >= _timestamp(latest[key].get("createdAt")):
            latest[key] = record
    by_chart: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in latest.values():
        by_chart[record["chartHash"]].append(record)
    count = len(by_chart)
    result: dict[str, Any] = {"suggestedPlan": plan, "evidenceCount": count, "reasons": [], "ready": count >= 3, "changedFields": {}, "nextActions": []}
    if count < 3:
        result["reasons"] = [f"Need reports for at least 3 distinct charts from {tester}; found {count}."]
        return result

    votes: dict[str, int] = defaultdict(int)
    for reports in by_chart.values():
        tags = {tag for report in reports for tag in (report.get("tags") or []) if isinstance(tag, str)}
        dense = bool(tags & {"too_dense", "tiring"})
        sparse = "too_sparse" in tags
        # Conflicting reports within a chart cancel instead of multiplying votes.
        votes["dense"] += int(dense and not sparse)
        votes["sparse"] += int(sparse and not dense)
        votes["repetitive"] += int("repetitive" in tags)
        votes["off_beat"] += int("off_beat" in tags)
        readability = []
        for report in reports:
            ratings = report.get("ratings", {})
            rating = ratings.get("readability") if isinstance(ratings, dict) else None
            if isinstance(rating, (int, float)) and not isinstance(rating, bool) and math.isfinite(rating) and 1 <= rating <= 5:
                readability.append(float(rating))
        votes["awkward"] += int("awkward" in tags or bool(readability and sum(readability) / len(readability) <= 2))

    suggestion = dict(plan)
    reasons: list[str] = []
    density_signal = (votes["dense"] - votes["sparse"]) / count
    if abs(density_signal) >= 1 / 3 and max(votes["dense"], votes["sparse"]) >= 2:
        suggestion["density"] = round(max(0.5, min(1.5, plan["density"] * (1.0 - 0.15 * density_signal))), 4)
        direction = "Lower" if density_signal > 0 else "Raise"
        reasons.append(f"{direction} density slightly: {votes['dense']} charts were reported too dense or tiring and {votes['sparse']} too sparse.")
    if votes["awkward"] >= 2 and votes["awkward"] / count >= 0.5:
        suggestion["intensity"] = round(max(0.5, plan["intensity"] * 0.9), 4)
        reasons.append(f"Try 10% lower intensity: {votes['awkward']} of {count} charts had awkward movement or low reported readability.")
    if votes["repetitive"] >= 2 and votes["repetitive"] / count >= 0.5 and plan["candidateCount"] < 3:
        suggestion["candidateCount"] = plan["candidateCount"] + 1
        reasons.append(f"Compare one more safe candidate: {votes['repetitive']} of {count} charts were reported repetitive.")
    if votes["off_beat"]:
        result["nextActions"].append("Review the sample timing and click track before generating again; density changes cannot repair off-beat notes.")
    result["suggestedPlan"] = normalize_mapping_plan(suggestion)
    result["changedFields"] = {key: {"from": plan[key], "to": suggestion[key]} for key in ("density", "intensity", "candidateCount") if plan[key] != suggestion[key]}
    result["reasons"] = reasons or ["These reports do not establish a consistent creative-control adjustment."]
    return result


def qa_next_actions(qa: dict[str, Any] | None, timing: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Order concrete next checks from existing machine reports; never certify playability."""
    qa = qa or {}
    timing = timing or {}
    actions = []
    errors = qa.get("errors") or []
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        actions.append({"priority": "blocking", "action": "repair_structure", "detail": str(first.get("message") or first.get("code") or "Resolve the structural validation errors before playtesting.")})
    if timing and timing.get("status") != "timing_verified":
        actions.append({"priority": "review", "action": "review_timing", "detail": "Check the click track at the start, middle, and end, then confirm timing anchors."})
    if not errors:
        actions.append({"priority": "playtest", "action": "human_playtest", "detail": "Play the chart slowly, then at full speed, and record flow and readability feedback."})
    return actions
