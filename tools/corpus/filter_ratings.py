"""Drop corpus maps whose BeatSaver rating is below a percent threshold."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "corpus"
MAPS = CORPUS / "maps"
FP_DIR = CORPUS / "fingerprints"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_beatsaver import RateClient, load_json, save_json  # noqa: E402


def rating_percent(stats: dict[str, Any] | None) -> float | None:
    if not stats:
        return None
    up = float(stats.get("upvotes") or 0)
    down = float(stats.get("downvotes") or 0)
    total = up + down
    if total > 0:
        return 100.0 * up / total
    score = stats.get("score")
    if score is None:
        return None
    s = float(score)
    if 0.0 <= s <= 1.0:
        return 100.0 * s
    if 1.0 < s <= 100.0:
        return s
    return None


def fetch_ratings(client: RateClient, ids: list[str], cache: dict[str, Any]) -> dict[str, Any]:
    pending = [i for i in ids if i not in cache]
    print(f"Rating cache {len(cache)} already, {len(pending)} to fetch")
    for i in range(0, len(pending), 50):
        batch = pending[i : i + 50]
        url = f"https://api.beatsaver.com/maps/ids/{','.join(batch)}"
        data = client.get_json(url)
        if not isinstance(data, dict):
            data = {}
        for mid in batch:
            m = data.get(mid) or {}
            stats = m.get("stats") or {}
            pct = rating_percent(stats)
            cache[mid] = {
                "upvotes": stats.get("upvotes"),
                "downvotes": stats.get("downvotes"),
                "score": stats.get("score"),
                "rating_pct": None if pct is None else round(pct, 3),
                "votes": int((stats.get("upvotes") or 0) + (stats.get("downvotes") or 0)),
                "missing": not bool(m),
            }
        save_json(CORPUS / "ratings.json", cache)
        print(f"  ratings {min(i + 50, len(pending))}/{len(pending)}")
    return cache


def prune(threshold: float, dry_run: bool) -> dict[str, int]:
    catalog = load_json(CORPUS / "catalog.json", {})
    ratings = load_json(CORPUS / "ratings.json", {})
    keep: dict[str, Any] = {}
    drop: list[str] = []
    unrated: list[str] = []
    for mid, meta in catalog.items():
        rec = ratings.get(mid) or {}
        pct = rec.get("rating_pct")
        votes = int(rec.get("votes") or 0)
        if rec.get("missing"):
            drop.append(mid)
            continue
        if pct is None and votes == 0:
            # Curated but no votes yet — keep.
            unrated.append(mid)
            keep[mid] = meta
            meta["rating_pct"] = None
            meta["votes"] = 0
            continue
        if pct is None or pct < threshold:
            drop.append(mid)
            continue
        meta["rating_pct"] = pct
        meta["votes"] = votes
        keep[mid] = meta

    print(
        f"Keep {len(keep)} (>= {threshold}% or unrated={len(unrated)}), "
        f"drop {len(drop)}"
    )
    if dry_run:
        return {"kept": len(keep), "dropped": len(drop), "unrated": len(unrated)}

    removed_maps = 0
    removed_fp = 0
    for mid in drop:
        folder = MAPS / mid
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            removed_maps += 1
        fp = FP_DIR / f"{mid}.json"
        if fp.is_file():
            fp.unlink()
            removed_fp += 1

    save_json(CORPUS / "catalog.json", keep)
    save_json(
        CORPUS / "membership.json",
        {mid: meta.get("playlists") or [] for mid, meta in keep.items()},
    )
    save_json(
        CORPUS / "rating_filter.json",
        {
            "threshold_pct": threshold,
            "kept": len(keep),
            "dropped": len(drop),
            "unrated_kept": len(unrated),
            "removed_map_dirs": removed_maps,
            "removed_fingerprints": removed_fp,
        },
    )
    print(f"Removed {removed_maps} map folders, {removed_fp} fingerprints")
    return {
        "kept": len(keep),
        "dropped": len(drop),
        "unrated": len(unrated),
        "removed_maps": removed_maps,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--threshold", type=float, default=85.0)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    CORPUS.mkdir(parents=True, exist_ok=True)
    catalog = load_json(CORPUS / "catalog.json", {})
    if not catalog:
        print("No catalog.json — run fetch_beatsaver.py first", file=sys.stderr)
        return 1
    cache = load_json(CORPUS / "ratings.json", {})
    client = RateClient()
    try:
        fetch_ratings(client, list(catalog.keys()), cache)
    finally:
        client.close()
    prune(args.threshold, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
