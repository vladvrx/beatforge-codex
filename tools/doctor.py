"""Read-only dependency checks for the local Studio. Does not download models."""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def inspect() -> dict:
    checks = []
    for module in ("beatforge", "fastapi", "uvicorn", "numpy", "soundfile", "ortools.sat.python.cp_model", "UnityPy"):
        try:
            importlib.import_module(module)
            checks.append({"name": module, "ok": True, "required": True})
        except Exception as error:
            checks.append({"name": module, "ok": False, "required": True, "detail": str(error), "action": 'Run Start-BeatForge.ps1 -Setup or python -m pip install -e ".[dev,studio]".'})
    try:
        import imageio_ffmpeg
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-version"], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr[:300])
        checks.append({"name": "FFmpeg", "ok": True, "required": True, "detail": result.stdout.splitlines()[0]})
    except Exception as error:
        checks.append({"name": "FFmpeg", "ok": False, "required": True, "detail": str(error), "action": "Install imageio-ffmpeg into this environment."})
    for module in ("torch", "beat_this", "demucs"):
        available = importlib.util.find_spec(module) is not None
        checks.append({"name": module, "ok": available, "required": False, "detail": "Package found; model weights and runtime have not been tested." if available else "Optional package missing. RL requires Torch; uncertain analysis requests timing anchors."})
    try:
        from beatforge.premium import corpus_database
        available = corpus_database().is_file()
        checks.append({"name": "Official corpus", "ok": available, "required": False, "detail": "Database found; completeness is checked during generation." if available else "Full premium generation needs the local official corpus. The bundled sample works without it."})
    except Exception as error:
        checks.append({"name": "Official corpus", "ok": False, "required": False, "detail": str(error)})
    return {"python": sys.executable, "studioReady": all(row["ok"] for row in checks if row["required"]), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = inspect()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Python: {report['python']}")
        for item in report["checks"]:
            label = "OK" if item["ok"] else "MISSING" if item["required"] else "OPTIONAL"
            print(f"[{label}] {item['name']}: {item.get('detail', '')}")
            if item.get("action"):
                print(f"  {item['action']}")
    return 0 if report["studioReady"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
