import json
import urllib.request
import re
from pathlib import Path

urls = [
    "https://open.spotify.com/album/7u6zL7kqpgLPISZYXNTgYk",
    "https://open.spotify.com/album/0PUdc9WBtlyjG9Ba9DPmKa",
    "https://open.spotify.com/track/124NFj84ppZ5pAxTuVQYCQ",
    "https://open.spotify.com/track/3cjF2OFRmip8spwZYQRKxP",
    "https://open.spotify.com/track/6p0Ai6wRjz9CmjqkhoKOwf",
    "https://open.spotify.com/track/514joG57v4yKTsfQmz7stz",
    "https://open.spotify.com/track/7JW8FRWVCOQDs40IjEXdPi",
]

results = []

for url in urls:
    oembed_url = f"https://open.spotify.com/oembed?url={url}"
    try:
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            title = data.get("title", "")
            entity_type = "album" if "/album/" in url else "track"
            print(f"Fetched [{entity_type}]: {title} ({url})")
            results.append({"url": url, "type": entity_type, "title": title})
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")

out_path = Path("data/downloads/new_spotify_links.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
print(f"Saved metadata to {out_path}")
