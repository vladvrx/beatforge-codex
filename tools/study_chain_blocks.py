import sqlite3
import json
from collections import Counter, defaultdict
from pathlib import Path

db_path = Path(r"C:\Users\user\AppData\Local\Codex\beat-saber-mapping\official-corpus.sqlite3")
conn = sqlite3.connect(str(db_path))
query = """
SELECT 
    b.pack_id,
    l.title,
    l.artist,
    l.bpm,
    d.difficulty,
    d.characteristic,
    e.beat,
    e.tail_beat,
    e.color,
    e.x,
    e.y,
    e.direction,
    e.tail_x,
    e.tail_y,
    e.tail_direction,
    e.payload_json
FROM events e
JOIN difficulties d ON e.difficulty_id = d.id
JOIN levels l ON d.level_id = l.id
JOIN bundles b ON l.bundle_id = b.id
WHERE e.kind IN ('burst_slider', 'chain', 'burstSlider', 'burst_sliders')
ORDER BY b.pack_id, l.title, e.beat;
"""

rows = conn.execute(query).fetchall()

print(f"Total Official Burst Sliders Analyzed: {len(rows)}")

pack_chains = Counter()
diff_chains = Counter()
slice_counts = Counter()
squish_counts = Counter()
directions = Counter()
delta_vectors = Counter()
durations = Counter()
speed_per_sec = []

# Detailed pack breakdown
edm_chains = []
daft_chains = []
ost_chains = []

dir_names = {0: "Up", 1: "Down", 2: "Left", 3: "Right", 4: "Up-Left", 5: "Up-Right", 6: "Down-Left", 7: "Down-Right", 8: "Dot"}

for r in rows:
    pack_id, title, artist, bpm, difficulty, characteristic, beat, tail_beat, color, x, y, direction, tail_x, tail_y, tail_dir, payload_json = r
    payload = json.loads(payload_json) if payload_json else {}
    
    pack_chains[pack_id] += 1
    diff_chains[difficulty] += 1
    
    sc = payload.get("sc", payload.get("sliceCount", 0))
    sq = round(float(payload.get("s", payload.get("squishAmount", 1.0))), 2)
    dt = round(float(tail_beat) - float(beat), 4) if tail_beat is not None else 0.0
    dx = (tail_x - x) if tail_x is not None else 0
    dy = (tail_y - y) if tail_y is not None else 0
    
    slice_counts[sc] += 1
    squish_counts[sq] += 1
    directions[direction] += 1
    delta_vectors[(dx, dy)] += 1
    durations[dt] += 1
    
    entry = {
        "pack": pack_id,
        "song": f"{artist} - {title}",
        "bpm": bpm,
        "diff": difficulty,
        "beat": beat,
        "dt": dt,
        "head": (x, y),
        "tail": (tail_x, tail_y),
        "dx_dy": (dx, dy),
        "dir": dir_names.get(direction, str(direction)),
        "color": color,
        "slices": sc,
        "squish": sq
    }
    
    if pack_id == "EDM":
        edm_chains.append(entry)
    elif pack_id == "DaftPunk":
        daft_chains.append(entry)
    elif "Ost" in str(pack_id):
        ost_chains.append(entry)

print("\n=== 1. DIFFICULTY DISTRIBUTION ===")
for diff, c in diff_chains.most_common():
    print(f"  * {diff:12s}: {c:5d} ({c/len(rows)*100:.1f}%)")

print("\n=== 2. SLICE COUNT DISTRIBUTION ===")
for sc, c in slice_counts.most_common(10):
    print(f"  * {sc} links: {c:5d} ({c/len(rows)*100:.1f}%)")

print("\n=== 3. SQUISH AMOUNT DISTRIBUTION ===")
for sq, c in squish_counts.most_common(10):
    print(f"  * squish {sq}: {c:5d} ({c/len(rows)*100:.1f}%)")

print("\n=== 4. DURATION IN BEATS ===")
for dt, c in durations.most_common(10):
    print(f"  * {dt} beats: {c:5d} ({c/len(rows)*100:.1f}%)")

print("\n=== 5. DELTA VECTORS (dx, dy) ===")
for (dx, dy), c in delta_vectors.most_common(15):
    print(f"  * dx={dx:+2d}, dy={dy:+2d}: {c:5d} ({c/len(rows)*100:.1f}%)")

print("\n=== 6. ELECTRONIC MIXTAPE (EDM) CHAIN BLOCK SIGNATURES ===")
print(f"Total EDM Chains: {len(edm_chains)}")
for ex in edm_chains[:15]:
    print(f"  [{ex['song']} | {ex['diff']} | BPM {ex['bpm']}] beat {ex['beat']:.2f} (len {ex['dt']}b): head={ex['head']} -> tail={ex['tail']} (dx={ex['dx_dy'][0]}, dy={ex['dx_dy'][1]}), dir={ex['dir']}, slices={ex['slices']}, squish={ex['squish']}")

print("\n=== 7. DAFT PUNK CHAIN BLOCK SIGNATURES ===")
print(f"Total Daft Punk Chains: {len(daft_chains)}")
for ex in daft_chains[:15]:
    print(f"  [{ex['song']} | {ex['diff']} | BPM {ex['bpm']}] beat {ex['beat']:.2f} (len {ex['dt']}b): head={ex['head']} -> tail={ex['tail']} (dx={ex['dx_dy'][0]}, dy={ex['dx_dy'][1]}), dir={ex['dir']}, slices={ex['slices']}, squish={ex['squish']}")

print("\n=== 8. OST 5 / OST 6 / OST 7 CHAIN BLOCK SIGNATURES ===")
print(f"Total OST Chains: {len(ost_chains)}")
for ex in ost_chains[:15]:
    print(f"  [{ex['song']} | {ex['diff']} | BPM {ex['bpm']}] beat {ex['beat']:.2f} (len {ex['dt']}b): head={ex['head']} -> tail={ex['tail']} (dx={ex['dx_dy'][0]}, dy={ex['dx_dy'][1]}), dir={ex['dir']}, slices={ex['slices']}, squish={ex['squish']}")
