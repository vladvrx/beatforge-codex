#!/usr/bin/env python3
"""Create or verify the byte-identical Codex skill copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "skills" / "beat-saber-mapping"
TARGETS = (
    ROOT / ".codex" / "skills" / "beat-saber-mapping",
)
LOCK = ROOT / "skill-lock.json"


def files(root: Path) -> dict[str, Path]:
    skip_dirs = {"__pycache__", ".pytest_cache"}
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix != ".pyc"
        and not any(part in skip_dirs for part in path.parts)
    }


def digest(path: Path) -> str:
    data = path.read_bytes()
    if b"\0" not in data[:1024]:
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def write_copies() -> dict[str, str]:
    source = files(CANONICAL)
    for target in TARGETS:
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for relative, source_path in source.items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
    hashes = {relative: digest(path) for relative, path in sorted(source.items())}
    LOCK.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "skill": "beat-saber-mapping",
                "canonical": "skills/beat-saber-mapping",
                "copies": [".codex/skills/beat-saber-mapping"],
                "files": hashes,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return hashes


def check() -> list[str]:
    failures: list[str] = []
    source = files(CANONICAL)
    expected = {relative: digest(path) for relative, path in source.items()}
    if not LOCK.is_file():
        failures.append("skill-lock.json is missing")
    else:
        locked = json.loads(LOCK.read_text(encoding="utf-8")).get("files", {})
        expected_sorted = dict(sorted(expected.items()))
        if locked != expected_sorted:
            failures.append("skill-lock.json does not match the canonical skill")
            locked_keys = set(locked)
            expected_keys = set(expected_sorted)
            for relative in sorted(expected_keys - locked_keys):
                failures.append(f"skill-lock.json missing {relative}")
            for relative in sorted(locked_keys - expected_keys):
                failures.append(f"skill-lock.json extra {relative}")
            for relative in sorted(expected_keys & locked_keys):
                if locked[relative] != expected_sorted[relative]:
                    failures.append(f"skill-lock.json hash mismatch {relative}")
    for target in TARGETS:
        actual_files = files(target) if target.is_dir() else {}
        if set(actual_files) != set(source):
            failures.append(f"{target.relative_to(ROOT)} has a different file set")
            continue
        for relative, expected_hash in expected.items():
            if digest(actual_files[relative]) != expected_hash:
                failures.append(f"{target.relative_to(ROOT)}/{relative} drifted")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        write_copies()
    failures = check()
    for failure in failures:
        print(f"ERROR: {failure}")
    if not failures:
        print("beat-saber-mapping copies are byte-identical")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
