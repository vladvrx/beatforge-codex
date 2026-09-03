import json
import subprocess
import sys
from pathlib import Path

payload = json.loads(Path("data/downloads/batch_5_tracks.json").read_text(encoding="utf-8"))
tracks = payload.get("tracks", [])
out_dir = Path("data/downloads/lost_in_yesterday_radio_2")
out_dir.mkdir(parents=True, exist_ok=True)

print(f"Downloading {len(tracks)} tracks into {out_dir}...", flush=True)

for idx, title in enumerate(tracks, 1):
    print(f"\n[{idx}/{len(tracks)}] Downloading: {title} into {out_dir}...", flush=True)
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
        print(f"Failed {title}: {e}", flush=True)

print("\nAll batch 5 downloads finished successfully!", flush=True)
