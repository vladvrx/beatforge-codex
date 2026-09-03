"""LEGACY — critic for the pre-premium pipeline. The studio pipeline validates
through skills/beat-saber-mapping/scripts/validate_map.py (see
docs/ARCHITECTURE.md). Kept only for the legacy CLI and its tests.

Self-critique: score generated charts against corpus-derived standards.

Uses the same metric math as tools/corpus/fingerprint.py so Beatforge output is
measured exactly like the curated maps it was tuned on, then checks each
difficulty against docs/style-study.md medians and anti-patterns.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from beatforge.chart import Chart, DifficultyChart
from beatforge.styles import DIFFICULTIES, FLOW_NEXT, STYLES

RIGHTWARD = {3, 5, 7}
LEFTWARD = {2, 4, 6}


def fingerprint_diff(
    diff: DifficultyChart, bpm: float, duration: float
) -> dict[str, Any]:
    notes = sorted(diff.notes, key=lambda n: (n.beat, n.color))
    n = len(notes)
    dur = duration if duration and duration > 1 else 1.0
    out: dict[str, Any] = {"note_count": n}
    out["nps"] = n / dur

    by_beat: dict[int, list] = defaultdict(list)
    for note in notes:
        by_beat[int(round(note.beat * 8))].append(note)
    jump_notes = 0
    for group in by_beat.values():
        colors = {x.color for x in group}
        if 0 in colors and 1 in colors:
            jump_notes += sum(1 for x in group if x.color in (0, 1))
    out["jump_ratio"] = jump_notes / n if n else 0.0

    left = [x for x in notes if x.color == 0]
    right = [x for x in notes if x.color == 1]
    out["hand_balance"] = len(left) / n if n else 0.5

    min_gap = 999.0
    stream_gaps: list[float] = []
    for seq in (left, right):
        gs = [
            seq[i].beat - seq[i - 1].beat
            for i in range(1, len(seq))
            if seq[i].beat - seq[i - 1].beat > 1e-6
        ]
        if gs:
            min_gap = min(min_gap, min(gs))
            stream_gaps.extend(gs)
    out["min_hand_gap_beats"] = min_gap if min_gap < 900 else 0.0
    out["stream_ratio"] = (
        sum(1 for g in stream_gaps if g <= 0.26) / len(stream_gaps)
        if stream_gaps
        else 0.0
    )

    alts = sames = 0
    max_color_streak = 0
    color_streak = 0
    streak_color: int | None = None
    # Judge color rhythm by timing groups. A red/blue double is one event and
    # must not inflate a same-color streak because of sort order.
    color_events: list[int | None] = []
    for key in sorted(by_beat):
        colors = {note.color for note in by_beat[key] if note.color in (0, 1)}
        color_events.append(next(iter(colors)) if len(colors) == 1 else None)
    previous_single: int | None = None
    for color in color_events:
        if color is None:
            color_streak = 0
            streak_color = None
            continue
        if color == streak_color:
            color_streak += 1
        else:
            streak_color = color
            color_streak = 1
        max_color_streak = max(max_color_streak, color_streak)
        if previous_single is not None:
            if color != previous_single:
                alts += 1
            else:
                sames += 1
        previous_single = color
    out["hand_alt_ratio"] = alts / (alts + sames) if alts + sames else 0.5
    out["max_color_streak"] = max_color_streak

    flow = reset = 0
    max_chain = 0
    for seq in (left, right):
        chain = 0
        prev: Any = None
        for cur in seq:
            if prev is not None and cur.beat - prev.beat <= 4:
                if cur.cut_dir == prev.cut_dir and cur.cut_dir != 8:
                    reset += 1
                    chain += 1
                    max_chain = max(max_chain, chain)
                else:
                    chain = 1
                    if cur.cut_dir in FLOW_NEXT.get(prev.cut_dir, []):
                        flow += 1
            else:
                chain = 1
            prev = cur
    denom = flow + reset
    out["flow_ratio"] = flow / denom if denom else 0.5
    out["reset_ratio"] = reset / denom if denom else 0.0
    out["max_reset_chain"] = max_chain

    out["dot_ratio"] = sum(1 for x in notes if x.cut_dir == 8) / n if n else 0.0
    cross = sum(
        1
        for x in notes
        if (x.color == 0 and x.lane >= 2) or (x.color == 1 and x.lane <= 1)
    )
    out["cross_ratio"] = cross / n if n else 0.0

    bins = 16
    curve = [0.0] * bins
    if notes:
        t0 = notes[0].beat
        span = max(notes[-1].beat - t0, 1e-6)
        for x in notes:
            idx = min(bins - 1, int((x.beat - t0) / span * bins))
            curve[idx] += 1
        mx = max(curve) or 1.0
        curve = [c / mx for c in curve]
    mean_c = sum(curve) / bins
    var = sum((c - mean_c) ** 2 for c in curve) / bins
    out["section_contrast"] = math.sqrt(var) / (mean_c + 1e-8)

    grams: Counter = Counter()
    tokens = [(x.color, x.cut_dir, x.lane, x.row) for x in notes]
    for i in range(len(tokens) - 3):
        grams[tuple(tokens[i : i + 4])] += 1
    reused = sum(c for c in grams.values() if c >= 2)
    out["motif_reuse"] = reused / max(len(grams), 1)

    face = 0
    for group in by_beat.values():
        reds = [x for x in group if x.color == 0]
        blues = [x for x in group if x.color == 1]
        for r in reds:
            for b in blues:
                if (
                    r.cut_dir in RIGHTWARD
                    and r.lane >= 1
                    and b.cut_dir in LEFTWARD
                    and b.lane <= 2
                    and 0 < b.lane - r.lane <= 2
                ):
                    face += 1
    out["face_plants"] = face

    cells: Counter = Counter((round(x.beat, 3), x.lane, x.row) for x in notes)
    out["stacked_cells"] = sum(c - 1 for c in cells.values() if c > 1)

    out["first_note_t"] = min((float(x.t) for x in notes), default=999.0)
    out["last_note_t"] = max((float(x.t) for x in notes), default=-999.0)

    near = 0
    first_bomb: float | None = None
    for b in diff.bombs:
        tb = b.beat * 60.0 / bpm
        if first_bomb is None or tb < first_bomb:
            first_bomb = tb
        for x in notes:
            db = abs(x.beat - b.beat)
            if db < 1.25 and (x.lane, x.row) == (b.lane, b.row):
                near += 1
                break
            if db < 0.5 and abs(x.lane - b.lane) <= 1 and x.row == b.row:
                near += 1
                break
    out["bombs_near_notes"] = near
    out["bomb_count"] = len(diff.bombs)
    out["first_bomb_t"] = first_bomb

    hit = 0
    invalid_walls = 0
    for w in diff.obstacles:
        if not (
            w.duration_beats > 0
            and 0 <= w.lane <= 3
            and 1 <= w.width <= 4
            and w.lane + w.width <= 4
            and 0 <= w.row <= 2
            and 1 <= w.height <= 5
            and w.row + w.height <= 5
            and not (w.lane == 0 and w.width == 4 and w.row == 0 and w.height >= 5)
        ):
            invalid_walls += 1
        w0, w1 = w.beat - 0.5, w.beat + w.duration_beats + 0.5
        lanes = set(range(w.lane, w.lane + max(1, w.width)))
        for x in notes:
            vertical_conflict = x.row >= 2 if w.row >= 2 else (w.height >= 3 or x.row == 0)
            if (
                w0 <= x.beat <= w1
                and x.lane in lanes
                and vertical_conflict
            ):
                hit += 1
                break
    out["walls_hit_notes"] = hit
    out["wall_count"] = len(diff.obstacles)
    out["invalid_walls"] = invalid_walls

    invalid_arcs = 0
    obstructed_arcs = 0
    overlapping_arcs = 0
    heads = {
        (round(note.beat, 3), note.lane, note.row, note.hand)
        for note in notes
    }
    last_tail = {"left": -999.0, "right": -999.0}
    for arc in sorted(diff.arcs, key=lambda item: (item.beat, item.hand)):
        valid = (
            arc.hand in ("left", "right")
            and arc.tail_beat > arc.beat
            and 0 <= arc.lane <= 3
            and 0 <= arc.tail_lane <= 3
            and 0 <= arc.row <= 2
            and 0 <= arc.tail_row <= 2
            and (round(arc.beat, 3), arc.lane, arc.row, arc.hand) in heads
        )
        if not valid:
            invalid_arcs += 1
        if any(
            note.hand == arc.hand
            and arc.beat + 0.02 < note.beat < arc.tail_beat - 0.02
            for note in notes
        ):
            obstructed_arcs += 1
        if arc.beat < last_tail.get(arc.hand, -999.0) - 0.02:
            overlapping_arcs += 1
        last_tail[arc.hand] = max(last_tail.get(arc.hand, -999.0), arc.tail_beat)
    out["arc_count"] = len(diff.arcs)
    out["invalid_arcs"] = invalid_arcs
    out["obstructed_arcs"] = obstructed_arcs
    out["overlapping_arcs"] = overlapping_arcs

    # Extended vocabulary metrics (corpus rescan v2)
    same_col = col_pairs = 0
    for seq in (left, right):
        for i in range(1, len(seq)):
            if seq[i].beat - seq[i - 1].beat > 4:
                continue
            col_pairs += 1
            if seq[i].lane == seq[i - 1].lane:
                same_col += 1
    out["same_col_repeat"] = same_col / col_pairs if col_pairs else 0.0

    cut_counts = Counter(x.cut_dir for x in notes)
    if n and len(cut_counts) > 1:
        ent = -sum((c / n) * math.log(c / n + 1e-9) for c in cut_counts.values())
        out["cut_entropy"] = ent / math.log(9)
    else:
        out["cut_entropy"] = 0.0

    changes = pairs2 = 0
    for seq in (left, right):
        for i in range(1, len(seq)):
            pairs2 += 1
            if seq[i].lane != seq[i - 1].lane:
                changes += 1
    out["lane_change_rate"] = changes / pairs2 if pairs2 else 0.0

    triplets = sum(
        1
        for x in notes
        if abs(x.beat * 3 - round(x.beat * 3)) < 0.12
        and abs(x.beat * 4 - round(x.beat * 4)) > 0.1
    )
    out["triplet_ratio"] = triplets / n if n else 0.0

    # Spatial + grid features (match tools/corpus/fingerprint.py definitions)
    row_hist = [0.0, 0.0, 0.0]
    col_hist = [0.0, 0.0, 0.0, 0.0]
    for x in notes:
        row_hist[max(0, min(2, x.row))] += 1
        col_hist[max(0, min(3, x.lane))] += 1
    out["row_hist"] = [round(c / n, 4) for c in row_hist] if n else row_hist
    out["col_hist"] = [round(c / n, 4) for c in col_hist] if n else col_hist

    def _travel(seq):
        if len(seq) < 2:
            return 0.0
        dist = 0.0
        for i in range(1, len(seq)):
            dist += math.hypot(seq[i].lane - seq[i - 1].lane, seq[i].row - seq[i - 1].row)
        return dist / (len(seq) - 1)

    out["spatial_travel"] = (_travel(left) + _travel(right)) / 2
    large_vertical = vertical_pairs = 0
    for seq in (left, right):
        for i in range(1, len(seq)):
            vertical_pairs += 1
            if abs(seq[i].row - seq[i - 1].row) >= 2:
                large_vertical += 1
    out["large_vertical_jump_ratio"] = (
        large_vertical / vertical_pairs if vertical_pairs else 0.0
    )

    ioi_bins = (0.125, 0.25, 0.333, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
    times = sorted({round(x.beat, 3) for x in notes})
    ioi: Counter = Counter()
    for i in range(1, len(times)):
        delta = times[i] - times[i - 1]
        if delta <= 0:
            continue
        best = min(ioi_bins, key=lambda b: abs(b - delta))
        key = "4+" if delta > 4.5 else str(best)
        ioi[key] += 1
    total_ioi = sum(ioi.values()) or 1
    out["ioi_hist"] = {k: round(v / total_ioi, 4) for k, v in sorted(ioi.items())}

    return out


def _spread_check(chart: Chart) -> dict[str, Any]:
    """Rule 9: lower difficulties must nest inside higher ones."""
    order = sorted(
        chart.difficulties,
        key=lambda n: DIFFICULTIES[n].rank if n in DIFFICULTIES else 0,
    )
    beats = {
        name: {round(n.beat, 3) for n in chart.difficulties[name].notes}
        for name in order
    }
    issues: list[dict[str, Any]] = []
    for i in range(len(order) - 1):
        lo, hi = order[i], order[i + 1]
        missing = beats[lo] - beats[hi]
        if missing:
            issues.append(
                {"lower": lo, "higher": hi, "missing_count": len(missing)}
            )
    return {"order": order, "consistent": not issues, "issues": issues}


def run_critic(chart: Chart, progress=None) -> dict[str, Any]:
    """Score every difficulty; returns a JSON-ready report dict."""
    style = STYLES.get(chart.style) or STYLES["flow"]
    diffs_out: dict[str, Any] = {}
    worst = 1.0
    for name, diff in chart.difficulties.items():
        if progress:
            progress("critic", f"scoring {name}")
        budget = DIFFICULTIES.get(name)
        fp = fingerprint_diff(diff, chart.bpm, chart.duration)
        section_density: list[dict[str, float | str]] = []
        ordered_sections = sorted(chart.sections, key=lambda section: section.t)
        for index, section in enumerate(ordered_sections):
            end = (
                ordered_sections[index + 1].t
                if index + 1 < len(ordered_sections)
                else chart.duration
            )
            span = max(0.0, end - section.t)
            if span < 1.0:
                continue
            count = sum(1 for note in diff.notes if section.t <= note.t < end)
            section_density.append(
                {
                    "label": section.label,
                    "energy": round(float(section.energy), 4),
                    "nps": round(count / span, 4),
                }
            )
        fp["section_density"] = section_density
        checks: list[dict[str, Any]] = []

        def check(cname: str, passed: bool, value: Any, detail: str,
                  severity: str = "soft") -> None:
            checks.append(
                {
                    "name": cname,
                    "passed": bool(passed),
                    "value": value,
                    "detail": detail,
                    "severity": severity,
                }
            )

        if fp["note_count"] == 0:
            check("has_notes", False, 0, "no notes placed")
        else:
            target = budget.target_nps * style.density_scale if budget else 4.0
            ratio = fp["nps"] / target if target else 0.0
            check(
                "nps_band",
                0.78 <= ratio <= 1.30,
                round(ratio, 3),
                f"nps {fp['nps']:.2f} vs target {target:.2f}",
            )
            # Lower tiers use simpler cut vocabularies; scale the floor.
            flow_floor = 0.45 if style.name == "tech" else (
                0.70 if (budget and budget.rank <= 3) else 0.85
            )
            check(
                "flow_floor",
                fp["flow_ratio"] >= flow_floor,
                round(fp["flow_ratio"], 3),
                f">= {flow_floor}",
            )
            reset_cap = 0.60 if style.name == "tech" else 0.15
            check(
                "reset_ceiling",
                fp["reset_ratio"] <= reset_cap,
                round(fp["reset_ratio"], 3),
                f"<= {reset_cap}",
            )
            check(
                "cross_ceiling",
                fp["cross_ratio"] <= 0.35,
                round(fp["cross_ratio"], 3),
                "<= 0.35",
            )
            jump_floor = 0.0
            check(
                "jump_band",
                jump_floor <= fp["jump_ratio"] <= 0.60,
                round(fp["jump_ratio"], 3),
                f"in [{jump_floor}, 0.60]",
            )
            check(
                "dot_ceiling",
                fp["dot_ratio"] <= 0.12,
                round(fp["dot_ratio"], 3),
                "<= 0.12",
            )
            check(
                "balance_band",
                0.42 <= fp["hand_balance"] <= 0.58,
                round(fp["hand_balance"], 3),
                "in [0.42, 0.58]",
            )
            alt_floor = 0.62 if budget and budget.rank >= 5 else 0.52
            check(
                "hand_alternation",
                fp["hand_alt_ratio"] >= alt_floor and fp["max_color_streak"] <= 3,
                {
                    "ratio": round(fp["hand_alt_ratio"], 3),
                    "max_streak": fp["max_color_streak"],
                },
                f"alternation >= {alt_floor} and no color streak over 3",
                severity="hard",
            )
            gap_floor = (budget.min_hand_gap_beats - 0.02) if budget else 0.23
            check(
                "gap_floor",
                fp["min_hand_gap_beats"] >= gap_floor,
                round(fp["min_hand_gap_beats"], 3),
                f">= {gap_floor:.2f}",
            )
            check(
                "contrast_floor",
                fp["section_contrast"] >= 0.08,
                round(fp["section_contrast"], 3),
                ">= 0.08",
            )
            check(
                "no_face_plants",
                fp["face_plants"] == 0,
                fp["face_plants"],
                "== 0",
                severity="hard",
            )
            chain_cap = 4 if style.name == "tech" else 2
            check(
                "chain_ceiling",
                fp["max_reset_chain"] <= chain_cap,
                fp["max_reset_chain"],
                f"<= {chain_cap}",
                severity="hard",
            )
            check(
                "no_stacked_cells",
                fp["stacked_cells"] == 0,
                fp["stacked_cells"],
                "== 0",
                severity="hard",
            )
            first_beat_t = float(chart.beats[0]) if chart.beats else 0.0
            last_beat_t = float(chart.beats[-1]) if chart.beats else chart.duration
            beat_seconds = 60.0 / max(chart.bpm, 1.0)
            check(
                "opening_coverage",
                fp["first_note_t"] <= first_beat_t + 0.12,
                round(float(fp["first_note_t"]), 3),
                f"first note on first detected beat ({first_beat_t:.3f}s)",
                severity="hard",
            )
            check(
                "ending_coverage",
                fp["last_note_t"] >= last_beat_t - 0.25 * beat_seconds,
                round(float(fp["last_note_t"]), 3),
                f"last note reaches final detected beat ({last_beat_t:.3f}s)",
                severity="hard",
            )
            bomb_ok = fp["bombs_near_notes"] == 0 and (
                fp["first_bomb_t"] is None or float(fp["first_bomb_t"]) >= 2.5
            )
            check("bomb_safety", bomb_ok, fp["bombs_near_notes"], "clear of swings")
            check(
                "wall_safety",
                fp["walls_hit_notes"] == 0,
                fp["walls_hit_notes"],
                "== 0",
            )
            if budget and budget.use_walls:
                energy_floor = 0.30 if chart.style == "chill" else 0.48
                active_sections = sum(
                    1
                    for section in chart.sections
                    if section.label in ("drop", "build", "body", "verse")
                    and section.energy
                    >= energy_floor + (0.08 if section.label == "verse" else 0.0)
                )
                wall_floor = min(4, max(1, active_sections))
                check(
                    "wall_presence",
                    fp["wall_count"] >= wall_floor,
                    fp["wall_count"],
                    f">= {wall_floor} readable wall cues",
                )
            check(
                "wall_geometry",
                fp["invalid_walls"] == 0,
                fp["invalid_walls"],
                "valid vanilla bounds with a safe head position",
                severity="hard",
            )
            check(
                "arc_validity",
                fp["invalid_arcs"] == 0,
                fp["invalid_arcs"],
                "ordered endpoints and a matching playable head note",
                severity="hard",
            )
            arc_clutter = fp["obstructed_arcs"] + fp["overlapping_arcs"]
            check(
                "arc_readability",
                arc_clutter == 0,
                arc_clutter,
                "no same-hand notes inside arcs or overlapping hand paths",
            )
            check(
                "cut_entropy_band",
                0.55 <= float(fp.get("cut_entropy") or 0) <= 1.0,
                round(float(fp.get("cut_entropy") or 0), 3),
                "in [0.55, 1.0]",
            )
            check(
                "lane_change_band",
                0.45 <= float(fp.get("lane_change_rate") or 0) <= 0.95,
                round(float(fp.get("lane_change_rate") or 0), 3),
                "in [0.45, 0.95]",
            )
            check(
                "same_col_ceiling",
                float(fp.get("same_col_repeat") or 0) <= 0.5,
                round(float(fp.get("same_col_repeat") or 0), 3),
                "<= 0.5",
                severity="hard",
            )
            sixteenth_share = float((fp.get("ioi_hist") or {}).get("0.25", 0.0))
            sixteenth_cap = 0.58 if budget and budget.rank >= 9 else 0.35
            check(
                "sixteenth_ceiling",
                sixteenth_share <= sixteenth_cap,
                round(sixteenth_share, 3),
                f"<= {sixteenth_cap} of event intervals",
                severity="hard",
            )
            check(
                "vertical_jump_ceiling",
                float(fp.get("large_vertical_jump_ratio") or 0.0) <= 0.30,
                round(float(fp.get("large_vertical_jump_ratio") or 0.0), 3),
                "large row jumps <= 0.30",
                severity="hard",
            )
            if len(section_density) >= 2:
                low = min(section_density, key=lambda item: float(item["energy"]))
                high = max(section_density, key=lambda item: float(item["energy"]))
                tracks_energy = float(high["nps"]) + 0.15 >= float(low["nps"]) * 0.90
                check(
                    "energy_density_tracking",
                    tracks_energy,
                    {"low": low, "high": high},
                    "highest-energy section is not thinner than the quietest section",
                    severity="hard",
                )
            if budget and budget.rank >= 5:
                rows = fp.get("row_hist") or [0.0, 0.0, 0.0]
                row_ok = len(rows) >= 3 and rows[0] <= 0.78 and rows[2] >= 0.02
                check(
                    "row_spread",
                    row_ok,
                    [round(float(v), 3) for v in rows],
                    "bottom row <= 0.78 and top-row accents >= 0.02",
                )
            check(
                "triplet_ceiling",
                float(fp.get("triplet_ratio") or 0) <= 0.25,
                round(float(fp.get("triplet_ratio") or 0), 3),
                "<= 0.25 (beat-anchored grids drift off quarter math)",
            )
            motif_floor = 0.05 if style.name == "chill" else 0.05
            check(
                "motif_floor",
                float(fp.get("motif_reuse") or 0) >= motif_floor,
                round(float(fp.get("motif_reuse") or 0), 3),
                f">= {motif_floor} (curated median 0.60 is aspirational)",
            )

        hard_fails = sum(1 for c in checks if not c["passed"] and c.get("severity") == "hard")
        soft = [c for c in checks if c.get("severity") != "hard"]
        passed = sum(1 for c in checks if c["passed"])
        total = len(checks)
        soft_passed = sum(1 for c in soft if c["passed"])
        # Hard failures cap the score; soft checks fill in the rest.
        if hard_fails:
            score = min(
                0.6,
                0.6 * (1 - hard_fails / max(total, 1))
                + 0.4 * (soft_passed / max(len(soft), 1)),
            )
        else:
            score = 0.4 + 0.6 * (soft_passed / max(len(soft), 1))
        worst = min(worst, score)
        diffs_out[name] = {
            "score": round(score, 3),
            "passed": passed,
            "total": total,
            "hard_failures": hard_fails,
            "metrics": {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in fp.items()
            },
            "checks": checks,
        }

    verdict = (
        "clean" if worst >= 0.99 else ("acceptable" if worst >= 0.85 else "needs-work")
    )
    spread = (
        _spread_check(chart) if len(chart.difficulties) > 1 else None
    )
    hard_any = any(
        d.get("hard_failures", 0) > 0 for d in diffs_out.values()
    )
    if hard_any:
        verdict = "needs-work"
    elif spread and not spread["consistent"] and verdict == "clean":
        verdict = "acceptable"
    return {
        "style": chart.style,
        "verdict": verdict,
        "spread": spread,
        "difficulties": diffs_out,
    }
