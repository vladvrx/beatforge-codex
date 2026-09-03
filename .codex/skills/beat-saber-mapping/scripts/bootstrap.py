#!/usr/bin/env python3
"""Install pinned Beatforge dependencies into its private local cache."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


BEATNET_PLUS_COMMIT = "bb90eb0a9065b101a4b4c4cb2b2061950266cb4b"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


def default_target() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "Codex" / "beat-saber-mapping" / "python"
    return Path.home() / ".cache" / "beat-saber-mapping" / "python"


def python_abi_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def installed_torch_wheel_tags(target: Path) -> list[str]:
    wheels = sorted(target.glob("torch-*.dist-info/WHEEL"))
    tags: list[str] = []
    for wheel in wheels:
        for line in wheel.read_text(encoding="utf-8").splitlines():
            if line.startswith("Tag:"):
                tags.append(line.split(":", 1)[1].strip())
    return tags


def torch_abi_mismatch(target: Path) -> str | None:
    tags = installed_torch_wheel_tags(target)
    if not tags:
        return None
    abi = python_abi_tag()
    if any(abi in tag for tag in tags):
        return None
    return f"installed Torch tags {tags} do not match interpreter {abi} ({sys.executable})"


def verify_model_imports(target: Path) -> dict[str, str]:
    """Import Torch and the optional trackers from the managed cache."""
    probe = r"""
import importlib
import json
import sys
from pathlib import Path

target = Path(sys.argv[1])
beatnet_src = Path(sys.argv[2]) if len(sys.argv) > 2 else None
if str(target) not in sys.path:
    sys.path.append(str(target))
if beatnet_src and beatnet_src.is_dir() and str(beatnet_src) not in sys.path:
    sys.path.insert(0, str(beatnet_src))
report = {}
try:
    import torch
    report["torch"] = f"{torch.__version__} {torch.__file__}"
except Exception as exc:
    report["torch"] = f"ERROR: {type(exc).__name__}: {exc}"
for name in ("beat_this", "demucs"):
    try:
        module = importlib.import_module(name)
        report[name] = getattr(module, "__file__", "ok") or "ok"
    except Exception as exc:
        report[name] = f"ERROR: {type(exc).__name__}: {exc}"
try:
    importlib.import_module("allin1_infer")
    report["all-in-one"] = "allin1_infer"
except Exception:
    try:
        importlib.import_module("allin1")
        report["all-in-one"] = "allin1"
    except Exception as exc:
        report["all-in-one"] = f"ERROR: {type(exc).__name__}: {exc}"
try:
    importlib.import_module("BeatNetPlus")
    report["beatnet-plus"] = "BeatNetPlus"
except Exception as exc:
    report["beatnet-plus"] = f"ERROR: {type(exc).__name__}: {exc}"
print(json.dumps(report))
"""
    beatnet_src = target.parent / "models" / f"BeatNet-Plus-{BEATNET_PLUS_COMMIT[:12]}" / "src"
    result = subprocess.run(
        [sys.executable, "-c", probe, str(target), str(beatnet_src)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"model import probe exited {result.returncode}")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    failures = {name: message for name, message in payload.items() if str(message).startswith("ERROR:")}
    if failures:
        raise RuntimeError("model import verification failed: " + "; ".join(f"{name}: {message}" for name, message in failures.items()))
    return payload


def reinstall_torch(target: Path, dry_run: bool = False) -> int:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--upgrade",
        "--force-reinstall",
        "--no-deps",
        "--target",
        str(target),
        "--index-url",
        PYTORCH_CPU_INDEX,
        "torch",
    ]
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    if dry_run:
        return 0
    target.mkdir(parents=True, exist_ok=True)
    return int(subprocess.run(command, check=False).returncode)


def install_beatnet_plus(target: Path) -> None:
    model_root = target.parent / "models" / f"BeatNet-Plus-{BEATNET_PLUS_COMMIT[:12]}"
    weights = model_root / "src" / "BeatNetPlus" / "models" / "generic_weights.pt"
    if weights.is_file():
        print(f"BeatNet+ already installed: {model_root}")
        return
    model_root.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/mjhydri/BeatNet-Plus/archive/{BEATNET_PLUS_COMMIT}.zip"
    with tempfile.TemporaryDirectory(prefix="beatnet-plus-") as temp:
        archive = Path(temp) / "source.zip"
        request = urllib.request.Request(url, headers={"User-Agent": "beat-saber-mapping-bootstrap/2.0"})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as output:
            while chunk := response.read(1 << 20):
                output.write(chunk)
        with zipfile.ZipFile(archive) as source:
            source.extractall(temp)
        extracted = next(path for path in Path(temp).iterdir() if path.is_dir() and path.name.startswith("BeatNet-Plus-"))
        if model_root.exists():
            raise RuntimeError(f"incomplete BeatNet+ target already exists: {model_root}")
        extracted.replace(model_root)
    if not weights.is_file():
        raise RuntimeError("BeatNet+ archive did not contain generic_weights.pt")
    (model_root / "PINNED_COMMIT.txt").write_text(BEATNET_PLUS_COMMIT + "\n", encoding="ascii")
    print(f"Installed BeatNet+ {BEATNET_PLUS_COMMIT} at {model_root}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("core", "models", "all"), default="core")
    parser.add_argument("--target", type=Path, default=default_target())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-models", action="store_true", help="import Torch and optional trackers from --target")
    parser.add_argument(
        "--reinstall-torch",
        action="store_true",
        help="force a CPU Torch wheel that matches this interpreter even if a different ABI is cached",
    )
    args = parser.parse_args()
    mismatch = torch_abi_mismatch(args.target)
    if mismatch and args.tier in {"models", "all"}:
        print(f"WARNING: {mismatch}; a matching CPU Torch wheel will be installed after requirements")
        args.reinstall_torch = True
    skill_root = Path(__file__).resolve().parent.parent
    requirement_files = [skill_root / "requirements-core.txt"]
    if args.tier in {"models", "all"}:
        requirement_files.append(skill_root / "requirements-models.txt")
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--target", str(args.target)]
    for requirement in requirement_files:
        command += ["-r", str(requirement)]
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    if args.dry_run:
        if args.reinstall_torch:
            reinstall_torch(args.target, dry_run=True)
        return 0
    args.target.mkdir(parents=True, exist_ok=True)
    result = int(subprocess.run(command, check=False).returncode)
    if result:
        return result
    if args.tier in {"models", "all"}:
        install_beatnet_plus(args.target)
        weights = args.target.parent / "models" / f"BeatNet-Plus-{BEATNET_PLUS_COMMIT[:12]}" / "src" / "BeatNetPlus" / "models" / "generic_weights.pt"
        if not weights.is_file():
            print(f"ERROR: BeatNet+ weights missing at {weights}", file=sys.stderr)
            return 2
        print(f"BeatNet+ weights: {weights}")
    if args.reinstall_torch or torch_abi_mismatch(args.target):
        result = reinstall_torch(args.target)
        if result:
            return result
    if args.verify_models or args.reinstall_torch:
        remaining = torch_abi_mismatch(args.target)
        if remaining:
            print(f"ERROR: {remaining}", file=sys.stderr)
            return 2
        report = verify_model_imports(args.target)
        print(json.dumps({"modelImports": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
