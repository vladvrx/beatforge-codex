#!/usr/bin/env python3
"""Generate a deterministic five-difficulty vanilla v3 Beat Saber map."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

from beatforge_core import DIFFICULTIES, TOOL_VERSION, default_cache_dir, difficulty_rank, emit_progress, encode_ogg, find_ffmpeg, load_json, parse_difficulties, sha256_file, write_json
from choreography import CONFIGS, generate_all
from artwork import derive_palette, extract_embedded_artwork, lookup_itunes_cover, lookup_release_cover, palette_from_rgb, write_palette_cover
from official_corpus import corpus_is_fresh, detect_game_root, sync as sync_official_corpus

# Headset originals. Generate into a new versioned folder; never write these names.
PROTECTED_CUSTOM_LEVEL_FOLDERS = frozenset({"Pacific_Coast_Highway", "Pacific_Coast_Highway_8"})


def refuse_protected_output(out: Path) -> str | None:
    for part in Path(out).resolve().parts:
        if part in PROTECTED_CUSTOM_LEVEL_FOLDERS:
            return (
                f"refusing to write into protected CustomLevels folder {part}. "
                "Generate Pacific Coast Highway into a new versioned directory."
            )
    return None


def write_default_cover(path: Path, title: str, size: int = 512) -> None:
    seed = sum(ord(character) for character in title)
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            glow = max(0.0, 1.0 - ((x - size * 0.5) ** 2 + (y - size * 0.5) ** 2) ** 0.5 / (size * 0.75))
            stripe = 0.5 + 0.5 * ((x + y + seed) % 64) / 63.0
            rows.extend((int(12 + 42 * glow), int(18 + 70 * glow * stripe), int(34 + 145 * glow)))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def _copy_file(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    shutil.copy2(source, destination)


def prepare_audio(source: Path, destination: Path) -> None:
    if source.suffix.lower() in {".ogg", ".egg"}:
        _copy_file(source, destination)
    else:
        encode_ogg(source, destination)


def probe_audio_metadata(source: Path) -> dict[str, str]:
    executable = find_ffmpeg()
    if not executable:
        return {}
    result = subprocess.run(
        [executable, "-v", "error", "-i", str(source), "-map_metadata", "0:s:a:0", "-f", "ffmetadata", "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    metadata: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        metadata[key.casefold()] = value.replace(r"\=", "=").replace(r"\;", ";").replace(r"\#", "#").replace(r"\\", "\\")
    return metadata


def run_analysis(args: argparse.Namespace, analysis_dir: Path) -> int:
    script = Path(__file__).with_name("analyze_audio.py")
    command = [sys.executable, str(script), str(args.audio), "--out", str(analysis_dir), "--models", args.models, "--device", args.device]
    command += ["--stems", args.stems]
    if args.anchors:
        command += ["--anchors", str(args.anchors)]
    if args.bpm is not None:
        command += ["--bpm", str(args.bpm)]
    if args.offset_seconds is not None:
        command += ["--offset-seconds", str(args.offset_seconds)]
    if args.beatnet_weights:
        command += ["--beatnet-weights", str(args.beatnet_weights)]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(command, check=False, env=env)
    return int(result.returncode)


def ensure_corpus(database_path: Path, requested_game_root: Path | None) -> tuple[bool, dict[str, Any]]:
    game_root = requested_game_root.resolve() if requested_game_root else detect_game_root()
    if game_root and not corpus_is_fresh(database_path, game_root):
        result = sync_official_corpus(
            argparse.Namespace(game_root=game_root, database=database_path, max_bundles=0)
        )
        if result != 0:
            return False, {"reason": "official corpus sync reported extraction failures", "gameRoot": str(game_root)}
    if not database_path.is_file():
        return False, {
            "reason": "official corpus is missing and no installed Beat Saber game root was found",
            "hint": "run official_corpus.py sync --game-root PATH",
        }
    database = sqlite3.connect(database_path)
    status = dict(database.execute("SELECT status,COUNT(*) FROM bundles GROUP BY status").fetchall())
    expected_row = database.execute("SELECT value FROM corpus_meta WHERE key='candidate_bundle_count'").fetchone()
    expected = json.loads(expected_row[0]) if expected_row else None
    covered = int(status.get("indexed", 0)) + int(status.get("excluded", 0))
    failures = int(status.get("failed", 0))
    ready = failures == 0 and expected is not None and covered == int(expected)
    return ready, {"expectedBundles": expected, "coveredBundles": covered, "failedBundles": failures}


DAFT_PUNK_COLOR_SCHEME = {
    "colorScheme": {
        "colorSchemeId": "DaftPunkColorScheme",
        "saberAColor": {"r": 0.98, "g": 0.62, "b": 0.08, "a": 1.0},
        "saberBColor": {"r": 0.08, "g": 0.88, "b": 0.98, "a": 1.0},
        "environmentColor0": {"r": 0.98, "g": 0.62, "b": 0.08, "a": 1.0},
        "environmentColor1": {"r": 0.08, "g": 0.88, "b": 0.98, "a": 1.0},
        "obstaclesColor": {"r": 1.0, "g": 0.38, "b": 0.05, "a": 1.0},
        "environmentColor0Boost": {"r": 1.0, "g": 0.8, "b": 0.2, "a": 1.0},
        "environmentColor1Boost": {"r": 0.2, "g": 0.95, "b": 1.0, "a": 1.0},
        "environmentColorW": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
        "environmentColorWBoost": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
    },
    "useOverride": True,
}


def info_dat(
    title: str,
    artist: str,
    mapper: str,
    bpm: float,
    cover: str = "cover.png",
    color_scheme: dict[str, Any] | None = None,
    difficulties: tuple[str, ...] | None = None,
    environment: str = "DefaultEnvironment",
) -> dict[str, Any]:
    chosen = difficulties or DIFFICULTIES
    if environment == "DaftPunkEnvironment" or "daft punk" in artist.casefold():
        environment = "DaftPunkEnvironment"
        if not color_scheme:
            color_scheme = DAFT_PUNK_COLOR_SCHEME

    entries = []
    for difficulty in chosen:
        config = CONFIGS[difficulty]
        entries.append(
            {
                "_difficulty": difficulty,
                "_difficultyRank": difficulty_rank(difficulty),
                "_beatmapFilename": f"{difficulty}Standard.dat",
                "_noteJumpMovementSpeed": config.njs,
                "_noteJumpStartBeatOffset": config.spawn_offset,
                "_beatmapColorSchemeIdx": 0 if color_scheme else -1,
                "_environmentNameIdx": 0,
            }
        )
    return {
        "_version": "2.1.0",
        "_songName": title,
        "_songSubName": "",
        "_songAuthorName": artist,
        "_levelAuthorName": mapper,
        "_beatsPerMinute": round(bpm, 6),
        "_songTimeOffset": 0,
        "_shuffle": 0,
        "_shufflePeriod": 0.5,
        "_previewStartTime": 10,
        "_previewDuration": 10,
        "_songFilename": "song.ogg",
        "_coverImageFilename": cover,
        "_environmentName": environment,
        "_allDirectionsEnvironmentName": "GlassDesertEnvironment",
        "_environmentNames": [environment, "GlassDesertEnvironment"],
        "_colorSchemes": [color_scheme] if color_scheme else [],
        "_difficultyBeatmapSets": [
            {
                "_beatmapCharacteristicName": "Standard",
                "_difficultyBeatmaps": entries,
            }
        ],
    }


def _discover_palette(
    args: argparse.Namespace,
    *,
    analysis: dict[str, Any],
    title: str,
    artist: str,
    package_analysis_dir: Path,
) -> tuple[Path | None, dict[str, Any]]:
    artwork: dict[str, Any]
    if args.cover:
        if not args.cover.is_file():
            return None, {"status": "needs_palette", "reason": "selected cover file does not exist"}
        data = args.cover.read_bytes()
        artwork = {
            "status": "found",
            "source": "user-file",
            "path": str(args.cover.resolve()),
            "sha256": __import__("hashlib").sha256(data).hexdigest(),
        }
    else:
        artwork = extract_embedded_artwork(args.audio, package_analysis_dir / "cover_candidate")
        if artwork.get("status") != "found":
            artwork = lookup_release_cover(
                title=title,
                artist=artist,
                duration_seconds=float(analysis.get("durationSeconds", 0.0)),
                destination_stem=package_analysis_dir / "cover_candidate",
            )
        if artwork.get("status") not in {"found", "needs_approval"}:
            artwork = lookup_itunes_cover(
                title=title,
                artist=artist,
                duration_seconds=float(analysis.get("durationSeconds", 0.0)),
                destination_stem=package_analysis_dir / "cover_candidate",
            )
    cover_path = Path(str(artwork.get("path", ""))) if artwork.get("path") else None
    palette = derive_palette(cover_path) if cover_path and cover_path.is_file() else {
        "status": "needs_palette",
        "reason": "no high-confidence artwork was available; request a structured AI mood palette or user-selected colors",
    }
    return cover_path, {
        "status": "needs_approval" if palette.get("status") == "needs_approval" else "needs_palette",
        "artwork": artwork,
        "palette": palette,
        "approvalRequired": True,
    }


def _palette_for_unconfirmed_download(
    args: argparse.Namespace,
    *,
    analysis: dict[str, Any],
    title: str,
    artist: str,
    package_analysis_dir: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    cover_path, manifest = _discover_palette(
        args,
        analysis=analysis,
        title=title,
        artist=artist,
        package_analysis_dir=package_analysis_dir,
    )
    palette = manifest.get("palette") if isinstance(manifest.get("palette"), dict) else {}
    artwork = manifest.get("artwork") if isinstance(manifest.get("artwork"), dict) else {}
    found = Path(str(artwork.get("path") or (cover_path or "")))
    scheme = palette.get("colorScheme") if isinstance(palette, dict) else None
    if found.is_file() and isinstance(scheme, dict) and palette.get("status") == "needs_approval":
        payload = {
            **manifest,
            "status": "unconfirmed_download",
            "approvalRequired": False,
            "note": "Used without CIELAB approval because the user requested an unconfirmed download.",
        }
        return found, scheme, payload
    fallback = palette_from_rgb(
        (0.91, 0.16, 0.22),
        (0.16, 0.42, 0.96),
        source="unconfirmed-download",
        rationale="Fallback saber colors for an explicit unconfirmed-grid download.",
    )
    dest = package_analysis_dir / "unconfirmed_cover.png"
    if fallback.get("status") == "needs_approval":
        write_palette_cover(dest, fallback["left"], fallback["right"])
        payload = {
            "status": "unconfirmed_download",
            "artwork": {"status": "found", "source": "unconfirmed-download", "path": str(dest.resolve())},
            "palette": fallback,
            "approvalRequired": False,
        }
        return dest, fallback["colorScheme"], payload
    write_default_cover(dest, title)
    derived = derive_palette(dest)
    if derived.get("status") == "needs_approval" and isinstance(derived.get("colorScheme"), dict):
        payload = {
            "status": "unconfirmed_download",
            "artwork": {"status": "found", "source": "unconfirmed-default-cover", "path": str(dest.resolve())},
            "palette": derived,
            "approvalRequired": False,
        }
        return dest, derived["colorScheme"], payload
    raise ValueError("could not assemble colors for an unconfirmed download")


def _palette_for_studio_pack(
    args: argparse.Namespace,
    *,
    analysis: dict[str, Any],
    title: str,
    artist: str,
    package_analysis_dir: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    cover, scheme, payload = _palette_for_unconfirmed_download(
        args,
        analysis=analysis,
        title=title,
        artist=artist,
        package_analysis_dir=package_analysis_dir,
    )
    return cover, scheme, {
        **payload,
        "status": "studio_auto",
        "approvalRequired": False,
        "note": "Studio does not collect artwork approval; used available cover or a fallback pair.",
    }


def _load_approved_palette(path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    payload = load_json(path)
    if payload.get("status") != "approved":
        raise ValueError("palette file has not been explicitly approved")
    artwork = payload.get("artwork", {}) if isinstance(payload.get("artwork"), dict) else {}
    palette = payload.get("palette", payload)
    if not isinstance(palette, dict):
        raise ValueError("approved palette file is missing palette colors")
    palette = dict(palette)
    palette["status"] = "approved"
    payload = {**payload, "status": "approved", "artwork": artwork, "palette": palette, "approvalRequired": False}
    cover_path = Path(str(artwork.get("path", "")))
    if not cover_path.is_file():
        raise ValueError("approved artwork file is missing")
    color_scheme = palette.get("colorScheme")
    if not isinstance(color_scheme, dict) or color_scheme.get("useOverride") is not True:
        raise ValueError("approved palette has no valid Beat Saber colorScheme")
    required = {
        "colorSchemeId",
        "saberAColor",
        "saberBColor",
        "environmentColor0",
        "environmentColor1",
        "obstaclesColor",
        "environmentColor0Boost",
        "environmentColor1Boost",
    }
    scheme = color_scheme.get("colorScheme", {})
    if not isinstance(scheme, dict) or not required.issubset(scheme):
        raise ValueError("approved palette omits required Info.dat color fields")
    return cover_path, color_scheme, payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title")
    parser.add_argument("--artist")
    parser.add_argument("--mapper", default="Beatforge")
    parser.add_argument("--cover", type=Path)
    parser.add_argument("--palette", type=Path, help="explicitly approved palette manifest")
    parser.add_argument(
        "--continue-unconfirmed",
        action="store_true",
        help="Generate from the unconfirmed beat grid after an explicit download request. Does not mark timing_verified.",
    )
    parser.add_argument("--profile", default="official-premium", choices=["official-premium", "official-rl"])
    parser.add_argument("--engine", default="cp-sat", choices=["cp-sat", "rl", "hybrid"], help="Choreography engine to use (cp-sat, rl, hybrid)")
    parser.add_argument("--rl-model", type=Path, help="Path to trained PyTorch RL policy checkpoint")
    parser.add_argument("--environment", default=None, help="Beat Saber environment name (e.g. DaftPunkEnvironment, DefaultEnvironment)")
    parser.add_argument("--full-spread", action="store_true", default=True)
    parser.add_argument(
        "--difficulty",
        action="append",
        dest="difficulties",
        help="Standard difficulty to generate. Repeat to build a subset. Default is all five.",
    )
    parser.add_argument("--analysis-dir", type=Path)
    parser.add_argument("--anchors", type=Path)
    parser.add_argument("--bpm", type=float)
    parser.add_argument("--offset-seconds", type=float)
    parser.add_argument("--models", default="beat-this,beatnet-plus,all-in-one")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--stems", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--beatnet-weights", type=Path)
    parser.add_argument("--corpus-database", type=Path, default=default_cache_dir() / "official-corpus.sqlite3")
    parser.add_argument("--game-root", type=Path, help="Beat Saber install root; auto-detected on common Oculus/Steam paths")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    if not args.audio.is_file():
        print(f"ERROR: audio file does not exist: {args.audio}", file=sys.stderr)
        return 2
    protected = refuse_protected_output(args.out)
    if protected:
        print(f"ERROR: {protected}", file=sys.stderr)
        return 2
    emit_progress("corpus", "checking the official Beat Saber map corpus")
    corpus_ready, corpus_report = ensure_corpus(args.corpus_database, args.game_root)
    if not corpus_ready:
        print(json.dumps({"status": "corpus_incomplete", **corpus_report}, indent=2), file=sys.stderr)
        return 4
    emit_progress("metadata", f"reading tags from {args.audio.name}")
    embedded_metadata = probe_audio_metadata(args.audio)
    title = args.title or embedded_metadata.get("title") or args.audio.stem
    artist = args.artist or embedded_metadata.get("artist") or embedded_metadata.get("album_artist") or "Unknown Artist"
    emit_progress("track", f"{title} · {artist}")
    args.out.mkdir(parents=True, exist_ok=True)
    analysis_dir = args.analysis_dir or args.out / "_beatforge"
    analysis_file = analysis_dir / "analysis.json"
    sections_file = analysis_dir / "sections.json"
    if not analysis_file.exists() or not sections_file.exists():
        emit_progress("timing", "starting the sample-accurate beat ensemble")
        result = run_analysis(args, analysis_dir)
        if result not in (0, 3):
            return result
    else:
        emit_progress("timing", "reusing existing analysis.json")
    analysis = load_json(analysis_file)
    sections = load_json(sections_file)
    unconfirmed = bool(args.continue_unconfirmed)
    if analysis.get("status") != "timing_verified":
        if not unconfirmed:
            print(
                json.dumps(
                    {
                        "status": "needs_anchors",
                        "message": "Timing confidence did not meet the premium gate. Listen to the click track, correct the suggested beat/time pairs, and run again.",
                        "analysis": str(analysis_file.resolve()),
                        "suggestions": analysis.get("anchorSuggestions", []),
                        "clickTrackEvidence": analysis.get("clickTrackEvidence", {}),
                        "residuals": analysis.get("residuals", {}),
                        "durationSeconds": analysis.get("durationSeconds"),
                        "sampleRate": analysis.get("sampleRate"),
                        "clickTrack": "click_track.wav",
                    },
                    indent=2,
                )
            )
            return 3
        emit_progress("timing", "continuing from the unconfirmed beat grid for download")
    package_analysis_dir = args.out / "_beatforge"
    package_analysis_dir.mkdir(parents=True, exist_ok=True)
    documentation_source = Path(__file__).resolve().parents[1] / "references" / "documentation-manifest.json"
    if documentation_source.is_file():
        shutil.copy2(documentation_source, package_analysis_dir / "documentation_manifest.json")
    if analysis_dir.resolve() != package_analysis_dir.resolve():
        for name in ("analysis.json", "beat_grid.json", "sections.json", "audio_features.npz", "click_track.wav"):
            source = analysis_dir / name
            if source.is_file():
                shutil.copy2(source, package_analysis_dir / name)
    if args.palette:
        try:
            cover_source, color_scheme, palette_manifest = _load_approved_palette(args.palette)
        except (OSError, ValueError) as error:
            print(json.dumps({"status": "needs_palette", "message": str(error)}, indent=2))
            return 5
    elif unconfirmed:
        emit_progress("palette", "using cover colors or a fallback pair for the unconfirmed download")
        try:
            cover_source, color_scheme, palette_manifest = _palette_for_unconfirmed_download(
                args,
                analysis=analysis,
                title=title,
                artist=artist,
                package_analysis_dir=package_analysis_dir,
            )
        except (OSError, ValueError) as error:
            print(json.dumps({"status": "needs_palette", "message": str(error)}, indent=2))
            return 5
    else:
        emit_progress("palette", "using embedded, release, or fallback cover colors")
        try:
            cover_source, color_scheme, palette_manifest = _palette_for_studio_pack(
                args,
                analysis=analysis,
                title=title,
                artist=artist,
                package_analysis_dir=package_analysis_dir,
            )
        except (OSError, ValueError) as error:
            print(json.dumps({"status": "needs_palette", "message": str(error)}, indent=2))
            return 5
    source_hash = str(analysis.get("sourceSha256") or sha256_file(args.audio))
    seed = args.seed if args.seed is not None else int(source_hash[:16], 16)
    try:
        chosen = parse_difficulties(args.difficulties)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    emit_progress("choreography", f"solving {', '.join(chosen)}")
    if args.engine == "rl" or args.profile == "official-rl":
        from rl.policy_generator import RLMapGenerator

        rl_gen = RLMapGenerator(model_path=args.rl_model)
        raw_bg = load_json(analysis_dir / "beat_grid.json") if (analysis_dir / "beat_grid.json").exists() else None
        if isinstance(raw_bg, dict) and "tempoRegions" in raw_bg:
            max_beat = max((float(r.get("endBeat", 0.0)) for r in raw_bg.get("tempoRegions", [])), default=0.0)
            beat_grid = [round(i * 0.25, 4) for i in range(max(16, int(max_beat * 4)))]
        elif isinstance(raw_bg, list):
            beat_grid = [float(b) for b in raw_bg]
        else:
            duration_s = float(analysis.get("durationSeconds", analysis.get("duration", 240.0)))
            bpm_val = float(analysis.get("bpm", 125.0))
            beat_grid = [round(i * 0.25, 4) for i in range(max(16, int(duration_s * bpm_val / 60.0 * 4)))]

        grid_len = len(beat_grid)
        try:
            import numpy as np
            from beatforge_core import frame_features, load_audio
            audio_buf = load_audio(args.audio)
            feat = frame_features(audio_buf.samples)
            flux = feat["flux"]
            onsets = np.interp(np.linspace(0, len(flux), grid_len), np.arange(len(flux)), flux)
            onsets = ((onsets - np.min(onsets)) / (np.max(onsets) - np.min(onsets) + 1e-6)).tolist()
        except Exception:
            onsets = [0.5] * grid_len

        audio_features = {
            "onsets": onsets,
            "flux": onsets,
            "stems": {
                "drums": onsets,
                "bass": onsets,
                "vocals": [o * 0.7 for o in onsets],
                "guitar": [o * 0.5 for o in onsets],
                "piano": [o * 0.5 for o in onsets],
                "other": [o * 0.4 for o in onsets],
            },
            "sections": [s.get("type", "verse") for s in sections] if isinstance(sections, list) else ["verse"] * grid_len,
        }
        maps = {}
        for diff in chosen:
            maps[diff] = rl_gen.generate_difficulty(audio_features, beat_grid, float(analysis["bpm"]), difficulty=diff)
        choreography_report = {
            "poseSolver": "rl-policy-v1",
            "solver": "rl-policy-v1",
            "difficulties": chosen,
        }
    else:
        maps, choreography_report = generate_all(analysis, sections, seed, args.corpus_database, difficulties=chosen)
    for difficulty, payload in maps.items():
        write_json(args.out / f"{difficulty}Standard.dat", payload)
    emit_progress("audio", "encoding song.ogg for the Beat Saber pack")
    prepare_audio(args.audio, args.out / "song.ogg")
    extension = cover_source.suffix.lower()
    cover_name = "cover.png" if extension == ".png" else "cover.jpg"
    _copy_file(cover_source, args.out / cover_name)
    write_json(
        args.out / "Info.dat",
        info_dat(
            title,
            artist,
            args.mapper,
            float(analysis["bpm"]),
            cover_name,
            color_scheme,
            chosen,
            environment=args.environment or ("DaftPunkEnvironment" if "daft punk" in artist.casefold() else "DefaultEnvironment"),
        ),
    )
    provenance = {
        "tool": "beat-saber-mapping",
        "toolVersion": TOOL_VERSION,
        "profile": args.profile,
        "seed": seed,
        "status": "generated_unvalidated",
        "sourceAudio": {"filename": args.audio.name, "sha256": source_hash},
        "metadata": {"title": title, "artist": artist, "mapper": args.mapper, "embeddedTags": embedded_metadata},
        "artworkAndPalette": palette_manifest,
        "timing": {
            "status": analysis["status"],
            "unconfirmedDownload": unconfirmed and analysis.get("status") != "timing_verified",
            "gridSource": analysis["gridSource"],
            "bpm": analysis["bpm"],
            "consensus": analysis.get("consensus", {}),
            "externalAgreement": analysis.get("externalAgreement", {}),
            "demucs": analysis.get("demucs")
            or {
                "package": "demucs==4.0.1",
                "requiredModel": "htdemucs_6s",
                "model": None,
                "status": "not-run",
                "note": "Use htdemucs_6s (six stems). Do not use Demucs CLI --two-stems karaoke mode.",
            },
        },
        "choreography": choreography_report,
        "officialCorpus": {"database": str(args.corpus_database.resolve()), **corpus_report},
        "documentation": {
            "manifest": "_beatforge/documentation_manifest.json",
            "sha256": sha256_file(package_analysis_dir / "documentation_manifest.json")
            if (package_analysis_dir / "documentation_manifest.json").is_file()
            else None,
        },
        "releaseGate": {
            "structuralInspection": False,
            "editorChecker": False,
            "slowVrPlaytest": False,
            "fullSpeedVrPlaytest": False,
            "freshSightRead": False,
        },
    }
    write_json(package_analysis_dir / "provenance.json", provenance)
    emit_progress("validating", "running schema, flow, occupancy, and packaging gates")
    validator = Path(__file__).with_name("validate_map.py")
    qa_file = package_analysis_dir / "qa_report.json"
    result = subprocess.run([sys.executable, str(validator), str(args.out), "--json", str(qa_file)], check=False)
    qa = load_json(qa_file) if qa_file.exists() else {"status": "invalid"}
    provenance["status"] = "playtest_candidate" if result.returncode == 0 else "invalid"
    provenance["releaseGate"]["structuralInspection"] = result.returncode == 0
    write_json(package_analysis_dir / "provenance.json", provenance)
    print(json.dumps({"status": provenance["status"], "output": str(args.out.resolve()), "seed": seed, "qa": qa.get("status")}, indent=2))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
