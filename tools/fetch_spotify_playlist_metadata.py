import json
import urllib.request
from pathlib import Path

track_ids = [
    '0t2QiRkpag0fAgs9zuCPlH',
    '1jJci4qxiYcOHhQR247rEU',
    '3zYpRGnnoegSpt3SguSo3W',
    '6RZmhpvukfyeSURhf4kZ0d',
    '1xqT27jSG1Y15vOXfsV0gv',
    '098ttCNmncrO4YvqWUNMvn',
    '71DSaeKtXQaMAqnvMT7Uoc',
    '78H72MElkOY9cRnaudxZFY',
    '5vTPFJOzgr6IzF11Kvjzls',
    '50GxvQA2KEWNt31EdwIlzY',
    '0DiWol3AO6WpXZgp0goxAV',
    '4iz9lGMjU1lXS51oPmUmTe',
    '303ccTay2FiDTZ9fZ2AdBt',
    '7v9Q0dAb9t7h8gJOkcJHay',
    '1GfEpihzfbV6HFt21JA1dz',
    '5DZ4M3yMat79ok25rZHuA9',
    '6KzkqZqhUBEsWYJJa2aBOd',
    '6qZjm61s6u8Ead9sWxCDro',
    '3McBKxKZLXbE4czUezk5QG',
    '3LbZIhU0smEU5SUnxod4j4',
]

results = []
for tid in track_ids:
    url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{tid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            title = data.get("title", "")
            results.append({"id": tid, "title": title})
            print(f"Fetched: {title}")
    except Exception as e:
        print(f"Failed {tid}: {e}")

out_path = Path("data/downloads/spotify_july/playlist_tracks.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
print(f"Saved {len(results)} tracks to {out_path}")
