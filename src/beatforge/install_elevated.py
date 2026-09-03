"""Restricted Windows helper for installing one generated Beatforge job."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from beatforge.install import install_map


ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = ROOT / "data" / "jobs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{12}", args.job_id):
        parser.error("invalid job id")

    job_dir = JOBS_DIR / args.job_id
    zip_path = job_dir / "map.zip"
    status_path = job_dir / "status.json"
    result_path = job_dir / "install-result.json"
    result: dict[str, object]
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        title = str(status.get("title") or "beatforge_map")
        dest = install_map(zip_path, title=title)
        result = {"installed": True, "path": str(dest)}
        code = 0
    except Exception as exc:
        result = {
            "installed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        code = 1
    job_dir.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
