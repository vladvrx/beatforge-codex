"""Cluster fingerprints and write docs/style-study.md from the curated corpus."""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "corpus"
DOCS = ROOT / "docs"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))

from fingerprint import feature_vector, iter_standard_fps  # noqa: E402

DIFF_ORDER = ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
CLUSTER_HINTS = {
    "flow": "continuous swings, high flow_ratio, moderate density",
    "tech": "resets, verticality, cross-hand, lower stream",
    "speed": "high NPS, high stream/0.25 IOI, fewer jumps",
    "chill": "low NPS, high flow, bottom-row bias",
    "jump": "high jump_ratio, doubles on strong beats",
    "balanced": "mid NPS, mixed vocabulary",
}


def load_fingerprints() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fp_dir = CORPUS / "fingerprints"
    if not fp_dir.is_dir():
        return rows
    for path in sorted(fp_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            rows.extend(data)
        elif isinstance(data, dict):
            rows.append(data)
    return rows


def median(xs: list[float], default: float = 0.0) -> float:
    if not xs:
        return default
    return float(statistics.median(xs))


def mean(xs: list[float], default: float = 0.0) -> float:
    if not xs:
        return default
    return float(statistics.fmean(xs))


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round((p / 100) * (len(s) - 1)))))
    return float(s[i])


