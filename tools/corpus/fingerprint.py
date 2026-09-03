"""Fingerprints for density, rhythm, hands, flow, space, motifs, jumps/walls."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

try:
    from parse_maps import ParsedBomb, ParsedDifficulty, ParsedMap, ParsedNote, ParsedWall
except ImportError:  # pytest: pythonpath includes tools/
    from corpus.parse_maps import ParsedBomb, ParsedDifficulty, ParsedMap, ParsedNote, ParsedWall

# Swing continuations (same as beatforge.styles.FLOW_NEXT)
FLOW_NEXT: dict[int, list[int]] = {
    0: [1, 6, 7],
    1: [0, 4, 5],
    2: [3, 5, 7],
    3: [2, 4, 6],
    4: [7, 1, 3],
    5: [6, 1, 2],
    6: [5, 0, 3],
    7: [4, 0, 2],
    8: [1, 0, 2, 3],
}

IOI_BINS = (0.125, 0.25, 0.333, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)


def _nearest_ioi(delta: float) -> str:
    if delta <= 0:
        return "0"
    best = min(IOI_BINS, key=lambda b: abs(b - delta))
    if delta > 4.5:
        return "4+"
    return str(best)


def _duration_sec(bpm: float, notes: list[ParsedNote], fallback: float) -> float:
    if fallback and fallback > 1:
        return fallback
    if not notes or bpm <= 0:
        return 1.0
    span = (max(n.beat for n in notes) - min(n.beat for n in notes)) * 60.0 / bpm
    return max(span, 1.0)


def fingerprint_notes(
    notes: list[ParsedNote],
    *,
    bombs: list[ParsedBomb] | None = None,
    walls: list[ParsedWall] | None = None,
    bpm: float = 120.0,
    duration: float = 0.0,
    njs: float = 0.0,
) -> dict[str, Any]:
    bombs = bombs or []
    walls = walls or []
    notes = sorted(notes, key=lambda n: (n.beat, n.color, n.x))
    dur = _duration_sec(bpm, notes, duration)
    n = len(notes)
    empty = n == 0

    nps = n / dur if dur else 0.0

    # Jumps: both colors on the same quantized beat
    by_beat: dict[int, list[ParsedNote]] = defaultdict(list)
    for note in notes:
        by_beat[int(round(note.beat * 8))].append(note)
    jump_notes = 0
    for group in by_beat.values():
        colors = {x.color for x in group}
        if 0 in colors and 1 in colors:
            jump_notes += sum(1 for x in group if x.color in (0, 1))
    jump_ratio = jump_notes / n if n else 0.0

    left = [x for x in notes if x.color == 0]
    right = [x for x in notes if x.color == 1]
    hand_balance = len(left) / n if n else 0.5

    def _hand_gaps(seq: list[ParsedNote]) -> tuple[float, float, float]:
        if len(seq) < 2:
            return 999.0, 0.0, 0.0
        gaps = [seq[i].beat - seq[i - 1].beat for i in range(1, len(seq))]
        pos_gaps = [g for g in gaps if g > 1e-6]
        min_gap = min(pos_gaps) if pos_gaps else 999.0
        stream = sum(1 for g in pos_gaps if g <= 0.26) / len(pos_gaps) if pos_gaps else 0.0
        mean_gap = sum(pos_gaps) / len(pos_gaps) if pos_gaps else 0.0
        return min_gap, stream, mean_gap

    l_min, l_stream, l_mean = _hand_gaps(left)
    r_min, r_stream, r_mean = _hand_gaps(right)
    min_hand_gap = min(l_min, r_min)
    stream_ratio = (l_stream + r_stream) / 2.0

    # Alternation vs same-hand repeats (ignore true doubles)
    alts = sames = 0
    for i in range(1, n):
        if abs(notes[i].beat - notes[i - 1].beat) < 0.02:
            continue
        if notes[i].color != notes[i - 1].color:
            alts += 1
        else:
            sames += 1
    denom = alts + sames
    hand_alt_ratio = alts / denom if denom else 0.5

    flow = reset = 0
    for seq in (left, right):
        for i in range(1, len(seq)):
            prev, cur = seq[i - 1], seq[i]
            if cur.beat - prev.beat > 4:
                continue
            if cur.cut == prev.cut and cur.cut != 8:
                reset += 1
            elif cur.cut in FLOW_NEXT.get(prev.cut, []):
                flow += 1
    flow_denom = flow + reset
    flow_ratio = flow / flow_denom if flow_denom else 0.5
    reset_ratio = reset / flow_denom if flow_denom else 0.0

    dot_ratio = sum(1 for x in notes if x.cut == 8) / n if n else 0.0
    cross = 0
    for x in notes:
        if x.color == 0 and x.x >= 2:
            cross += 1
        if x.color == 1 and x.x <= 1:
            cross += 1
    cross_ratio = cross / n if n else 0.0

    row_hist = [0.0, 0.0, 0.0]
    col_hist = [0.0, 0.0, 0.0, 0.0]
    for x in notes:
        row_hist[max(0, min(2, x.y))] += 1
        col_hist[max(0, min(3, x.x))] += 1
    if n:
        row_hist = [c / n for c in row_hist]
        col_hist = [c / n for c in col_hist]

    # Spatial travel per hand
    def _travel(seq: list[ParsedNote]) -> float:
        if len(seq) < 2:
            return 0.0
        dist = 0.0
        for i in range(1, len(seq)):
            dist += math.hypot(seq[i].x - seq[i - 1].x, seq[i].y - seq[i - 1].y)
        return dist / (len(seq) - 1)

    spatial_travel = (_travel(left) + _travel(right)) / 2.0

    # Rhythm vocabulary
    ioi = Counter()
    times = sorted({round(x.beat, 3) for x in notes})
    for i in range(1, len(times)):
        ioi[_nearest_ioi(times[i] - times[i - 1])] += 1
    ioi_total = sum(ioi.values()) or 1
    ioi_hist = {k: round(v / ioi_total, 4) for k, v in sorted(ioi.items())}

    # Motifs: 4-grams of (color, cut, x, y)
    grams: Counter[tuple] = Counter()
    tokens = [(x.color, x.cut, x.x, x.y) for x in notes]
    for i in range(len(tokens) - 3):
        grams[tuple(tokens[i : i + 4])] += 1
    reused = sum(c for c in grams.values() if c >= 2)
    motif_reuse = reused / max(len(grams), 1) if grams else 0.0

    # Density curve (16 bins of note timeline)
    bins = 16
    curve = [0.0] * bins
    if notes:
        t0 = min(x.beat for x in notes)
        t1 = max(x.beat for x in notes)
        span = max(t1 - t0, 1e-6)
        for x in notes:
            idx = min(bins - 1, int((x.beat - t0) / span * bins))
            curve[idx] += 1
        mx = max(curve) or 1.0
        curve = [c / mx for c in curve]
    mean_c = sum(curve) / bins
    var = sum((c - mean_c) ** 2 for c in curve) / bins
    section_contrast = math.sqrt(var) / (mean_c + 1e-8)

    bomb_rate = len(bombs) / dur if dur else 0.0
    wall_rate = len(walls) / dur if dur else 0.0

    # --- Extended vocabulary / safety metrics (rescan v2) ---
    # Same-column repeats per hand (teleport-y patterns)
    same_col = col_pairs = 0
    for seq in (left, right):
        for i in range(1, len(seq)):
            if seq[i].beat - seq[i - 1].beat > 4:
                continue
            col_pairs += 1
            if seq[i].x == seq[i - 1].x:
                same_col += 1
    same_col_repeat = same_col / col_pairs if col_pairs else 0.0

    # Cut-direction vocabulary breadth (normalized Shannon entropy over 9 cuts)
    cut_counts: Counter = Counter(x.cut for x in notes)
    if n and len(cut_counts) > 1:
        ent = -sum((c / n) * math.log(c / n + 1e-9) for c in cut_counts.values())
        cut_entropy = ent / math.log(9)
    else:
        cut_entropy = 0.0

    # Lane-change rate per hand
    changes = pairs = 0
    for seq in (left, right):
        for i in range(1, len(seq)):
            pairs += 1
            if seq[i].x != seq[i - 1].x:
                changes += 1
    lane_change_rate = changes / pairs if pairs else 0.0

    # Triplet/swing vocabulary: notes near x.33 / x.67 beat offsets
    triplets = 0
    for x in notes:
        f = x.beat * 3.0
        if abs(f - round(f)) < 0.12 and abs(x.beat * 4 - round(x.beat * 4)) > 0.1:
            triplets += 1
    triplet_ratio = triplets / n if n else 0.0

    # Doubles that are vertically stacked (same column, different row)
    stacked_doubles = total_doubles = 0
    for group in by_beat.values():
        colors = {x.color for x in group}
        if not (0 in colors and 1 in colors):
            continue
        reds = [x for x in group if x.color == 0]
        blues = [x for x in group if x.color == 1]
        total_doubles += 1
        if any(r.x == b.x and r.y != b.y for r in reds for b in blues):
            stacked_doubles += 1
    double_stack_rate = stacked_doubles / total_doubles if total_doubles else 0.0

    # Wall/note collisions and bomb proximity in curated practice
    wall_note_overlaps = 0
    for w in walls:
        w0, w1 = w.beat - 0.5, w.beat + w.duration + 0.5
        lanes = set(range(w.x, w.x + max(1, w.width)))
        for x in notes:
            if (
                w0 <= x.beat <= w1
                and x.x in lanes
                and (w.height >= 2 or x.y == 0)
            ):
                wall_note_overlaps += 1
                break
    bombs_near = 0
    for b in bombs:
        hit = False
        for x in notes:
            db = abs(x.beat - b.beat)
            if db < 1.25 and (x.x, x.y) == (b.x, b.y):
                hit = True
                break
            if db < 0.5 and abs(x.x - b.x) <= 1 and x.y == b.y:
                hit = True
                break
        if hit:
            bombs_near += 1

    return {
        "nps": round(nps, 4),
        "note_count": n,
        "bomb_count": len(bombs),
        "wall_count": len(walls),
        "duration": round(dur, 3),
        "bpm": round(bpm, 3),
        "njs": round(njs, 3),
        "jump_ratio": round(jump_ratio, 4),
        "hand_balance": round(hand_balance, 4),
        "min_hand_gap_beats": round(min_hand_gap if min_hand_gap < 900 else 0.0, 4),
        "stream_ratio": round(stream_ratio, 4),
        "hand_alt_ratio": round(hand_alt_ratio, 4),
        "flow_ratio": round(flow_ratio, 4),
        "reset_ratio": round(reset_ratio, 4),
        "dot_ratio": round(dot_ratio, 4),
        "cross_ratio": round(cross_ratio, 4),
        "row_hist": [round(x, 4) for x in row_hist],
        "col_hist": [round(x, 4) for x in col_hist],
        "spatial_travel": round(spatial_travel, 4),
        "ioi_hist": ioi_hist,
        "motif_reuse": round(motif_reuse, 4),
        "density_curve": [round(x, 4) for x in curve],
        "section_contrast": round(section_contrast, 4),
        "bomb_rate": round(bomb_rate, 4),
        "wall_rate": round(wall_rate, 4),
        "same_col_repeat": round(same_col_repeat, 4),
        "cut_entropy": round(cut_entropy, 4),
        "lane_change_rate": round(lane_change_rate, 4),
        "triplet_ratio": round(triplet_ratio, 4),
        "double_stack_rate": round(double_stack_rate, 4),
        "wall_note_overlaps": wall_note_overlaps,
        "bombs_near_notes": bombs_near,
        "empty": empty,
    }


def fingerprint_map(parsed: ParsedMap, meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    meta = meta or {}
    rows: list[dict[str, Any]] = []
    playlists = meta.get("playlists") or []
    tags = meta.get("tags") or []
    duration = parsed.duration or float(meta.get("duration") or 0)
    bpm = parsed.bpm or float(meta.get("bpm") or 120)
    for d in parsed.difficulties:
        fp = fingerprint_notes(
            d.notes,
            bombs=d.bombs,
            walls=d.walls,
            bpm=bpm,
            duration=duration,
            njs=d.njs,
        )
        fp.update(
            {
                "map_id": parsed.map_id,
                "title": parsed.title,
                "artist": parsed.artist,
                "characteristic": d.characteristic,
                "difficulty": d.difficulty,
                "dat_version": d.version,
                "info_version": parsed.info_version,
                "tags": tags,
                "playlists": playlists,
                "uploader": meta.get("uploader"),
                "curators": sorted(
                    {p.get("curator") for p in playlists if p.get("curator")}
                ),
            }
        )
        rows.append(fp)
    return rows


def feature_vector(fp: dict[str, Any]) -> list[float]:
    row = fp.get("row_hist") or [0, 0, 0]
    col = fp.get("col_hist") or [0, 0, 0, 0]
    ioi = fp.get("ioi_hist") or {}
    return [
        float(fp.get("nps") or 0),
        float(fp.get("jump_ratio") or 0),
        float(fp.get("reset_ratio") or 0),
        float(fp.get("flow_ratio") or 0),
        float(fp.get("cross_ratio") or 0),
        float(fp.get("stream_ratio") or 0),
        float(fp.get("section_contrast") or 0),
        float(row[2] if len(row) > 2 else 0),
        float(fp.get("motif_reuse") or 0),
        float(fp.get("bomb_rate") or 0),
        float(fp.get("wall_rate") or 0),
        float(fp.get("dot_ratio") or 0),
        float(fp.get("hand_alt_ratio") or 0),
        float(fp.get("spatial_travel") or 0),
        float(col[0] if col else 0),
        float(col[3] if len(col) > 3 else 0),
        float(ioi.get("0.25") or 0),
        float(ioi.get("0.5") or 0),
        float(ioi.get("1.0") or 0),
    ]


def iter_standard_fps(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if str(r.get("characteristic", "")).lower() == "standard" and not r.get("empty")
    ]
