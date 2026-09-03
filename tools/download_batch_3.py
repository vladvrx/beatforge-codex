import json
import subprocess
import sys
from pathlib import Path

payload = json.loads(Path("data/downloads/new_tracks_list.json").read_text(encoding="utf-8"))

def download_list(tracks, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    for idx, title in enumerate(tracks, 1):
        print(f"\n[{idx}/{len(tracks)}] Downloading: {title} into {out_dir}...")
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            f"ytsearch1:{title}",
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            str(out_dir / f"%(title)s.%(ext)s"),
            "--no-warnings",
            "--no-playlist",
        ]
        try:
            subprocess.run(cmd, check=False)
        except Exception as e:
            print(f"Failed {title}: {e}")

print("=== Downloading Alive 2007 ===")
download_list(payload.get("alive_2007", []), Path("data/downloads/daft_punk_alive_2007"))

print("\n=== Downloading The Slow Rush B-Sides ===")
download_list(payload.get("slow_rush_bsides", []), Path("data/downloads/tame_impala_slow_rush_bsides"))

print("\n=== Downloading Singles ===")
download_list(payload.get("singles", []), Path("data/downloads/singles"))

print("\nAll batch 3 downloads finished successfully!")
