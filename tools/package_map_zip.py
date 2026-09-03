import zipfile
import shutil
from pathlib import Path
import sys

map_dir = Path("data/maps/MusicSoundsBetterWithYou")
zip_path = Path("data/maps/Stardust - Music Sounds Better With You.zip")

if not map_dir.exists():
    print(f"Map directory {map_dir} does not exist yet.")
    sys.exit(1)

with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
    for file in map_dir.rglob("*"):
        if file.is_file():
            arcname = file.relative_to(map_dir)
            zipf.write(file, arcname)
            print(f"Added {arcname} ({file.stat().st_size} bytes)")

print(f"\nSuccessfully created level zip at: {zip_path.resolve()} ({zip_path.stat().st_size} bytes)")