def summarize(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for k in keys:
        xs = [float(r.get(k) or 0) for r in rows]
        out[k] = {
            "n": len(xs),
            "mean": round(mean(xs), 4),
            "median": round(median(xs), 4),
            "p25": round(pct(xs, 25), 4),
            "p75": round(pct(xs, 75), 4),
        }
    return out


def name_cluster(center: dict[str, float]) -> str:
    nps = center.get("nps", 0)
    flow = center.get("flow_ratio", 0)
    reset = center.get("reset_ratio", 0)
    stream = center.get("stream_ratio", 0)
    jump = center.get("jump_ratio", 0)
    cross = center.get("cross_ratio", 0)
    contrast = center.get("section_contrast", 0)
    scores = {
        "speed": stream * 2 + nps / 8 + (1 - jump),
        "tech": reset * 3 + cross * 2 + center.get("spatial_travel", 0),
        "flow": flow * 2 + (1 - reset) + contrast * 0.3,
        "chill": (1 / (nps + 0.5)) + flow + (1 - stream),
        "jump": jump * 4,
        "balanced": 1.0 - abs(nps - 4) / 8,
    }
    return max(scores, key=scores.get)


def cluster_rows(rows: list[dict[str, Any]], k: int = 6) -> tuple[list[int], list[list[float]]]:
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    X = np.array([feature_vector(r) for r in rows], dtype=float)
    k = max(2, min(k, len(rows)))
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    model = KMeans(n_clusters=k, n_init=10, random_state=7)
    labels = model.fit_predict(Xs)
    centers = scaler.inverse_transform(model.cluster_centers_)
    return labels.tolist(), centers.tolist()


def scaling_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_map: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if str(r.get("characteristic", "")).lower() != "standard":
            continue
        by_map[str(r.get("map_id"))][str(r.get("difficulty"))] = float(r.get("nps") or 0)
    ratios: dict[str, list[float]] = defaultdict(list)
    nps_by_diff: dict[str, list[float]] = defaultdict(list)
    for diffs in by_map.values():
        for d, nps in diffs.items():
            nps_by_diff[d].append(nps)
        if "Easy" in diffs and "ExpertPlus" in diffs and diffs["Easy"] > 0.2:
            ratios["Ex+/Easy"].append(diffs["ExpertPlus"] / diffs["Easy"])
        if "Normal" in diffs and "Expert" in diffs and diffs["Normal"] > 0.2:
            ratios["Expert/Normal"].append(diffs["Expert"] / diffs["Normal"])
        if "Hard" in diffs and "Expert" in diffs and diffs["Hard"] > 0.2:
            ratios["Expert/Hard"].append(diffs["Expert"] / diffs["Hard"])
    out: dict[str, dict[str, float]] = {}
    for d in DIFF_ORDER:
        xs = nps_by_diff.get(d) or []
        out[d] = {
            "count": len(xs),
            "nps_median": round(median(xs), 3),
            "nps_p25": round(pct(xs, 25), 3),
            "nps_p75": round(pct(xs, 75), 3),
        }
    out["ratios"] = {k: round(median(v), 3) for k, v in ratios.items()}  # type: ignore[assignment]
    return out


def anti_patterns(rows: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    n = len(rows) or 1
    stacked = sum(1 for r in rows if float(r.get("min_hand_gap_beats") or 0) < 0.12 and float(r.get("nps") or 0) > 3)
    if stacked / n < 0.15:
        findings.append(
            "Same-hand gaps under ~1/8 at moderate+ density are uncommon in curated maps "
            f"({stacked}/{len(rows)}). Treat sub-0.125 same-hand spacing as an anti-pattern except in dedicated speed streams."
        )
    high_reset_flow = sum(
        1
        for r in rows
        if float(r.get("reset_ratio") or 0) > 0.35 and float(r.get("flow_ratio") or 0) > 0.6
    )
    findings.append(
        f"Mixed high-reset+high-flow charts are rare ({high_reset_flow}/{len(rows)}); "
        "pick a lane: either flow-forward swings or intentional tech resets, not both at once."
    )
    early_cross = sum(1 for r in rows if float(r.get("cross_ratio") or 0) > 0.35)
    findings.append(
        f"Heavy cross-hand occupancy (>35% of notes) appears in {early_cross}/{len(rows)} diffs — "
        "keep hands mostly on their sides except in tech/cross styles."
    )
    no_contrast = sum(
        1 for r in rows if float(r.get("section_contrast") or 0) < 0.08 and float(r.get("nps") or 0) > 4
    )
    findings.append(
        f"High-NPS maps with almost flat density curves are uncommon ({no_contrast}/{len(rows)}); "
        "intros/outros should breathe even on Expert+."
    )
    return findings


def cluster_feature_means(rows: list[dict[str, Any]], labels: list[int]) -> dict[int, dict[str, float]]:
    keys = [
        "nps",
        "jump_ratio",
        "reset_ratio",
        "flow_ratio",
        "cross_ratio",
        "stream_ratio",
        "section_contrast",
        "motif_reuse",
        "dot_ratio",
        "spatial_travel",
        "hand_alt_ratio",
        "bomb_rate",
        "wall_rate",
    ]
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r, lab in zip(rows, labels):
        groups[lab].append(r)
    out: dict[int, dict[str, float]] = {}
    for lab, rs in groups.items():
        out[lab] = {k: round(mean([float(x.get(k) or 0) for x in rs]), 4) for k in keys}
        out[lab]["size"] = float(len(rs))
        top = Counter(t for r in rs for t in (r.get("tags") or [])).most_common(6)
        out[lab]["_tags"] = top  # type: ignore[assignment]
    return out


def signature_maps(rows: list[dict[str, Any]], labels: list[int], centers: list[list[float]]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r, lab in zip(rows, labels):
        vec = feature_vector(r)
        c = centers[lab]
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(vec, c)))
        item = {
            "map_id": r.get("map_id"),
            "title": r.get("title"),
            "difficulty": r.get("difficulty"),
            "dist": round(dist, 3),
            "nps": r.get("nps"),
            "reset_ratio": r.get("reset_ratio"),
            "flow_ratio": r.get("flow_ratio"),
            "jump_ratio": r.get("jump_ratio"),
            "stream_ratio": r.get("stream_ratio"),
            "tags": r.get("tags") or [],
        }
        out[lab].append(item)
    for lab in out:
        out[lab].sort(key=lambda x: x["dist"], reverse=True)
        out[lab] = out[lab][:8]
    return out


def recommended_styles(scale: dict[str, Any], expert_rows: list[dict[str, Any]]) -> dict[str, Any]:
    def m(key: str) -> float:
        return median([float(r.get(key) or 0) for r in expert_rows])

    diffs = {}
    defaults = {
        "Easy": (1.8, 1.0, 1.0, 0.15, False, 0.9, 10.0),
        "Normal": (2.8, 1.0, 0.75, 0.35, False, 0.75, 10.0),
        "Hard": (4.2, 0.5, 0.5, 0.55, False, 0.55, 12.0),
        "Expert": (6.0, 0.5, 0.35, 0.75, True, 0.4, 16.0),
        "ExpertPlus": (8.0, 0.5, 0.25, 0.9, True, 0.3, 18.0),
    }
    for name, fallback in defaults.items():
        stats = scale.get(name) or {}
        nps = float(stats.get("nps_median") or fallback[0])
        diffs[name] = {
            "target_nps": round(nps, 2),
            "subdivision": fallback[1],
            "min_hand_gap_beats": fallback[2],
            "jump_scale": fallback[3],
            "use_walls": fallback[4],
            "row_bias_bottom": fallback[5],
            "njs": fallback[6],
        }
    return {
        "difficulties": diffs,
        "expert_medians": {
            "flow_ratio": round(m("flow_ratio"), 3),
            "reset_ratio": round(m("reset_ratio"), 3),
            "jump_ratio": round(m("jump_ratio"), 3),
            "stream_ratio": round(m("stream_ratio"), 3),
            "cross_ratio": round(m("cross_ratio"), 3),
            "motif_reuse": round(m("motif_reuse"), 3),
            "section_contrast": round(m("section_contrast"), 3),
            "dot_ratio": round(m("dot_ratio"), 3),
        },
    }


def render_md(
    *,
    n_maps: int,
    n_playlists: int,
    n_fps: int,
    n_std: int,
    scale: dict[str, Any],
    keys_summary: dict[str, dict[str, float]],
    clusters: list[dict[str, Any]],
    signatures: dict[int, list[dict[str, Any]]],
    antis: list[str],
    recs: dict[str, Any],
    incomplete: bool,
) -> str:
    lines: list[str] = []
    lines.append("# Beatforge style study")
    lines.append("")
    lines.append(
        "Principles distilled from **every map** in **every curated + verified** "
        "BeatSaver playlist (`order=Curated&verified=true`). Notes are never copied — "
        "only aggregate rhythm, density, flow, and spatial statistics inform Beatforge placement."
    )
    lines.append("")
    if incomplete:
        lines.append(
            "> Corpus download was still running or incomplete when this report was generated. "
            "Re-run `python tools/corpus/compare_styles.py` after `fetch_beatsaver.py` finishes."
        )
        lines.append("")
    lines.append("## Corpus")
    lines.append("")
    lines.append(f"- Playlists enumerated: **{n_playlists}**")
    lines.append(f"- Unique maps fingerprinted: **{n_maps}**")
    lines.append(f"- Difficulty files scored: **{n_fps}** ({n_std} Standard)")
    lines.append("- Automapper maps skipped at catalog time")
    lines.append("- All Standard difficulties are fingerprinted (Easy through Expert+ when present)")
    lines.append("")
    lines.append("## Easy → Expert+ scaling")
    lines.append("")
    lines.append("| Difficulty | Maps | NPS median | p25 | p75 |")
    lines.append("|---|---:|---:|---:|---:|")
    for d in DIFF_ORDER:
        s = scale.get(d) or {}
        lines.append(
            f"| {d} | {int(s.get('count') or 0)} | {s.get('nps_median', 0)} | "
            f"{s.get('nps_p25', 0)} | {s.get('nps_p75', 0)} |"
        )
    lines.append("")
    ratios = scale.get("ratios") or {}
    if ratios:
        lines.append("Typical full-spread ratios (median across maps that include both diffs):")
        for k, v in ratios.items():
            lines.append(f"- {k}: **{v}×**")
        lines.append("")
    lines.append(
        "Ladder rule: Easy is mostly quarter-note bottom-row reading; Hard is where 1/8 "
        "vocabulary becomes common; Expert is the 'complete' chart; Expert+ adds density, "
        "height, and tighter hand gaps rather than a new genre of pattern."
    )
    lines.append("")
    lines.append("## Expert-level fingerprint medians (Standard)")
    lines.append("")
    lines.append("| Feature | mean | median | p25 | p75 |")
    lines.append("|---|---:|---:|---:|---:|")
    for k, s in keys_summary.items():
        lines.append(f"| `{k}` | {s['mean']} | {s['median']} | {s['p25']} | {s['p75']} |")
    lines.append("")
    lines.append("## Style families (k-means on Standard diffs)")
    lines.append("")
    for c in clusters:
        lines.append(f"### Cluster {c['id']}: **{c['name']}** ({int(c['size'])} diffs)")
        lines.append("")
        lines.append(c["blurb"])
        lines.append("")
        lines.append(
            f"- NPS {c['means']['nps']}, flow {c['means']['flow_ratio']}, "
            f"reset {c['means']['reset_ratio']}, stream {c['means']['stream_ratio']}, "
            f"jumps {c['means']['jump_ratio']}, cross {c['means']['cross_ratio']}"
        )
        tags = c.get("tags") or []
        if tags:
            tag_s = ", ".join(f"{t} ({n})" for t, n in tags)
            lines.append(f"- Common BeatSaver tags: {tag_s}")
        lines.append("")
        sigs = signatures.get(c["id"]) or []
        if sigs:
            lines.append("Stand-outs vs cluster mean (largest feature distance — study, do not clone):")
            for s in sigs[:5]:
                lines.append(
                    f"- `{s['map_id']}` {s.get('title')} [{s.get('difficulty')}] "
                    f"dist={s['dist']} nps={s.get('nps')} flow={s.get('flow_ratio')} "
                    f"reset={s.get('reset_ratio')} tags={', '.join(s.get('tags')[:4])}"
                )
            lines.append("")
    lines.append("## Universal good-map rules")
    lines.append("")
    em = recs.get("expert_medians") or {}
    lines.append(
        f"1. **Parity / flow first.** Expert median `flow_ratio` is {em.get('flow_ratio', '—')}. "
        "Each hand should mostly swing opposite the previous cut; resets are seasoning, not the meal."
    )
    lines.append(
        f"2. **Hands stay home.** Median `cross_ratio` {em.get('cross_ratio', '—')}. "
        "Left lives in columns 0–1, right in 2–3, with rare intentional crosses."
    )
    lines.append(
        f"3. **Doubles are accents.** Median `jump_ratio` {em.get('jump_ratio', '—')}. "
        "Place two-hand hits on strong onsets / high energy, not as default density padding."
    )
    lines.append(
        f"4. **Section contrast.** Median `section_contrast` {em.get('section_contrast', '—')}. "
        "Intros and outros must be thinner than drops even when the song is loud the whole way."
    )
    lines.append(
        f"5. **Motifs repeat.** Median `motif_reuse` {em.get('motif_reuse', '—')}. "
        "Verse/drop patterns should recur; do not generate independent random notes every bar."
    )
    lines.append(
        f"6. **Dots are rare.** Median `dot_ratio` {em.get('dot_ratio', '—')}. "
        "Use dots for percussion ornaments, not as an escape from parity."
    )
    lines.append(
        "7. **Same-hand spacing scales with difficulty.** Easy ≥ 1 beat, Hard ≥ ½, "
        "Expert ~⅓, Expert+ can stream ¼ in speed maps only."
    )
    lines.append(
        "8. **Bombs and walls support reading, they are not NPS.** Curated maps use them as "
        "punctuation in drops, not as a substitute for notes."
    )
    lines.append(
        "9. **Full spreads teach.** When generating multiple diffs, keep the same motif skeleton "
        "and thin/subdivide rather than inventing unrelated charts."
    )
    lines.append("")
    lines.append("## Anti-patterns")
    lines.append("")
    for a in antis:
        lines.append(f"- {a}")
    lines.append("- Stacking two notes in the same cell on the same beat.")
    lines.append("- Face-plants: both sabers cutting inward/down into each other on inner columns.")
    lines.append("- Notes in the first ~2–3 seconds before the player can ready up.")
    lines.append("- Reset chains (same cut 3+ times on one hand) outside of tech maps.")
    lines.append("- Ignoring the kick/snare grid: off-grid spam that does not lock to 1/4 or 1/8.")
    lines.append("")
    lines.append("## Encoded into Beatforge")
    lines.append("")
    lines.append("Difficulty ladders (`src/beatforge/styles.py`) use corpus median NPS:")
    lines.append("")
    lines.append("| Diff | target NPS | subdivision | min hand gap (beats) | NJS |")
    lines.append("|---|---:|---:|---:|---:|")
    for name, d in (recs.get("difficulties") or {}).items():
        lines.append(
            f"| {name} | {d['target_nps']} | {d['subdivision']} | "
            f"{d['min_hand_gap_beats']} | {d['njs']} |"
        )
    lines.append("")
    lines.append(
        "Style profiles (flow / tech / speed / chill) are tuned to measured "
        "style-family medians (`data/corpus/style_family_medians.json`): tech runs "
        "~54% same-cut transitions, speed streams at ~0.61 with low lane changes, "
        "chill avoids bombs entirely."
    )
    lines.append("")
    lines.append(
        "Full spreads are **additive**: difficulties are placed easiest-first and each "
        "tier inherits every note of the previous tier (re-deriving cuts in its own "
        "context), then escalates inherited singles into doubles. Nesting is verified "
        "by the critic's spread check (rule 9)."
    )
    lines.append("")
    lines.append("## Reproduction")
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/corpus/fetch_beatsaver.py")
    lines.append("python tools/corpus/compare_styles.py")
    lines.append("```")
    lines.append("")
    return "\n".join(lines) + "\n"


def apply_styles(recs: dict[str, Any]) -> None:
    """Rewrite difficulty target_nps in styles.py from corpus medians."""
    styles_path = ROOT / "src" / "beatforge" / "styles.py"
    text = styles_path.read_text(encoding="utf-8")
    diffs = recs.get("difficulties") or {}
    for name, d in diffs.items():
        nps = d["target_nps"]
        # replace the first target_nps= inside each DifficultyBudget(name=...)
        needle = f'name="{name}"'
        idx = text.find(needle)
        if idx < 0:
            continue
        tidx = text.find("target_nps=", idx)
        if tidx < 0 or tidx > idx + 250:
            continue
        end = text.find(",", tidx)
        text = text[:tidx] + f"target_nps={nps}" + text[end:]
    styles_path.write_text(text, encoding="utf-8")


def main() -> int:
    playlists = []
    pl_path = CORPUS / "playlists.json"
    if pl_path.is_file():
        playlists = json.loads(pl_path.read_text(encoding="utf-8"))
    rows = load_fingerprints()
    std = iter_standard_fps(rows)
    n_maps = len({r.get("map_id") for r in rows})
    state = {}
    sp = CORPUS / "download_state.json"
    if sp.is_file():
        state = json.loads(sp.read_text(encoding="utf-8"))
    remaining = state.get("remaining") or []
    incomplete = bool(remaining) or n_maps == 0

    scale = scaling_table(rows)
    expert = [
        r
        for r in std
        if r.get("difficulty") in ("Expert", "ExpertPlus")
    ]
    keys = [
        "nps",
        "jump_ratio",
        "reset_ratio",
        "flow_ratio",
        "cross_ratio",
        "stream_ratio",
        "section_contrast",
        "motif_reuse",
        "dot_ratio",
        "hand_alt_ratio",
        "spatial_travel",
        "bomb_rate",
        "wall_rate",
        "same_col_repeat",
        "cut_entropy",
        "lane_change_rate",
        "triplet_ratio",
        "double_stack_rate",
    ]
    keys_summary = summarize(expert or std, keys)

    clusters_out: list[dict[str, Any]] = []
    signatures: dict[int, list[dict[str, Any]]] = {}
    if len(std) >= 8:
        labels, centers = cluster_rows(std, k=min(6, max(2, len(std) // 15 or 2)))
        means = cluster_feature_means(std, labels)
        used_names: set[str] = set()
        for lab, m in sorted(means.items()):
            name = name_cluster(m)
            if name in used_names:
                name = f"{name}-{lab}"
            used_names.add(name.split("-")[0] if False else name)
            clusters_out.append(
                {
                    "id": lab,
                    "name": name,
                    "size": m.get("size", 0),
                    "means": m,
                    "tags": m.get("_tags") or [],
                    "blurb": CLUSTER_HINTS.get(name.split("-")[0], "Mixed curated mapping vocabulary."),
                }
            )
        signatures = signature_maps(std, labels, centers)
    else:
        clusters_out.append(
            {
                "id": 0,
                "name": "insufficient-sample",
                "size": len(std),
                "means": {k: 0 for k in keys},
                "tags": [],
                "blurb": "Not enough Standard fingerprints to cluster yet.",
            }
        )

    antis = anti_patterns(expert or std)
    recs = recommended_styles(scale, expert or std)
    (CORPUS / "style_recommendations.json").parent.mkdir(parents=True, exist_ok=True)
    recs_path = CORPUS / "style_recommendations.json"
    recs_path.write_text(json.dumps(recs, indent=2), encoding="utf-8")

    md = render_md(
        n_maps=n_maps,
        n_playlists=len(playlists),
        n_fps=len(rows),
        n_std=len(std),
        scale=scale,
        keys_summary=keys_summary,
        clusters=clusters_out,
        signatures=signatures,
        antis=antis,
        recs=recs,
        incomplete=incomplete,
    )
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "style-study.md").write_text(md, encoding="utf-8")
    if n_maps:
        apply_styles(recs)
    print(f"Wrote {DOCS / 'style-study.md'} from {n_maps} maps / {len(std)} Standard diffs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
