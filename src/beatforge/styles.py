"""LEGACY — style profiles for the pre-premium pipeline. The studio pipeline
derives difficulty profiles from the local official corpus instead (see
docs/ARCHITECTURE.md).

Corpus-derived style profiles and difficulty ladders."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StyleProfile:
    name: str
    # Probability weight for allowing tech resets (opposite cuts)
    reset_tolerance: float
    # Prefer continuous swing chains
    flow_bias: float
    # Target relative density multiplier vs base difficulty
    density_scale: float
    # Chance of double (jump) on strong beats
    jump_chance: float
    # Prefer bottom row (0) vs using full height
    verticality: float
    # Motif reuse probability in verse sections
    motif_reuse: float
    # Allow cross-lane (left hand on right columns)
    cross_chance: float
    # Eighth-note stream bias in drops
    stream_bias: float


# Values tuned to style-family medians measured across 27,196 Expert+ Standard
# corpus diffs (data/corpus/style_family_medians.json):
#   flow  n=20993: reset .030 flow .970 jump .295 cross .249 motif .603
#   tech  n=1629:  reset .538 flow .462 jump .261 motif .867 stream .104
#   speed n=1521:  stream .606 nps 10.9 jump .307 dot .079 lane_chg .578
#   chill n=301:   nps 1.86 jump .204 dot .015 bomb .000 flow .995
STYLES: dict[str, StyleProfile] = {
    "flow": StyleProfile(
        name="flow",
        reset_tolerance=0.05,
        flow_bias=0.95,
        density_scale=1.0,
        jump_chance=0.32,
        verticality=0.4,
        motif_reuse=0.6,
        cross_chance=0.16,
        stream_bias=0.12,
    ),
    "tech": StyleProfile(
        name="tech",
        # Corpus tech runs ~54% same-cut transitions; allow chains to 4.
        reset_tolerance=0.45,
        flow_bias=0.50,
        density_scale=0.95,
        jump_chance=0.26,
        verticality=0.65,
        motif_reuse=0.65,
        cross_chance=0.16,
        stream_bias=0.10,
    ),
    "speed": StyleProfile(
        name="speed",
        reset_tolerance=0.06,
        flow_bias=0.85,
        density_scale=1.3,
        jump_chance=0.18,
        verticality=0.35,
        motif_reuse=0.55,
        cross_chance=0.06,
        stream_bias=0.75,
    ),
    "chill": StyleProfile(
        name="chill",
        reset_tolerance=0.02,
        flow_bias=0.96,
        density_scale=0.7,
        jump_chance=0.20,
        verticality=0.25,
        motif_reuse=0.5,
        cross_chance=0.04,
        stream_bias=0.05,
    ),
}


@dataclass(frozen=True)
class DifficultyBudget:
    name: str
    rank: int
    target_nps: float
    subdivision: float  # fraction of beat (1=quarter, 0.5=eighth)
    min_hand_gap_beats: float
    jump_scale: float
    use_walls: bool
    row_bias_bottom: float
    njs: float
    offset: float


# Difficulty ladders from median NPS across 19,403 curated+verified Standard maps
# (BeatSaver playlists?order=Curated&verified=true). See docs/style-study.md.
DIFFICULTIES: dict[str, DifficultyBudget] = {
    "Easy": DifficultyBudget(
        name="Easy",
        rank=1,
        target_nps=1.59,
        subdivision=1.0,
        min_hand_gap_beats=1.0,
        jump_scale=0.25,
        use_walls=False,
        row_bias_bottom=0.9,
        njs=10.0,
        offset=0.0,
    ),
    "Normal": DifficultyBudget(
        name="Normal",
        rank=3,
        target_nps=2.5,
        subdivision=1.0,
        min_hand_gap_beats=0.75,
        jump_scale=0.5,
        use_walls=False,
        row_bias_bottom=0.75,
        njs=10.0,
        offset=0.0,
    ),
    "Hard": DifficultyBudget(
        name="Hard",
        rank=5,
        target_nps=3.27,
        subdivision=0.5,
        min_hand_gap_beats=0.5,
        jump_scale=0.75,
        use_walls=False,
        row_bias_bottom=0.55,
        njs=12.0,
        offset=0.0,
    ),
    "Expert": DifficultyBudget(
        name="Expert",
        rank=7,
        target_nps=4.25,
        subdivision=0.5,
        min_hand_gap_beats=0.35,
        jump_scale=0.8,
        use_walls=True,
        row_bias_bottom=0.4,
        njs=16.0,
        offset=0.0,
    ),
    "ExpertPlus": DifficultyBudget(
        name="ExpertPlus",
        rank=9,
        target_nps=5.47,
        subdivision=0.5,
        min_hand_gap_beats=0.25,
        jump_scale=0.9,
        use_walls=True,
        row_bias_bottom=0.3,
        njs=18.0,
        offset=0.0,
    ),
}


def pick_style(name: str, bpm: float, mean_energy: float) -> StyleProfile:
    if name and name != "auto" and name in STYLES:
        return STYLES[name]
    # Auto-pick from audio only; tech is a mapping choice, never inferred.
    if bpm >= 150 and mean_energy >= 0.55:
        return STYLES["speed"]
    if mean_energy < 0.4:
        return STYLES["chill"]
    return STYLES["flow"]


# Swing continuations: preferred next cut after current (flow)
FLOW_NEXT: dict[int, list[int]] = {
    0: [1, 6, 7],  # up -> down family
    1: [0, 4, 5],  # down -> up family
    2: [3, 5, 7],  # left -> right family
    3: [2, 4, 6],  # right -> left family
    4: [7, 1, 3],
    5: [6, 1, 2],
    6: [5, 0, 3],
    7: [4, 0, 2],
    8: [1, 0, 2, 3],
}

# Opposite / reset cuts
RESET_CUTS: dict[int, list[int]] = {
    0: [0],
    1: [1],
    2: [2],
    3: [3],
    4: [4, 0],
    5: [5, 0],
    6: [6, 1],
    7: [7, 1],
    8: [8],
}
