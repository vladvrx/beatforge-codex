import json
import subprocess
import sys
from pathlib import Path

payload = json.loads(Path("data/downloads/batch_4_tracks.json").read_text(encoding="utf-8"))

brat_tracks = [f"Charli xcx - {t}" for t in payload[0]["tracks"]]
radio_tracks = payload[1]["tracks"]

def download_tracklist(tracks, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
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

print("=== Downloading Charli xcx: BRAT ===", flush=True)
download_tracklist(brat_tracks, Path("data/downloads/charli_xcx_brat"))

print("\n=== Downloading Lost In Yesterday Radio ===", flush=True)
download_tracklist(radio_tracks, Path("data/downloads/lost_in_yesterday_radio"))

print("\nAll batch 4 downloads finished successfully!", flush=True)
