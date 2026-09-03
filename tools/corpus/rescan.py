"""Re-scan every downloaded map with the extended fingerprint (v2 metrics).

Reads data/corpus/maps/<id>/ (dat files + meta.json), recomputes fingerprints,
and rewrites data/corpus/fingerprints/<id>.json. Parallel across processes.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "corpus"
MAPS = CORPUS / "maps"
FP_DIR = CORPUS / "fingerprints"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fingerprint import fingerprint_map  # noqa: E402
from parse_maps import parse_map_folder  # noqa: E402


def rescan_one(map_dir: Path) -> str:
    mid = map_dir.name
    meta_path = map_dir / "meta.json"
    meta: dict = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    parsed = parse_map_folder(map_dir, map_id=mid)
    if parsed is None:
        return f"skip {mid}: unparseable"
    rows = fingerprint_map(parsed, meta)
    out = FP_DIR / f"{mid}.json"
    out.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    return f"ok {mid} ({len(rows)} diffs)"


def main(argv: list[str] | None = None) -> int:
    workers = 8
    if argv and argv[0].isdigit():
        workers = int(argv[0])
    FP_DIR.mkdir(parents=True, exist_ok=True)
    dirs = sorted(d for d in MAPS.iterdir() if d.is_dir()) if MAPS.is_dir() else []
    print(f"Rescanning {len(dirs)} maps with {workers} workers …")
    t0 = time.time()
    done = failed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(rescan_one, d): d.name for d in dirs}
        for fut in as_completed(futures):
            try:
                msg = fut.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                msg = f"FAIL {futures[fut]}: {e}"
            if (done + failed) % 1000 == 0:
                print(f"  {(done + failed)}/{len(dirs)} ({time.time() - t0:.0f}s)")
    print(f"Done: {done} ok, {failed} failed in {time.time() - t0:.0f}s")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
