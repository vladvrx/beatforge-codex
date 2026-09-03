"""Studio CLI. Default generation is the premium skill pipeline.

The website never uses this module. `--legacy` keeps the pre-premium
librosa/place generator for its existing unit tests only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from beatforge.premium import package_map, run_premium_pipeline
from beatforge.styles import DIFFICULTIES


def parse_diffs(values: list[str] | None) -> list[str]:
    if not values:
        return ["Expert"]
    out: list[str] = []
    for raw in values:
        for part in raw.split(","):
            name = part.strip()
            if not name:
                continue
            if name not in DIFFICULTIES:
                known = ", ".join(DIFFICULTIES)
                raise SystemExit(f"Unknown difficulty {name!r}. Choose from: {known}")
            if name not in out:
                out.append(name)
    return out or ["Expert"]


def _legacy_main(args: argparse.Namespace) -> int:
    from beatforge.critic import run_critic
    from beatforge.export_beatsaber import export_zip
    from beatforge.place import generate_chart

    diffs = parse_diffs(args.diffs)
    audio = args.audio.expanduser().resolve()
    print(f"LEGACY mapper: analyzing {audio.name} …", file=sys.stderr)
    chart = generate_chart(
        audio,
        title=args.title,
        artist=args.artist,
        difficulties=diffs,
        style=args.style,
        seed=args.seed,
    )
    out_dir = args.out.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in chart.title)[:80]
    zip_path = out_dir / f"{slug}_{chart.style}.zip"
    export_zip(chart, audio, zip_path)
    print(f"Wrote {zip_path}")
    print(f"BPM: {chart.bpm:.2f}  duration: {chart.duration:.1f}s  style: {chart.style}")
    for name, diff in chart.difficulties.items():
        print(
            f"  {name}: {len(diff.notes)} notes, {len(diff.arcs)} arcs, "
            f"{len(diff.obstacles)} walls"
        )
    if args.no_critic:
        return 0
    report = run_critic(chart)
    critic_path = out_dir / f"{slug}_critic.json"
    critic_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Critic verdict: {report['verdict']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="beatforge",
        description="Generate a Beat Saber custom map from a song file via the official-premium skill pipeline.",
    )
    p.add_argument("audio", type=Path, help="Path to mp3/wav/ogg/flac")
    p.add_argument(
        "--diff",
        action="append",
        dest="diffs",
        help="Legacy mapper only. Premium always writes Easy–Expert+.",
    )
    p.add_argument("--out", type=Path, default=Path("out"), help="Output directory")
    p.add_argument("--title", default=None, help="Song title (default: filename)")
    p.add_argument("--artist", default="Unknown")
    p.add_argument("--mapper", default="BeatForge")
    p.add_argument(
        "--style",
        default="auto",
        help="Legacy mapper only (flow | tech | speed | chill | auto)",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--anchors", type=Path, default=None)
    p.add_argument("--palette", type=Path, default=None)
    p.add_argument(
        "--no-critic",
        action="store_true",
        help="Legacy mapper only: skip the post-generation critic",
    )
    p.add_argument(
        "--legacy",
        action="store_true",
        help="Use the quarantined librosa/place generator. Not the studio path.",
    )
    args = p.parse_args(argv)

    audio = args.audio.expanduser().resolve()
    if not audio.is_file():
        print(f"Audio not found: {audio}", file=sys.stderr)
        return 1
    if args.legacy:
        return _legacy_main(args)

    title = args.title or audio.stem
    out_dir = args.out.expanduser().resolve()
    map_folder = out_dir / "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:80]
    map_folder.mkdir(parents=True, exist_ok=True)
    print(f"Premium pipeline: {audio.name} → {map_folder}")
    result = run_premium_pipeline(
        audio=audio,
        output=map_folder,
        title=title,
        artist=args.artist,
        mapper=args.mapper,
        seed=args.seed,
        anchors=args.anchors.expanduser().resolve() if args.anchors else None,
        palette=args.palette.expanduser().resolve() if args.palette else None,
        progress=lambda stage, detail: print(f"  [{stage}] {detail}"),
    )
    print(f"Studio status: {result['status']} (exit {result['returnCode']})")
    payload = result.get("payload") or {}
    if payload:
        print(json.dumps(payload, indent=2)[:4000])
    if result["status"] == "playtest_candidate" and (map_folder / "Info.dat").is_file():
        zip_path = out_dir / f"{map_folder.name}.zip"
        package_map(map_folder, zip_path)
        print(f"Wrote {zip_path}")
    if result["stderr"]:
        print(result["stderr"][-2000:], file=sys.stderr)
    return int(result["returnCode"])


if __name__ == "__main__":
    raise SystemExit(main())
