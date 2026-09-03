"""Build the rights-safe WebMCP demo used by GitHub Pages."""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web"
OUTPUT = ROOT / "site"


def build() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    html = (SOURCE / "index.html").read_text(encoding="utf-8")
    html = html.replace('src="/web/webmcp.js"', 'src="webmcp.js"')
    html = html.replace('"/assets/', '"assets/')
    html = html.replace("'/assets/", "'assets/")
    (OUTPUT / "index.html").write_text(html, encoding="utf-8")
    shutil.copy2(SOURCE / "webmcp.js", OUTPUT / "webmcp.js")
    assets = OUTPUT / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    shutil.copytree(SOURCE / "assets", assets)


if __name__ == "__main__":
    build()
