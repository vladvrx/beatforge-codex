"""Build a nearest-neighbor index over corpus fingerprints for provenance."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "corpus"
FP_DIR = CORPUS / "fingerprints"
OUT = CORPUS / "nn_index.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fingerprint import feature_vector, iter_standard_fps  # noqa: E402


def main() -> int:
    t0 = time.time()
    rows = []
    for p in sorted(FP_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.extend(data if isinstance(data, list) else [data])
    std = iter_standard_fps(rows)
    print(f"{len(std)} Standard diffs loaded in {time.time() - t0:.1f}s")

    entries = []
    for r in std:
        vec = [round(v, 5) for v in feature_vector(r)]
        entries.append(
            {
                "map_id": r.get("map_id"),
                "title": r.get("title"),
                "artist": r.get("artist"),
                "difficulty": r.get("difficulty"),
                "nps": r.get("nps"),
                "tags": (r.get("tags") or [])[:4],
                "vec": vec,
            }
        )

    # Standardization stats so generated charts live in the same space.
    n_dim = len(entries[0]["vec"]) if entries else 0
    mean = [0.0] * n_dim
    stdv = [1.0] * n_dim
    if entries:
        for d in range(n_dim):
            col = [e["vec"][d] for e in entries]
            m = sum(col) / len(col)
            var = sum((x - m) ** 2 for x in col) / len(col)
            mean[d] = round(m, 6)
            stdv[d] = round(max(var**0.5, 1e-6), 6)

    OUT.write_text(
        json.dumps({"dim": n_dim, "mean": mean, "std": stdv, "rows": entries},
                   separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB) in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
