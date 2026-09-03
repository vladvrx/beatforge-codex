import json
import subprocess
import sys
from pathlib import Path

playlist_file = Path("data/downloads/spotify_july/playlist_tracks.json")
if not playlist_file.is_file():
    print("playlist_tracks.json not found")
    sys.exit(1)

tracks = json.loads(playlist_file.read_text(encoding="utf-8"))
out_dir = Path("data/downloads/spotify_july")

print(f"Downloading {len(tracks)} tracks into {out_dir}...")

for idx, t in enumerate(tracks, 1):
    title = t["title"]
    print(f"\n[{idx}/{len(tracks)}] Searching & downloading: {title}")
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
        print(f"Failed to download {title}: {e}")

print("\nDownload batch finished!")
