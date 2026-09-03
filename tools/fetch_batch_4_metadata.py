import json
import urllib.request
import re
from pathlib import Path

urls = [
    "https://open.spotify.com/album/2lIZef4lzdvZkiiCzvPKj7",
    "https://open.spotify.com/playlist/37i9dQZF1E8CZ3Y73ZXBNX",
]

def fetch_details(url):
    oembed_url = f"https://open.spotify.com/oembed?url={url}"
    title = "Unknown"
    try:
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            title = data.get("title", "")
    except Exception as e:
        print(f"oEmbed error on {url}: {e}")
    
    # Also fetch webpage to extract track IDs
    track_names = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            track_ids = re.findall(r'name="music:song" content="https://open\.spotify\.com/track/([^"]+)"', html)
            print(f"Found {len(track_ids)} track IDs in {title}")
            for tid in track_ids:
                t_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{tid}"
                try:
                    r2 = urllib.request.Request(t_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(r2, timeout=10) as resp2:
                        d2 = json.loads(resp2.read().decode())
                        t_name = d2.get("title", "")
                        track_names.append(t_name)
                        print(f" - {t_name}")
                except Exception as e:
                    print(f"Track {tid} error: {e}")
    except Exception as e:
        print(f"Page fetch error: {e}")
    
    return {"title": title, "url": url, "tracks": track_names}

print("=== Fetching URL 1 ===")
res1 = fetch_details(urls[0])

print("\n=== Fetching URL 2 ===")
res2 = fetch_details(urls[1])

out_file = Path("data/downloads/batch_4_tracks.json")
out_file.write_text(json.dumps([res1, res2], indent=2), encoding="utf-8")
print(f"\nSaved metadata to {out_file}")
