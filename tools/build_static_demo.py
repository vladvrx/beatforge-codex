"""Build the rights-safe WebMCP demo used by GitHub Pages."""

from __future__ import annotations

import shutil
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web"
OUTPUT = ROOT / "site"


def build() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    html = (SOURCE / "index.html").read_text(encoding="utf-8")
    html = html.replace('<head>', '<head>\n  <script>window.BEATFORGE_STATIC = true;</script>')
    html = html.replace('src="/web/', 'src="')
    html = html.replace('href="/web/', 'href="')
    html = html.replace('"/assets/', '"assets/')
    html = html.replace("'/assets/", "'assets/")
    for file in SOURCE.iterdir():
        if file.suffix in {'.js', '.css'}:
            shutil.copy2(file, OUTPUT / file.name)
            revision = hashlib.sha256(file.read_bytes()).hexdigest()[:12]
            html = html.replace(f'"{file.name}"', f'"{file.name}?v={revision}"')
    (OUTPUT / "index.html").write_text(html, encoding="utf-8", newline="\n")
    assets = OUTPUT / "assets"
    shutil.copytree(SOURCE / "assets", assets, dirs_exist_ok=True)


if __name__ == "__main__":
    build()
