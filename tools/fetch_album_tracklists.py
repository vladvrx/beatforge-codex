import urllib.request
import re
import json
from pathlib import Path

def get_page_tracks(url, album_name, artist):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            track_ids = re.findall(r'name="music:song" content="https://open\.spotify\.com/track/([^"]+)"', html)
            tracks = []
            for tid in track_ids:
                oembed = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{tid}"
                try:
                    r2 = urllib.request.Request(oembed, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(r2, timeout=10) as resp2:
                        d2 = json.loads(resp2.read().decode())
                        t_title = d2.get("title", "")
                        tracks.append(f"{artist} - {t_title}")
                        print(f"[{album_name}] {artist} - {t_title}")
                except Exception as e:
                    print(f"Failed {tid}: {e}")
            return tracks
    except Exception as e:
        print(f"Error loading {url}: {e}")
        return []

print("--- Fetching Alive 2007 ---")
alive_tracks = get_page_tracks("https://open.spotify.com/album/7u6zL7kqpgLPISZYXNTgYk", "Alive 2007", "Daft Punk")

print("\n--- Fetching The Slow Rush B-Sides ---")
slowrush_tracks = get_page_tracks("https://open.spotify.com/album/0PUdc9WBtlyjG9Ba9DPmKa", "The Slow Rush B-Sides", "Tame Impala")

individual_tracks = [
    "Drake ft. Rihanna - Take Care",
    "Drake - Feel No Ways",
    "Darius - Road Trips",
    "Ninajirachi - Janice STFU",
    "Ninajirachi - WannaCry",
]

all_payload = {
    "alive_2007": alive_tracks,
    "slow_rush_bsides": slowrush_tracks,
    "singles": individual_tracks,
}

out_file = Path("data/downloads/new_tracks_list.json")
out_file.write_text(json.dumps(all_payload, indent=2), encoding="utf-8")
print(f"\nSaved all tracklists to {out_file}")
