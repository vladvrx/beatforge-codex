"""Export high-rated maps as compact charts + a few full .dat examples.

Output stays under data/corpus/ (gitignored): third-party map contents never
enter the repository. Earlier revisions wrote to examples/beatmaps; that copy
was removed from the repo and must not be recreated.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "corpus"
MAPS = CORPUS / "maps"
OUT = CORPUS / "layout-pack"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_beatsaver import load_json  # noqa: E402
from parse_maps import find_info_dat, parse_map_folder  # noqa: E402

DIFF_ORDER = ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
CUT = {
    0: "up",
    1: "down",
    2: "left",
    3: "right",
    4: "up_left",
    5: "up_right",
    6: "down_left",
    7: "down_right",
    8: "dot",
}


def _round(v: float, n: int = 3) -> float:
    return round(float(v), n)


def compact_row(parsed, meta: dict) -> dict | None:
    std = [d for d in parsed.difficulties if d.characteristic.lower() == "standard"]
    if not std:
        return None
    std.sort(key=lambda d: DIFF_ORDER.index(d.difficulty) if d.difficulty in DIFF_ORDER else 99)
    diffs = []
    for d in std:
        diffs.append(
            {
                "difficulty": d.difficulty,
                "njs": d.njs,
                "version": d.version,
                "notes": [
                    [_round(n.beat), n.x, n.y, n.color, n.cut] for n in d.notes
                ],
                "bombs": [[_round(b.beat), b.x, b.y] for b in d.bombs],
                "walls": [
                    [_round(w.beat), w.x, w.y, _round(w.duration), w.width, w.height]
                    for w in d.walls
                ],
            }
        )
    return {
        "id": parsed.map_id,
        "name": parsed.title,
        "artist": parsed.artist,
        "bpm": _round(parsed.bpm, 3),
        "rating_pct": meta.get("rating_pct"),
        "votes": meta.get("votes"),
        "tags": (meta.get("tags") or [])[:8],
        "note_schema": ["beat", "x", "y", "color", "cut"],
        "color": {"0": "left_red", "1": "right_blue"},
        "cut": CUT,
        "grid": {"x": "0 leftmost .. 3 rightmost", "y": "0 bottom .. 2 top"},
        "diffs": diffs,
    }


def copy_full_examples(catalog: dict, limit: int = 12) -> list[str]:
    """Copy small, high-rated maps as readable Info.dat + difficulty files."""
    dest = OUT / "full"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    ranked = sorted(
        (
            (mid, meta)
            for mid, meta in catalog.items()
            if isinstance(meta.get("rating_pct"), (int, float))
        ),
        key=lambda kv: (-float(kv[1]["rating_pct"]), -int(kv[1].get("votes") or 0)),
    )
    copied = []
    for mid, meta in ranked:
        folder = MAPS / mid
        info = find_info_dat(folder) if folder.is_dir() else None
        if info is None:
            continue
        # Skip huge lightshow dumps
        dat_files = [p for p in info.parent.glob("*.dat")]
        total = sum(p.stat().st_size for p in dat_files)
        if total > 400_000:
            continue
        slot = dest / mid
        slot.mkdir(parents=True, exist_ok=True)
        for p in dat_files:
            shutil.copy2(p, slot / p.name)
        meta_path = slot / "meta.json"
        meta_path.write_text(
            json.dumps(
                {
                    "id": mid,
                    "name": meta.get("name"),
                    "rating_pct": meta.get("rating_pct"),
                    "votes": meta.get("votes"),
                    "tags": meta.get("tags") or [],
                    "bpm": meta.get("bpm"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        copied.append(mid)
        if len(copied) >= limit:
            break
    return copied


def main() -> int:
    catalog = load_json(CORPUS / "catalog.json", {})
    if not catalog:
        print("No filtered catalog", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    gz_path = OUT / "charts.jsonl.gz"
    n_ok = 0
    n_fail = 0
    with gzip.open(gz_path, "wt", encoding="utf-8") as fh:
        for i, (mid, meta) in enumerate(catalog.items(), 1):
            folder = MAPS / mid
            try:
                parsed = parse_map_folder(folder, map_id=mid) if folder.is_dir() else None
                row = compact_row(parsed, meta) if parsed else None
            except Exception:
                row = None
            if not row:
                n_fail += 1
                continue
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            n_ok += 1
            if i % 500 == 0:
                print(f"  packed {i}/{len(catalog)}")
    examples = copy_full_examples(catalog)
    index = {
        "threshold_pct": 85,
        "maps_in_catalog": len(catalog),
        "charts_packed": n_ok,
        "parse_failed": n_fail,
        "charts_file": "charts.jsonl.gz",
        "full_example_ids": examples,
        "note_tuple": ["beat", "x (0-3)", "y (0-2)", "color 0=red/left 1=blue/right", "cut 0-8"],
        "cut_enum": CUT,
    }
    (OUT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    mb = gz_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {n_ok} charts ({mb:.1f} MB gzip), {len(examples)} full examples, fail={n_fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
