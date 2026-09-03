"""LEGACY — pre-premium chart generator. The studio website and POST
/api/generate never import this module; they shell out to the bundled
beat-saber-mapping skill (see docs/ARCHITECTURE.md). Do not wire it back into
the studio path. Kept only for the legacy CLI and its tests.

Flow-aware, rule-enforcing note placement from analysis + style profiles.

Hard rules distilled from the curated-corpus study (docs/style-study.md):
- parity/flow first; reset chains capped by style
- hands stay home; crosses are rare accents
- doubles are accents on strong onsets, mirrored safely
- one rhythm skeleton shared across difficulties (thin/subdivide, never re-invent)
- bombs/walls are punctuation with swing-path clearance
- face-plants (inward opposing cuts) are structurally impossible
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from beatforge.analyze import analyze_audio, section_at
from beatforge.chart import (
    CUT_NAMES,
    AnalysisResult,
    Arc,
    Bomb,
    Chart,
    Cut,
    DifficultyChart,
    Note,
    Obstacle,
    SustainRegion,
    beat_to_time,
    time_to_beat,
)
from beatforge.styles import (
    DIFFICULTIES,
    FLOW_NEXT,
    DifficultyBudget,
    StyleProfile,
    pick_style,
)

RIGHTWARD = {3, 5, 7}
LEFTWARD = {2, 4, 6}
MIRROR_CUT = {0: 0, 1: 1, 2: 3, 3: 2, 4: 5, 5: 4, 6: 7, 7: 6, 8: 8}
_DET_STYLES: dict[str, StyleProfile] = {}
_CUT_ANGLES = [
    (3, 0.0),
    (5, 45.0),
    (0, 90.0),
    (4, 135.0),
    (2, 180.0),
    (6, -135.0),
    (1, -90.0),
    (7, -45.0),
]
_UP_CUTS = {0, 4, 5}
_DOWN_CUTS = {1, 6, 7}

# These are authored swing phrases, not random cells. Each hand starts where
# its previous cut ended, then alternates a comfortable forehand/backhand path.
_MOTIF_LIBRARY: tuple[tuple[tuple[str, int, int, int], ...], ...] = (
    (
        ("left", 1, 1, 1),
        ("right", 1, 2, 1),
        ("left", 4, 0, 0),
        ("right", 5, 3, 0),
        ("left", 7, 1, 1),
        ("right", 6, 2, 1),
        ("left", 0, 1, 2),
        ("right", 0, 2, 0),
    ),
    (
        ("left", 6, 0, 1),
        ("right", 7, 3, 1),
        ("left", 5, 1, 0),
        ("right", 4, 2, 0),
        ("left", 1, 0, 1),
        ("right", 1, 3, 1),
        ("left", 0, 1, 0),
        ("right", 0, 2, 2),
    ),
    (
        ("left", 1, 1, 1),
        ("right", 1, 2, 1),
        ("left", 5, 0, 0),
        ("right", 4, 3, 0),
        ("left", 6, 1, 1),
        ("right", 7, 2, 1),
        ("left", 0, 0, 2),
        ("right", 0, 3, 2),
    ),
)


@dataclass
class RhythmPlan:
    candidates: list[tuple[float, float]]
    motifs: list[list[tuple[str, int, int, int]]]
    real: set[float] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.real is None:
            self.real = set()


def generate_chart(
    audio_path: str | Path,
    *,
    title: str | None = None,
    artist: str = "Unknown",
    difficulties: list[str] | None = None,
    style: str = "auto",
    seed: int = 42,
    progress=None,
) -> Chart:
    path = Path(audio_path)
    analysis = analyze_audio(path, progress=progress)
    profile = pick_style(style, analysis.bpm, _mean_energy(analysis))
    if progress:
        progress("style", f"picked {profile.name} style at {analysis.bpm:.0f} bpm")
    diff_names = difficulties or ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
    from beatforge.critic import run_critic

    best_chart: Chart | None = None
    best_key = (-999, -1.0, -1)
    for attempt in range(4):
        attempt_seed = seed + attempt * 7919
        if progress and attempt:
            progress("refine", f"quality pass {attempt + 1}/4")
        diffs = place_difficulties(
            analysis,
            diff_names,
            profile=profile,
            seed=attempt_seed,
            progress=progress if attempt == 0 else None,
        )
        candidate = Chart(
            title=title or path.stem,
            artist=artist,
            bpm=analysis.bpm,
            duration=analysis.duration,
            beats=analysis.beats,
            sections=analysis.sections,
            difficulties=diffs,
            style=profile.name,
        )
        report = run_critic(candidate)
        reports = list(report["difficulties"].values())
        hard_failures = sum(int(item["hard_failures"]) for item in reports)
        worst_score = min((float(item["score"]) for item in reports), default=0.0)
        passed = sum(int(item["passed"]) for item in reports)
        quality_key = (-hard_failures, worst_score, passed)
        if quality_key > best_key:
            best_chart = candidate
            best_key = quality_key
        if hard_failures == 0 and worst_score >= 0.92:
            break
    assert best_chart is not None
    if progress:
        progress("refine", f"selected chart at critic score {best_key[1]:.3f}")
    return best_chart


def place_difficulties(
    analysis: AnalysisResult,
    diff_names: list[str],
    *,
    profile: StyleProfile | None = None,
    style: str = "auto",
    seed: int = 42,
    progress=None,
) -> dict[str, DifficultyChart]:
    """Place every difficulty from ONE shared rhythm plan (corpus rule 9)."""
    if profile is None:
        profile = pick_style(style, analysis.bpm, _mean_energy(analysis))
    master = random.Random(seed)
    plan = _build_plan(analysis, profile, master)
    if progress:
        progress("plan", f"{len(plan.candidates)} rhythm slots, {len(plan.motifs)} motifs")
    # Additive charting (corpus rule 9): place easiest first, then each tier
    # inherits every note of the previous tier and only adds.
    ordered = sorted(
        [n for n in diff_names if n in DIFFICULTIES],
        key=lambda n: DIFFICULTIES[n].rank,
    )
    diffs: dict[str, DifficultyChart] = {}
    inherit: dict[float, list[Note]] = {}
    for name in ordered:
        if progress:
            progress("place", f"placing {name}")
        rng = random.Random(_stable_seed(seed, name))
        diffs[name] = _place_difficulty(
            analysis, DIFFICULTIES[name], profile, plan, rng, inherit=inherit
        )
        for nte in diffs[name].notes:
            inherit.setdefault(round(nte.beat, 3), []).append(nte)
    return diffs


def _mean_energy(analysis: AnalysisResult) -> float:
    return (
        sum(analysis.beat_energy) / len(analysis.beat_energy)
        if analysis.beat_energy
        else 0.5
    )


def _stable_seed(seed: int, name: str) -> int:
    h = hashlib.md5(name.encode("utf-8")).hexdigest()
    return seed + (int(h[:8], 16) % 10_000)


def _build_plan(
    analysis: AnalysisResult, style: StyleProfile, rng: random.Random
) -> RhythmPlan:
    beat_dur = 60.0 / analysis.bpm
    # Tick grid derived from DETECTED beats (not arithmetic from t=0), so
    # notes land exactly on the song's musical beats including any tempo
    # drift or pickup offset. Sixteenth resolution.
    ticks: list[float] = []
    bts = analysis.beats
    if bts:
        for i in range(len(bts) - 1):
            a, b = bts[i], bts[i + 1]
            for k in range(4):
                ticks.append(a + (b - a) * k / 4.0)
        ticks.append(bts[-1])
        ticks.sort()
    else:
        step = beat_dur / 4.0
        t = 0.0
        while t < analysis.duration:
            ticks.append(t)
            t += step
    import bisect

    def snap(t: float) -> float:
        if not ticks:
            return round(t / (beat_dur / 4)) * (beat_dur / 4)
        i = bisect.bisect_left(ticks, t)
        window = ticks[max(0, i - 1): i + 1]
        return min(window, key=lambda x: abs(x - t))

    by_t: dict[float, float] = {}
    real_times: set[float] = set()
    for t, s in zip(analysis.onset_times, analysis.onset_strengths):
        qt = snap(t)
        k = round(qt, 4)
        by_t[k] = max(by_t.get(k, 0.0), s)
        real_times.add(k)
    for i, bt in enumerate(analysis.beats):
        e = analysis.beat_energy[i] if i < len(analysis.beat_energy) else 0.5
        qt = snap(bt)
        k = round(qt, 4)
        by_t[k] = max(by_t.get(k, 0.0), e * 0.85)
        # Only the speed profile may add a missing eighth-note pulse. Flow
        # maps are selected from detected attacks and beat anchors, so they do
        # not manufacture sixteenth streams to chase a global NPS number.
        if style.name == "speed" and style.stream_bias > 0.5 and e > 0.6:
            nxt = snap(bt + beat_dur / 2)
            mid = round(nxt, 4)
            by_t[mid] = max(by_t.get(mid, 0.0), e * 0.55)

    cands = sorted(by_t.items(), key=lambda kv: kv[0])
    return RhythmPlan(candidates=cands, motifs=_build_motifs(rng), real=real_times)


def _build_motifs(rng: random.Random, n_motifs: int = 3, n_tokens: int = 8):
    motifs: list[list[tuple[str, int, int, int]]] = []
    order = list(range(len(_MOTIF_LIBRARY)))
    rng.shuffle(order)
    for i in range(n_motifs):
        base = list(_MOTIF_LIBRARY[order[i % len(order)]])
        if rng.random() < 0.5:
            base = [
                (
                    "right" if hand == "left" else "left",
                    MIRROR_CUT[cut],
                    3 - lane,
                    row,
                )
                for hand, cut, lane, row in base
            ]
        if base and base[0][0] == "right":
            base = base[1:] + base[:1]
        repeats = (n_tokens + len(base) - 1) // len(base)
        motifs.append((base * repeats)[:n_tokens])
    return motifs


def _select_candidates(
    analysis: AnalysisResult,
    budget: DifficultyBudget,
    style: StyleProfile,
    plan: RhythmPlan,
    inherit: dict[float, list[Note]] | None,
) -> list[tuple[float, float]]:
    """Choose rhythm locally, so energy and attacks shape every phrase.

    A song-wide top-k budget made quiet late sections denser than drops and
    could starve both ends of a track. This selector budgets each musical bar,
    preserves the lower difficulty's timing, and caps sixteenth density.
    """
    if not plan.candidates:
        return []

    bars: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    for t, strength in plan.candidates:
        beat = time_to_beat(t, analysis.beats, analysis.bpm)
        bars[math.floor(beat / 4.0)].append((t, strength, beat))

    inherited = set(inherit or {})
    chosen: dict[float, tuple[float, float]] = {}
    first_t = round(analysis.beats[0], 4) if analysis.beats else round(plan.candidates[0][0], 4)
    last_t = round(analysis.beats[-1], 4) if analysis.beats else round(plan.candidates[-1][0], 4)

    # Event spacing is the chart's readable rhythm vocabulary. Expert uses
    # eighths; Expert+ may use detected sixteenths, but never an all-song roll.
    event_gap = 0.25 if budget.rank >= 9 else max(0.5, budget.subdivision)
    max_per_bar = 12 if budget.rank >= 9 else (8 if budget.rank >= 5 else 4)
    seconds_per_bar = 4.0 * 60.0 / max(analysis.bpm, 1.0)
    base_per_bar = budget.target_nps * style.density_scale * seconds_per_bar

    for bar_index in sorted(bars):
        pool = bars[bar_index]
        sec = section_at(analysis.sections, pool[0][0])
        energy_scale = 0.45 + 0.70 * max(0.0, min(1.0, sec.energy))
        target = max(1, min(max_per_bar, int(round(base_per_bar * energy_scale))))

        required = [
            item
            for item in pool
            if round(item[2], 3) in inherited
            or round(item[0], 4) in (first_t, last_t)
        ]
        selected = list(required)

        def priority(item: tuple[float, float, float]) -> tuple[float, float]:
            t, strength, beat = item
            is_real = round(t, 4) in plan.real
            on_beat = abs(beat - round(beat)) < 0.04
            half_beat = abs(beat * 2.0 - round(beat * 2.0)) < 0.04
            score = strength + (0.55 if is_real else 0.0)
            score += 0.32 if on_beat else (0.10 if half_beat else 0.0)
            return score, -t

        for item in sorted(pool, key=priority, reverse=True):
            if item in selected or len(selected) >= target:
                continue
            beat = item[2]
            if any(abs(beat - old[2]) < event_gap - 0.02 for old in selected):
                continue
            selected.append(item)

        # If strong spacing leaves a bar under target, fill only with detected
        # attacks and keep a smaller safety gap. This is bounded on Expert+.
        if budget.rank >= 9 and len(selected) < target:
            for item in sorted(pool, key=priority, reverse=True):
                if item in selected or round(item[0], 4) not in plan.real:
                    continue
                if any(abs(item[2] - old[2]) < 0.23 for old in selected):
                    continue
                selected.append(item)
                if len(selected) >= target:
                    break

        for t, strength, _ in selected:
            chosen[round(t, 4)] = (t, strength)

    # The opening and closing beat are non-negotiable musical anchors.
    by_key = {round(t, 4): (t, s) for t, s in plan.candidates}
    for anchor in (first_t, last_t):
        if anchor in by_key:
            chosen[anchor] = by_key[anchor]
    return sorted(chosen.values(), key=lambda item: item[0])


def _place_difficulty(
    analysis: AnalysisResult,
    budget: DifficultyBudget,
    style: StyleProfile,
    plan: RhythmPlan,
    rng: random.Random,
    inherit: dict[float, list[Note]] | None = None,
) -> DifficultyChart:
    bpm = analysis.bpm
    cands = _select_candidates(analysis, budget, style, plan, inherit)

    notes: list[Note] = []
    last_beat = {"left": -999.0, "right": -999.0}
    last_cut = {"left": 1, "right": 1}
    last_pos = {"left": (1, 0), "right": (2, 0)}
    chain = {"left": 0, "right": 0}
    last_hand = "right"
    occupied: set[tuple[int, int, int]] = set()
    at_beat: dict[int, list[Note]] = defaultdict(list)
    beat_dur = 60.0 / bpm
    # Arrangement sections choose related authored motifs. Each phrase gets a
    # variation instead of stamping one four-beat block over the whole song.
    sec_parity: dict[float, int] = {}
    _k = 0
    for s in analysis.sections:
        if s.label in ("verse", "drop", "body"):
            sec_parity[s.t] = _k
            _k += 1
    _PATTERN_CYCLE = 16  # tokens span 4 beats at sixteenth resolution
    max_chain = 4 if style.reset_tolerance > 0.15 else 2

    # Forward-looking reservations: inherited beats block nearby free
    # placements on the same hand so child tiers never crowd parents.
    reserved: dict[str, list[float]] = {"left": [], "right": []}
    if inherit:
        for beats_ in inherit.values():
            for src in beats_:
                reserved[src.hand].append(src.beat)
        for h in reserved:
            reserved[h].sort()

    def _near_reserved(hand: str, beat: float) -> bool:
        import bisect

        lst = reserved[hand]
        i = bisect.bisect_left(lst, beat)
        for j in (i - 1, i):
            if 0 <= j < len(lst):
                if abs(lst[j] - beat) < budget.min_hand_gap_beats * 0.999:
                    return True
        return False

    for t, s in cands:
        if t < 0.0 or t >= analysis.duration - 0.05:
            continue
        beat = time_to_beat(t, analysis.beats, analysis.bpm)
        bq = int(round(beat * 4))
        sec = section_at(analysis.sections, t)

        # Inherited tier: copy the lower difficulty's notes at this beat
        # verbatim so spreads nest by construction. Clone cuts are re-validated
        # against this tier's chain state and neighbors.
        ikey = round(beat, 3)
        if inherit and ikey in inherit:
            cloned: list[Note] = []
            for src in inherit[ikey]:
                if (bq, src.lane, src.row) in occupied:
                    continue
                h = src.hand
                # Keep the parent's cut when it preserves this tier's parity;
                # otherwise derive the best local option.
                c: int = src.cut_dir
                keeps_parity = (
                    c in FLOW_NEXT.get(last_cut[h], [])
                    and not (c == last_cut[h] and chain[h] >= max_chain)
                    and not _creates_face_plant(c, src.lane, src.row, h, at_beat[bq])
                )
                if not keeps_parity:
                    c = _choose_cut(
                        h,
                        last_cut[h],
                        last_pos[h],
                        (src.lane, src.row),
                        chain[h],
                        _DET_STYLES.setdefault(style.name, replace(style, flow_bias=1.0)),
                        rng,
                    )
                if c == last_cut[h] and chain[h] >= max_chain:
                    alts = [
                        a
                        for a in FLOW_NEXT.get(last_cut[h], [])
                        if not _creates_face_plant(a, src.lane, src.row, h, at_beat[bq])
                    ]
                    c = alts[0] if alts else 8
                c = _resolve_face_plant(c, src.lane, src.row, h, at_beat[bq])
                clone = Note(
                    t=t,
                    beat=beat,
                    lane=src.lane,
                    row=src.row,
                    hand=h,
                    cut=CUT_NAMES.get(Cut(c), "down"),
                    strength=float(s),
                )
                notes.append(clone)
                occupied.add((bq, clone.lane, clone.row))
                at_beat[bq].append(clone)
                cloned.append(clone)
                chain[h] = chain[h] + 1 if c == last_cut[h] else 1
                last_beat[h] = beat
                last_cut[h] = c
                last_pos[h] = (clone.lane, clone.row)
                last_hand = h
            # Higher tiers escalate inherited singles into doubles — this is
            # how full spreads add weight on beats players already know.
            if (
                len(cloned) == 1
                and round(t, 4) in plan.real
                and s >= 0.68
                and sec.energy >= 0.50
            ):
                p = budget.jump_scale * style.jump_chance * 0.30
                if rng.random() < p:
                    base = cloned[0]
                    other = "right" if base.hand == "left" else "left"
                    ox = 3 - base.lane if base.lane in (0, 3) else (
                        2 if base.hand == "left" else 1
                    )
                    oy = base.row
                    if (bq, ox, oy) not in occupied and not _near_reserved(
                        other, beat
                    ):
                        if beat - last_beat[other] >= budget.min_hand_gap_beats:
                            oc = _choose_cut(
                                other,
                                last_cut[other],
                                last_pos[other],
                                (ox, oy),
                                chain[other],
                                style,
                                rng,
                            )
                            probe = Note(
                                t=t,
                                beat=beat,
                                lane=base.lane,
                                row=base.row,
                                hand=base.hand,
                                cut=base.cut,
                            )
                            if _creates_face_plant(
                                oc, ox, oy, other, at_beat[bq] + [probe]
                            ):
                                oc = 1
                            if oc == last_cut[other] and chain[other] >= max_chain:
                                alts = [
                                    a
                                    for a in FLOW_NEXT.get(last_cut[other], [])
                                    if not _creates_face_plant(
                                        a, ox, oy, other, at_beat[bq]
                                    )
                                ]
                                oc = alts[0] if alts else 8
                            partner = Note(
                                t=t,
                                beat=beat,
                                lane=ox,
                                row=oy,
                                hand=other,
                                cut=CUT_NAMES.get(Cut(oc), "down"),
                                strength=float(s),
                            )
                            notes.append(partner)
                            occupied.add((bq, ox, oy))
                            at_beat[bq].append(partner)
                            chain[other] = (
                                chain[other] + 1 if oc == last_cut[other] else 1
                            )
                            last_beat[other] = beat
                            last_cut[other] = oc
                            last_pos[other] = (ox, oy)
            continue

        # Doubles are accents on grid downbeats; probability scales with
        # section energy so mid-energy verses still get some.
        is_accent = abs(beat - round(beat)) < 0.01
        jump_p = budget.jump_scale * style.jump_chance * (
            0.55 if s >= 0.82 else 0.25
        )
        phrase_accent = (
            budget.rank >= 5
            and sec.label == "drop"
            and is_accent
            and int(round(beat)) % 4 == 0
            and s >= 0.78
        )
        if (
            sec.energy >= 0.42
            and round(t, 4) in plan.real
            and s >= 0.68
            and not _near_reserved("left", beat)
            and not _near_reserved("right", beat)
        ):
            if phrase_accent or rng.random() < jump_p:
                placed = _try_jump(
                    t=t,
                    beat=beat,
                    bq=bq,
                    s=s,
                    last_hand=last_hand,
                    last_beat=last_beat,
                    budget=budget,
                    style=style,
                    last_pos=last_pos,
                    last_cut=last_cut,
                    chain=chain,
                    occupied=occupied,
                    at_beat=at_beat,
                    rng=rng,
                    notes=notes,
                )
                if placed is not None:
                    last_hand = placed
                    continue

        # Reuse authored swing vocabulary without copying one four-beat block
        # across an entire section. Alternating phrase mirrors keep the map
        # coherent while avoiding the old mechanical/scattered repetition.
        use_motif = bool(plan.motifs) and sec.label in ("verse", "drop", "body")
        motif_idx = sec_parity.get(sec.t, 0) % max(1, len(plan.motifs))

        def _hand_ok(hh: str) -> bool:
            return (
                beat - last_beat[hh] >= budget.min_hand_gap_beats
                and not _near_reserved(hh, beat)
            )

        sec_start_beat = time_to_beat(sec.t, analysis.beats, analysis.bpm)
        slot = int(round((beat - sec_start_beat) * 4.0)) if use_motif else -1
        cyc = slot % _PATTERN_CYCLE if use_motif else -1

        hand = "left" if last_hand == "right" else "right"
        token = None
        if use_motif:
            m = plan.motifs[motif_idx]
            token = m[cyc % len(m)]
            phrase = max(0, slot) // 32
            if phrase % 2:
                token = (
                    "right" if token[0] == "left" else "left",
                    MIRROR_CUT[token[1]],
                    3 - token[2],
                    token[3],
                )
            hand = token[0]
            if hand == last_hand:
                hand = "right" if hand == "left" else "left"
                token = (hand, MIRROR_CUT[token[1]], 3 - token[2], token[3])

        if not _hand_ok(hand):
            other = "right" if hand == "left" else "left"
            if token is not None and token[0] == hand:
                token = None
            if _hand_ok(other):
                hand = other
            else:
                continue

        cut_i: int | None
        if token is not None:
            _, tok_cut, x, y = token
            cut_i = tok_cut
            if (
                cut_i not in FLOW_NEXT.get(last_cut[hand], [])
                or (cut_i == last_cut[hand] and chain[hand] >= max_chain)
            ):
                cut_i = _choose_cut(
                    hand,
                    last_cut[hand],
                    last_pos[hand],
                    (x, y),
                    chain[hand],
                    style,
                    rng,
                )
        else:
            x, y = _pick_position(
                hand,
                style,
                budget,
                last_pos[hand],
                rng,
                prev_cut=last_cut[hand],
            )
            cut_i = None

        if (bq, x, y) in occupied:
            moved = False
            for dx in (0, 1, -1, 2, -2):
                nx = x + dx
                if 0 <= nx <= 3 and (bq, nx, y) not in occupied:
                    x = nx
                    moved = True
                    break
            if not moved:
                continue

        if cut_i is None:
            cut_i = _choose_cut(
                hand, last_cut[hand], last_pos[hand], (x, y), chain[hand], style, rng
            )
        cut_i = _resolve_face_plant(cut_i, x, y, hand, at_beat[bq])
        if cut_i == last_cut[hand] and chain[hand] >= max_chain:
            alts = [
                c
                for c in FLOW_NEXT.get(last_cut[hand], [])
                if not _creates_face_plant(c, x, y, hand, at_beat[bq])
            ]
            cut_i = alts[0] if alts else 8

        note = Note(
            t=t,
            beat=beat,
            lane=x,
            row=y,
            hand=hand,
            cut=CUT_NAMES.get(Cut(cut_i), "down"),
            strength=float(s),
        )
        notes.append(note)
        occupied.add((bq, x, y))
        at_beat[bq].append(note)
        chain[hand] = chain[hand] + 1 if cut_i == last_cut[hand] else 1
        last_beat[hand] = beat
        last_cut[hand] = cut_i
        last_pos[hand] = (x, y)
        last_hand = hand

    _polish_color_flow(notes, style, budget, rng)
    notes.sort(key=lambda n: (n.beat, n.hand))
    arcs = (
        _place_arcs(
            analysis,
            notes,
            budget,
            protected_beats=set(inherit or {}),
        )
        if budget.rank >= 3
        else []
    )
    # Arc interiors are deliberate holds with no extra cubes. Rebalance the
    # remaining event stream, then bind each arc back to its playable head.
    _polish_color_flow(notes, style, budget, rng)
    notes.sort(key=lambda n: (n.beat, n.hand))
    arcs = _refresh_arcs(arcs, notes)
    obstacles = (
        _place_walls(analysis, notes, style, rng) if budget.use_walls else []
    )
    bombs = (
        _place_bombs(analysis, notes, bpm, style, rng) if budget.use_walls else []
    )
    return DifficultyChart(notes=notes, arcs=arcs, obstacles=obstacles, bombs=bombs)


def _polish_color_flow(
    notes: list[Note],
    style: StyleProfile,
    budget: DifficultyBudget,
    rng: random.Random,
) -> None:
    """Make the final event stream alternate colors with coherent home lanes.

    Additive difficulty timing can insert a note between two inherited events.
    Without a final pass that produced long same-color runs even though each
    individual placement looked valid. This works on timing groups, preserves
    doubles, and recomputes each hand's swing continuation.
    """
    groups: dict[int, list[Note]] = defaultdict(list)
    for note in notes:
        groups[int(round(note.beat * 8.0))].append(note)

    next_hand = "left"
    last_cut = {"left": 1, "right": 1}
    last_pos = {"left": (1, 1), "right": (2, 1)}
    chain = {"left": 0, "right": 0}

    def place_for_hand(note: Note, hand: str, peers: list[Note]) -> None:
        home = [0, 1] if hand == "left" else [2, 3]
        preferred = note.lane if note.lane in home else home[0]
        if rng.random() < 0.14:
            lane = last_pos[hand][0]
        else:
            lane = (
                preferred
                if preferred != last_pos[hand][0]
                else home[1 - home.index(preferred)]
            )
        row = max(0, min(2, note.row))
        cut = _choose_cut(
            hand,
            last_cut[hand],
            last_pos[hand],
            (lane, row),
            chain[hand],
            style,
            rng,
        )
        cut = _resolve_face_plant(cut, lane, row, hand, peers)
        note.hand = hand
        note.lane = lane
        note.row = row
        note.cut = CUT_NAMES.get(Cut(cut), "down")
        chain[hand] = chain[hand] + 1 if cut == last_cut[hand] else 1
        last_cut[hand] = cut
        last_pos[hand] = (lane, row)

    for key in sorted(groups):
        group = groups[key]
        if len(group) >= 2:
            ordered = sorted(group[:2], key=lambda note: note.color)
            place_for_hand(ordered[0], "left", [])
            place_for_hand(ordered[1], "right", [ordered[0]])
            for extra in group[2:]:
                # Stacked triples are not authored; retain at most a safe pair.
                notes.remove(extra)
            continue
        note = group[0]
        place_for_hand(note, next_hand, [])
        next_hand = "right" if next_hand == "left" else "left"


def _refresh_arcs(arcs: list[Arc], notes: list[Note]) -> list[Arc]:
    """Rebind arc heads after final color/position polishing."""
    refreshed: list[Arc] = []
    last_tail = {"left": -999.0, "right": -999.0}
    for arc in sorted(arcs, key=lambda item: item.beat):
        heads = [note for note in notes if abs(note.beat - arc.beat) < 0.02]
        if not heads:
            continue
        head = heads[0]
        if arc.beat < last_tail[head.hand] - 0.02:
            continue
        arc.hand = head.hand
        arc.lane = head.lane
        arc.row = head.row
        arc.cut = head.cut
        refreshed.append(arc)
        last_tail[head.hand] = arc.tail_beat
    return refreshed


def _try_jump(
    *,
    t: float,
    beat: float,
    bq: int,
    s: float,
    last_hand: str,
    last_beat: dict,
    budget: DifficultyBudget,
    style: StyleProfile,
    last_pos: dict,
    last_cut: dict,
    chain: dict,
    occupied: set,
    at_beat: dict,
    rng: random.Random,
    notes: list,
) -> str | None:
    """Place a mirrored double. Returns the hand to treat as 'last', or None."""
    hand = "left" if last_hand == "right" else "right"
    other = "right" if hand == "left" else "left"
    if beat - last_beat[hand] < budget.min_hand_gap_beats:
        return None
    if beat - last_beat[other] < budget.min_hand_gap_beats:
        return None
    x, y = _pick_position(
        hand,
        style,
        budget,
        last_pos[hand],
        rng,
        prev_cut=last_cut[hand],
    )
    if (bq, x, y) in occupied:
        return None
    cut_i = _choose_cut(
        hand, last_cut[hand], last_pos[hand], (x, y), chain[hand], style, rng
    )
    ox = 3 - x if x in (0, 3) else (2 if hand == "left" else 1)
    oy = y
    oc = MIRROR_CUT[cut_i]
    max_chain = 4 if style.reset_tolerance > 0.15 else 2
    if oc == last_cut[other] and chain[other] >= max_chain:
        alts = list(FLOW_NEXT.get(last_cut[other], []))
        oc = alts[0] if alts else 8

    probe_a = Note(t=t, beat=beat, lane=x, row=y, hand=hand, cut=CUT_NAMES.get(Cut(cut_i), "down"))
    probe_b = Note(t=t, beat=beat, lane=ox, row=oy, hand=other, cut=CUT_NAMES.get(Cut(oc), "down"))
    existing = at_beat[bq]
    if _creates_face_plant(cut_i, x, y, hand, existing + [probe_b]):
        cut_i = oc = 1
    elif _creates_face_plant(oc, ox, oy, other, existing + [probe_a]):
        cut_i = oc = 1
    # Face-plant fallback must not smuggle in a reset chain.
    if cut_i == last_cut[hand] and chain[hand] >= max_chain:
        alts = [
            c
            for c in FLOW_NEXT.get(last_cut[hand], [])
            if not _creates_face_plant(c, x, y, hand, existing)
        ]
        cut_i = alts[0] if alts else 8
    if oc == last_cut[other] and chain[other] >= max_chain:
        alts = [
            c
            for c in FLOW_NEXT.get(last_cut[other], [])
            if not _creates_face_plant(c, ox, oy, other, existing)
        ]
        oc = alts[0] if alts else 8

    def _mk(h: str, cx: int, cy: int, cc: int) -> Note:
        return Note(
            t=t,
            beat=beat,
            lane=cx,
            row=cy,
            hand=h,
            cut=CUT_NAMES.get(Cut(cc), "down"),
            strength=float(s),
        )

    primary = _mk(hand, x, y, cut_i)
    notes.append(primary)
    occupied.add((bq, x, y))
    at_beat[bq].append(primary)
    chain[hand] = chain[hand] + 1 if cut_i == last_cut[hand] else 1
    last_beat[hand] = beat
    last_cut[hand] = cut_i
    last_pos[hand] = (x, y)

    if (bq, ox, oy) not in occupied:
        mirror = _mk(other, ox, oy, oc)
        notes.append(mirror)
        occupied.add((bq, ox, oy))
        at_beat[bq].append(mirror)
        chain[other] = chain[other] + 1 if oc == last_cut[other] else 1
        last_beat[other] = beat
        last_cut[other] = oc
        last_pos[other] = (ox, oy)
        return other
    return hand


def _choose_cut(
    hand: str,
    prev_cut: int,
    prev_pos: tuple[int, int],
    new_pos: tuple[int, int],
    chain_len: int,
    style: StyleProfile,
    rng: random.Random,
) -> int:
    """Pick a cut that follows swing geometry and flow, respecting chain caps."""
    dx = new_pos[0] - prev_pos[0]
    dy = new_pos[1] - prev_pos[1]
    geo = _cut_from_movement(dx, dy)
    flow = list(FLOW_NEXT.get(prev_cut, [1, 0, 2, 3]))
    if geo in flow:
        cands = [geo] + [c for c in flow if c != geo]
    else:
        cands = flow[:]
        if geo >= 0 and rng.random() < style.reset_tolerance:
            cands.append(geo)
    max_chain = 4 if style.reset_tolerance > 0.15 else 2
    if chain_len >= max_chain:
        trimmed = [c for c in cands if c != prev_cut]
        if trimmed:
            cands = trimmed
    if rng.random() < style.flow_bias:
        return cands[0]
    return rng.choice(cands[: min(3, len(cands))])


def _cut_from_movement(dx: int, dy: int) -> int:
    """Quantize the approach vector into a cut direction (-1 when stationary)."""
    if dx == 0 and dy == 0:
        return -1
    ang = math.degrees(math.atan2(dy, dx))
    best_cut, best_d = -1, 999.0
    for cut, a in _CUT_ANGLES:
        d = abs((ang - a + 180.0) % 360.0 - 180.0)
        if d < best_d:
            best_cut, best_d = cut, d
    return best_cut


def _pick_position(
    hand: str,
    style: StyleProfile,
    budget: DifficultyBudget,
    last: tuple[int, int],
    rng: random.Random,
    prev_cut: int | None = None,
) -> tuple[int, int]:
    home = [0, 1] if hand == "left" else [2, 3]
    if rng.random() < style.cross_chance:
        cols = sorted(set(home + ([2] if hand == "left" else [1])))
    else:
        cols = home
    if prev_cut in _UP_CUTS:
        rows = [1, 1, 2]
    elif prev_cut in _DOWN_CUTS:
        rows = [0, 0, 1]
    elif rng.random() < budget.row_bias_bottom:
        rows = [0, 0, 1]
    elif style.verticality > 0.5:
        rows = [0, 1, 1, 2]
    else:
        rows = [0, 0, 1, 1, 2]
    opts = sorted({
        (x, y)
        for x in cols
        for y in rows
        if abs(x - last[0]) + abs(y - last[1]) <= 2
    }) or sorted({(x, y) for x in cols for y in rows})

    def movement_cost(pos: tuple[int, int]) -> tuple[float, float]:
        dx = abs(pos[0] - last[0])
        dy = abs(pos[1] - last[1])
        same_cell = 3.0 if pos == last else 0.0
        same_column = 0.9 if pos[0] == last[0] else 0.0
        distance = abs((dx + dy) - 1.0) * 0.35
        edge = 0.15 if pos[0] in (0, 3) else 0.0
        return same_cell + same_column + distance + edge, rng.random()

    opts.sort(key=movement_cost)
    return rng.choice(opts[: min(2, len(opts))])


def _creates_face_plant(
    cut_i: int, x: int, y: int, hand: str, others: list[Note]
) -> bool:
    """True if this note would clash inward with an opposing saber nearby."""
    if cut_i == 8:
        return False
    if hand == "left":
        if not (cut_i in RIGHTWARD and x >= 1):
            return False
        return any(
            o.hand == "right"
            and o.cut_dir in LEFTWARD
            and o.lane <= 2
            and 0 < o.lane - x <= 2
            for o in others
        )
    if not (cut_i in LEFTWARD and x <= 2):
        return False
    return any(
        o.hand == "left"
        and o.cut_dir in RIGHTWARD
        and o.lane >= 1
        and 0 < x - o.lane <= 2
        for o in others
    )


def _resolve_face_plant(
    cut_i: int, x: int, y: int, hand: str, others: list[Note]
) -> int:
    if _creates_face_plant(cut_i, x, y, hand, others):
        return 1  # down cuts never cut inward horizontally
    return cut_i


def _arc_for_region(
    region: SustainRegion,
    notes: list[Note],
    analysis: AnalysisResult,
    protected_beats: set[float],
) -> Arc | None:
    """Fit one readable arc to a detected sustained phrase."""

    start_beat = time_to_beat(region.t, analysis.beats, analysis.bpm)
    end_beat = time_to_beat(region.end_t, analysis.beats, analysis.bpm)
    options: list[tuple[float, Arc]] = []

    for hand in ("left", "right"):
        hand_notes = [n for n in notes if n.hand == hand]
        for head in hand_notes:
            if head.beat < start_beat - 0.75 or head.beat > start_beat + 0.5:
                continue
            desired_tail = min(end_beat, head.beat + 4.0)
            if desired_tail - head.beat < 1.0:
                continue

            protected_blockers = [
                note
                for note in notes
                if head.beat + 0.02 < note.beat < desired_tail - 0.02
                and round(note.beat, 3) in protected_beats
            ]
            if protected_blockers:
                tail_note = min(protected_blockers, key=lambda note: note.beat)
                if tail_note.beat - head.beat < 1.0:
                    continue
            else:
                tail_options = [
                    note
                    for note in hand_notes
                    if note.beat - head.beat >= 1.0
                    and abs(note.beat - desired_tail) <= 0.75
                ]
                tail_note = (
                    min(tail_options, key=lambda note: abs(note.beat - desired_tail))
                    if tail_options
                    else None
                )
            if tail_note is not None:
                tail_beat = tail_note.beat
                tail_t = tail_note.t
                tail_lane = tail_note.lane
                tail_row = tail_note.row
                tail_cut = tail_note.cut
            else:
                tail_beat = desired_tail
                tail_t = min(
                    beat_to_time(tail_beat, analysis.beats, analysis.bpm),
                    analysis.duration - 0.25,
                )
                guide = min(
                    hand_notes,
                    key=lambda note: abs(note.beat - tail_beat),
                    default=head,
                )
                tail_lane = guide.lane
                tail_row = guide.row
                tail_cut = guide.cut

            duration = tail_beat - head.beat
            if duration < 1.0:
                continue
            interior = [
                note
                for note in notes
                if head.beat + 0.02 < note.beat < tail_beat - 0.02
            ]
            if any(round(note.beat, 3) in protected_beats for note in interior):
                continue
            overlap = min(tail_beat, end_beat) - max(head.beat, start_beat)
            score = (
                overlap
                + region.strength
                - 0.35 * abs(head.beat - start_beat)
                - 0.03 * len(interior)
            )
            options.append(
                (
                    score,
                    Arc(
                        t=head.t,
                        beat=head.beat,
                        tail_t=tail_t,
                        tail_beat=tail_beat,
                        lane=head.lane,
                        row=head.row,
                        tail_lane=tail_lane,
                        tail_row=tail_row,
                        hand=hand,
                        cut=head.cut,
                        tail_cut=tail_cut,
                    ),
                )
            )

    return max(options, key=lambda item: item[0])[1] if options else None


def _fallback_sustain_regions(analysis: AnalysisResult) -> list[SustainRegion]:
    """Infer legato windows from strong attacks followed by energetic quiet."""

    if len(analysis.beats) < 4 or not analysis.onset_times:
        return []
    strong_onsets = [
        t
        for t, strength in zip(analysis.onset_times, analysis.onset_strengths)
        if strength >= 0.45
    ]
    if not strong_onsets:
        return []

    regions: list[SustainRegion] = []
    beat_energy = analysis.beat_energy
    for i, start in enumerate(analysis.beats[:-2]):
        if start < 3.0 or start >= analysis.duration - 0.75:
            continue
        step = (
            analysis.beats[i + 1] - start
            if i + 1 < len(analysis.beats)
            else 60.0 / analysis.bpm
        )
        if not any(abs(t - start) <= max(0.18, step * 0.45) for t in strong_onsets):
            continue

        for span in (4, 3, 2):
            j = i + span
            if j >= len(analysis.beats):
                continue
            end = min(analysis.beats[j], analysis.duration - 0.25)
            if end - start < 0.75:
                continue
            interior_attacks = [
                t for t in strong_onsets if start + 0.3 < t < end - 0.15
            ]
            if interior_attacks:
                continue
            energies = beat_energy[i : j + 1]
            mean_energy = sum(energies) / len(energies) if energies else 0.0
            if mean_energy < 0.42:
                continue
            regions.append(
                SustainRegion(
                    t=float(start),
                    end_t=float(end),
                    strength=round(mean_energy * 0.8, 4),
                )
            )
            break

    chosen: list[SustainRegion] = []
    for region in sorted(regions, key=lambda item: (-item.strength, item.t)):
        if any(
            not (region.end_t + 0.5 <= other.t or region.t >= other.end_t + 0.5)
            for other in chosen
        ):
            continue
        chosen.append(region)
    return sorted(chosen, key=lambda item: item.t)


def _place_arcs(
    analysis: AnalysisResult,
    notes: list[Note],
    budget: DifficultyBudget,
    protected_beats: set[float] | None = None,
) -> list[Arc]:
    """Turn detected held tones into sparse, non-overlapping vanilla arcs."""

    if not notes:
        return []

    protected_beats = protected_beats or set()
    regions = list(analysis.sustains)
    for inferred in _fallback_sustain_regions(analysis):
        if not any(
            not (inferred.end_t <= region.t or inferred.t >= region.end_t)
            for region in regions
        ):
            regions.append(inferred)
    if not regions:
        return []
    arcs: list[Arc] = []
    last_tail = {"left": -999.0, "right": -999.0}
    max_arcs = max(1, int(analysis.duration / (14.0 if budget.rank >= 7 else 18.0)))
    for region in sorted(regions, key=lambda sustain: sustain.t):
        arc = _arc_for_region(region, notes, analysis, protected_beats)
        if arc is None or arc.beat < last_tail[arc.hand] + 2.0:
            continue
        notes[:] = [
            note
            for note in notes
            if not (
                arc.beat + 0.02 < note.beat < arc.tail_beat - 0.02
            )
        ]
        arcs.append(arc)
        last_tail[arc.hand] = arc.tail_beat
        if len(arcs) >= max_arcs:
            break
    return sorted(arcs, key=lambda arc: (arc.beat, arc.hand))


def _place_bombs(
    analysis: AnalysisResult,
    notes: list[Note],
    bpm: float,
    style: StyleProfile,
    rng: random.Random,
) -> list[Bomb]:
    """Bombs as drop punctuation with swing-path clearance (never NPS filler).

    Bombs are deliberately rare: one readable body cue is more useful than a
    field of random hazards around otherwise musical patterns.
    """
    if style.name == "chill":
        return []
    rate = {"tech": 0.035, "flow": 0.04}.get(style.name, 0.055)
    bombs: list[Bomb] = []
    global_cap = min(12, max(1, int(analysis.duration / 30.0)))
    beat_dur = 60.0 / bpm
    secs = analysis.sections
    for i, sec in enumerate(secs):
        if sec.label != "drop" or sec.energy < 0.6:
            continue
        end = secs[i + 1].t if i + 1 < len(secs) else analysis.duration
        span = max(end - sec.t, 0.0)
        max_bombs = max(1, int(span * rate))
        count = 0
        t = sec.t + beat_dur
        while t < end - 0.5 * beat_dur and count < max_bombs:
            if len(bombs) >= global_cap:
                return bombs
            beat = time_to_beat(t, analysis.beats, bpm)
            cells = [(lane, row) for row in (2, 0, 1) for lane in range(4)]
            rng.shuffle(cells)
            for lane, row in cells:
                ok = True
                for n in notes:
                    db = abs(n.beat - beat)
                    if db < 1.25 and (n.lane, n.row) == (lane, row):
                        ok = False
                        break
                    if db < 0.5 and abs(n.lane - lane) <= 1 and n.row == row:
                        ok = False
                        break
                if ok:
                    for bomb in bombs:
                        if (
                            abs(bomb.beat - beat) < 0.5
                            and abs(bomb.lane - lane) <= 1
                        ):
                            ok = False
                            break
                if ok:
                    bombs.append(Bomb(t=t, beat=beat, lane=lane, row=row))
                    count += 1
                    break
            t += beat_dur * rng.choice([8, 12, 16])
    return bombs


def _place_walls(
    analysis: AnalysisResult,
    notes: list[Note],
    style: StyleProfile,
    rng: random.Random,
) -> list[Obstacle]:
    """Build spaced wall phrases that remain readable beside the note path."""
    walls: list[Obstacle] = []
    secs = analysis.sections
    last_wall_beat = -999.0
    energy_floor = 0.30 if style.name == "chill" else 0.48
    wall_spacing = 16.0 if style.name == "chill" else 12.0
    side_order = [0, 2]
    rng.shuffle(side_order)
    side_index = 0
    max_walls = min(12, max(4, int(analysis.duration / 28.0)))

    def clear_wall_lanes(lane: int, width: int, start: float, end: float) -> bool:
        blocked = set(range(lane, lane + width))
        conflicts = [
            n for n in notes if start <= n.beat <= end and n.lane in blocked
        ]
        if not conflicts:
            return True
        occupied = {
            (round(n.beat, 3), n.lane, n.row)
            for n in notes
            if n not in conflicts
        }
        moves: list[tuple[Note, int, int]] = []
        for note in conflicts:
            home = [0, 1] if note.hand == "left" else [2, 3]
            lane_options = sorted(
                [x for x in home if x not in blocked],
                key=lambda x: abs(x - note.lane),
            )
            lane_options += [
                x for x in range(4) if x not in blocked and x not in home
            ]
            target: tuple[int, int] | None = None
            for row in (note.row, 1, 0, 2):
                for new_lane in lane_options:
                    cell = (round(note.beat, 3), new_lane, row)
                    if cell not in occupied:
                        target = (new_lane, row)
                        occupied.add(cell)
                        break
                if target is not None:
                    break
            if target is None:
                return False
            moves.append((note, target[0], target[1]))
        for note, new_lane, new_row in moves:
            note.lane = new_lane
            note.row = new_row
        return True

    for i, sec in enumerate(secs):
        if len(walls) >= max_walls:
            break
        if sec.label not in ("drop", "build", "body", "verse"):
            continue
        section_floor = energy_floor + (0.08 if sec.label == "verse" else 0.0)
        if sec.energy < section_floor:
            continue
        end_t = secs[i + 1].t if i + 1 < len(secs) else analysis.duration
        reaction = max(0.75, 2.0 * 60.0 / analysis.bpm)
        candidates = [
            t
            for t in analysis.beats
            if sec.t + reaction <= t < end_t - 2.0 * 60.0 / analysis.bpm
        ]
        if not candidates:
            continue

        section_beats = max(1.0, (end_t - sec.t) * analysis.bpm / 60.0)
        phrase_length = 16.0 if style.name == "chill" else 8.0
        target_count = min(2 if style.name == "chill" else 3, max(1, math.ceil(section_beats / phrase_length)))
        placed_here = 0
        for t in candidates:
            beat = time_to_beat(t, analysis.beats, analysis.bpm)
            if beat < last_wall_beat + wall_spacing:
                continue

            use_crouch = (
                style.name != "chill"
                and placed_here == target_count - 1
                and target_count >= 3
            )
            wall: Obstacle | None = None
            if use_crouch:
                dur = 1.5
                approach_window = (beat - 1.0, beat + dur + 0.75)
                if any(
                    approach_window[0] <= n.beat <= approach_window[1]
                    and (
                        n.row == 2
                        or (
                            beat - 0.75 <= n.beat <= beat
                            and n.cut in ("up", "up_left", "up_right")
                        )
                    )
                    for n in notes
                ):
                    use_crouch = False
                else:
                    wall = Obstacle(
                        t=t,
                        beat=beat,
                        duration_beats=dur,
                        lane=0,
                        row=2,
                        width=4,
                        height=3,
                    )

            if not use_crouch:
                dur = 2.0
                wall_window = (beat - 0.5, beat + dur + 0.5)
                for offset in range(2):
                    lane = side_order[(side_index + offset) % 2]
                    if not clear_wall_lanes(lane, 2, wall_window[0], wall_window[1]):
                        continue
                    wall = Obstacle(
                        t=t,
                        beat=beat,
                        duration_beats=dur,
                        lane=lane,
                        row=0,
                        width=2,
                        height=5,
                    )
                    side_index = (side_order.index(lane) + 1) % 2
                    break

            if wall is not None:
                walls.append(wall)
                placed_here += 1
                last_wall_beat = beat
                if len(walls) >= max_walls:
                    break
                if placed_here >= target_count:
                    break

    # Even low-energy or unusually segmented songs need at least one visible
    # obstacle on difficulties that enable walls. Use a readable side dodge
    # instead of silently exporting an empty list.
    if not walls and analysis.duration >= 8.0:
        reaction = max(3.0, 2.0 * 60.0 / analysis.bpm)
        for t in analysis.beats:
            if t < reaction or t >= analysis.duration - 2.5:
                continue
            beat = time_to_beat(t, analysis.beats, analysis.bpm)
            for lane in side_order:
                if clear_wall_lanes(lane, 2, beat - 0.5, beat + 2.0):
                    walls.append(
                        Obstacle(
                            t=t,
                            beat=beat,
                            duration_beats=1.5,
                            lane=lane,
                            row=0,
                            width=2,
                            height=5,
                        )
                    )
                    return walls
    return walls
