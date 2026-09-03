#!/usr/bin/env python3
"""Cover-art discovery and accessible Beat Saber palette derivation."""

from __future__ import annotations

import base64
import colorsys
import hashlib
import io
import math
import re
import subprocess
import struct
import zlib
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from beatforge_core import find_ffmpeg


USER_AGENT = "BeatForge/0.2 (local Beat Saber mapping tool)"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extension(mime: str, data: bytes) -> str:
    if "png" in mime.casefold() or data.startswith(b"\x89PNG"):
        return ".png"
    return ".jpg"


def extract_embedded_artwork(audio_path: Path, output_stem: Path) -> dict[str, Any]:
    """Extract MP3, FLAC, MP4/M4A, OGG, or WAV embedded front art."""

    try:
        import mutagen
        from mutagen.flac import Picture
    except ImportError:
        executable = find_ffmpeg()
        if not executable:
            return {"status": "unavailable", "reason": "mutagen and FFmpeg are unavailable"}
        destination = output_stem.with_suffix(".jpg")
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio_path), "-map", "0:v:0", "-frames:v", "1", str(destination)],
            check=False,
            capture_output=True,
        )
        if result.returncode or not destination.is_file():
            return {"status": "missing", "reason": "no embedded cover art"}
        data = destination.read_bytes()
        return {"status": "found", "source": "embedded", "container": "ffmpeg-attached-picture", "frontCover": True, "mime": "image/jpeg", "sha256": _sha256(data), "path": str(destination)}

    media = mutagen.File(audio_path)
    if media is None:
        return {"status": "missing", "reason": "unsupported or unreadable audio metadata"}
    pictures: list[tuple[int, str, bytes, str]] = []
    for picture in getattr(media, "pictures", []) or []:
        pictures.append((int(getattr(picture, "type", 0) == 3), str(getattr(picture, "mime", "")), bytes(picture.data), "flac-picture"))
    tags = getattr(media, "tags", None)
    if tags:
        values = list(tags.values()) if hasattr(tags, "values") else []
        for value in values:
            if hasattr(value, "data") and value.__class__.__name__.startswith("APIC"):
                pictures.append((int(getattr(value, "type", 0) == 3), str(getattr(value, "mime", "")), bytes(value.data), "id3-apic"))
        for value in tags.get("covr", []) if hasattr(tags, "get") else []:
            raw = bytes(value)
            mime = "image/png" if raw.startswith(b"\x89PNG") else "image/jpeg"
            pictures.append((1, mime, raw, "mp4-covr"))
        for key in ("metadata_block_picture", "METADATA_BLOCK_PICTURE"):
            for encoded in tags.get(key, []) if hasattr(tags, "get") else []:
                try:
                    picture = Picture(base64.b64decode(str(encoded)))
                    pictures.append((int(picture.type == 3), picture.mime, bytes(picture.data), "ogg-picture"))
                except (ValueError, TypeError):
                    continue
        for key in ("coverart", "COVERART"):
            for encoded in tags.get(key, []) if hasattr(tags, "get") else []:
                try:
                    raw = base64.b64decode(str(encoded))
                    pictures.append((0, "image/jpeg", raw, "ogg-coverart"))
                except (ValueError, TypeError):
                    continue
    pictures = [item for item in pictures if item[2]]
    if not pictures:
        return {"status": "missing", "reason": "no embedded cover art"}
    pictures.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
    preferred, mime, data, source = pictures[0]
    destination = output_stem.with_suffix(_extension(mime, data))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {
        "status": "found",
        "source": "embedded",
        "container": source,
        "frontCover": bool(preferred),
        "mime": mime or "application/octet-stream",
        "sha256": _sha256(data),
        "path": str(destination),
    }


def normalize_credit(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"\([^)]*(official|audio|video|lyrics?)[^)]*\)", " ", value)
    value = re.sub(r"\b(feat|ft)\.?\b", " featuring ", value)
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


def metadata_match_confidence(
    title: str, artist: str, candidate_title: str, candidate_artist: str
) -> float:
    title_score = SequenceMatcher(None, normalize_credit(title), normalize_credit(candidate_title)).ratio()
    artist_norm = normalize_credit(artist)
    if not artist_norm or artist_norm in {"unknown", "unknown artist"}:
        return title_score
    artist_score = SequenceMatcher(None, artist_norm, normalize_credit(candidate_artist)).ratio()
    return 0.68 * title_score + 0.32 * artist_score


def pick_cover_art_image(images: list[dict[str, Any]]) -> dict[str, Any] | None:
    fronts = [image for image in images if image.get("front") is True]
    if not fronts:
        return None
    approved = [image for image in fronts if image.get("approved") is True]
    return (approved or fronts)[0]


def lookup_itunes_cover(
    *,
    title: str,
    artist: str,
    duration_seconds: float,
    destination_stem: Path,
) -> dict[str, Any]:
    """Find iTunes artwork when MusicBrainz has no usable Official CAA front."""

    try:
        import httpx
    except ImportError:
        return {"status": "unavailable", "reason": "httpx is not installed"}
    artist_norm = normalize_credit(artist)
    term = title if not artist_norm or artist_norm in {"unknown", "unknown artist"} else f"{title} {artist}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
            response = client.get(
                "https://itunes.apple.com/search",
                params={"term": term, "entity": "song", "limit": 12},
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            ranked: list[tuple[float, dict[str, Any]]] = []
            for item in results:
                confidence = metadata_match_confidence(
                    title,
                    artist,
                    str(item.get("trackName", "")),
                    str(item.get("artistName", "")),
                )
                track_ms = item.get("trackTimeMillis")
                if confidence < 0.82 or not isinstance(track_ms, (int, float)):
                    continue
                if abs(float(track_ms) / 1000.0 - duration_seconds) > 6.0:
                    continue
                ranked.append((confidence, item))
            ranked.sort(key=lambda item: item[0], reverse=True)
            for confidence, item in ranked:
                thumb = str(item.get("artworkUrl100") or "")
                if not thumb:
                    continue
                image_url = re.sub(r"100x100bb", "600x600bb", thumb)
                image_response = client.get(image_url)
                image_response.raise_for_status()
                data = image_response.content
                if len(data) < 2000:
                    continue
                destination = destination_stem.with_suffix(
                    _extension(image_response.headers.get("content-type", ""), data)
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                return {
                    "status": "needs_approval",
                    "source": "itunes",
                    "approved": True,
                    "front": True,
                    "matchConfidence": round(confidence, 6),
                    "durationDeltaSeconds": round(abs(float(item["trackTimeMillis"]) / 1000.0 - duration_seconds), 6),
                    "releaseTitle": item.get("trackName"),
                    "sourceUrl": image_url,
                    "sha256": _sha256(data),
                    "path": str(destination),
                }
    except (OSError, ValueError, httpx.HTTPError) as error:
        return {"status": "unavailable", "reason": f"itunes lookup failed: {type(error).__name__}"}
    return {"status": "missing", "reason": "no iTunes song artwork met the thresholds"}


def lookup_release_cover(
    *,
    title: str,
    artist: str,
    duration_seconds: float,
    destination_stem: Path,
) -> dict[str, Any]:
    """Find a high-confidence Official MusicBrainz release and CAA front image."""

    try:
        import httpx
    except ImportError:
        return {"status": "unavailable", "reason": "httpx is not installed"}
    query = (
        f'recording:"{title}"'
        if not normalize_credit(artist) or normalize_credit(artist) in {"unknown", "unknown artist"}
        else f'recording:"{title}" AND artist:"{artist}"'
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
            response = client.get(
                "https://musicbrainz.org/ws/2/recording/",
                params={"query": query, "fmt": "json", "limit": 20},
            )
            response.raise_for_status()
            recordings = response.json().get("recordings", [])
            accepted: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
            for recording in recordings:
                credit = " ".join(
                    str(item.get("name", ""))
                    for item in recording.get("artist-credit", [])
                    if isinstance(item, dict)
                )
                confidence = metadata_match_confidence(title, artist, str(recording.get("title", "")), credit)
                length_ms = recording.get("length")
                if confidence < 0.86 or not isinstance(length_ms, (int, float)):
                    continue
                if abs(float(length_ms) / 1000.0 - duration_seconds) > 5.0:
                    continue
                for release in recording.get("releases", []):
                    if str(release.get("status", "")).casefold() != "official":
                        continue
                    accepted.append((confidence, recording, release))
            accepted.sort(key=lambda item: item[0], reverse=True)
            for confidence, recording, release in accepted:
                release_id = str(release.get("id", ""))
                if not release_id:
                    continue
                artwork = client.get(f"https://coverartarchive.org/release/{release_id}")
                if artwork.status_code != 200:
                    continue
                images = artwork.json().get("images", [])
                front = pick_cover_art_image(images)
                if not front:
                    continue
                image_url = str(front.get("image", ""))
                if not image_url:
                    continue
                image_response = client.get(image_url)
                image_response.raise_for_status()
                data = image_response.content
                destination = destination_stem.with_suffix(
                    _extension(image_response.headers.get("content-type", ""), data)
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                return {
                    "status": "needs_approval",
                    "source": "cover-art-archive",
                    "releaseStatus": "Official",
                    "approved": True,
                    "front": True,
                    "matchConfidence": round(confidence, 6),
                    "durationDeltaSeconds": round(abs(float(recording["length"]) / 1000.0 - duration_seconds), 6),
                    "musicBrainzRecordingId": recording.get("id"),
                    "musicBrainzReleaseId": release_id,
                    "releaseTitle": release.get("title"),
                    "sourceUrl": image_url,
                    "sha256": _sha256(data),
                    "path": str(destination),
                }
    except (OSError, ValueError, httpx.HTTPError) as error:
        return {"status": "unavailable", "reason": f"catalog lookup failed: {type(error).__name__}"}
    return {"status": "missing", "reason": "no exact Official release with approved front art met the thresholds"}


def _srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(np.clip(rgb, 0.0, None), 1.0 / 2.4) - 0.055)


def rgb_to_lab(rgb: Iterable[float]) -> np.ndarray:
    linear = _srgb_to_linear(np.asarray(list(rgb), dtype=np.float64))
    xyz = np.asarray(
        [
            0.4124564 * linear[0] + 0.3575761 * linear[1] + 0.1804375 * linear[2],
            0.2126729 * linear[0] + 0.7151522 * linear[1] + 0.0721750 * linear[2],
            0.0193339 * linear[0] + 0.1191920 * linear[1] + 0.9503041 * linear[2],
        ]
    ) / np.asarray([0.95047, 1.0, 1.08883])
    epsilon = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    f = np.where(xyz > epsilon, np.cbrt(xyz), (kappa * xyz + 16.0) / 116.0)
    return np.asarray([116.0 * f[1] - 16.0, 500.0 * (f[0] - f[1]), 200.0 * (f[1] - f[2])])


def lab_to_rgb(lab: Iterable[float]) -> np.ndarray:
    lightness, a_value, b_value = np.asarray(list(lab), dtype=np.float64)
    fy = (lightness + 16.0) / 116.0
    fx = fy + a_value / 500.0
    fz = fy - b_value / 200.0
    epsilon = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    values = np.asarray([fx, fy, fz])
    cubes = values**3
    xyz = np.where(cubes > epsilon, cubes, (116.0 * values - 16.0) / kappa)
    xyz *= np.asarray([0.95047, 1.0, 1.08883])
    linear = np.asarray(
        [
            3.2404542 * xyz[0] - 1.5371385 * xyz[1] - 0.4985314 * xyz[2],
            -0.9692660 * xyz[0] + 1.8760108 * xyz[1] + 0.0415560 * xyz[2],
            0.0556434 * xyz[0] - 0.2040259 * xyz[1] + 1.0572252 * xyz[2],
        ]
    )
    return np.clip(_linear_to_srgb(linear), 0.0, 1.0)


def delta_e_2000(lab1: Iterable[float], lab2: Iterable[float]) -> float:
    """CIEDE2000 using the reference kL=kC=kH=1 weighting."""

    l1, a1, b1 = map(float, lab1)
    l2, a2, b2 = map(float, lab2)
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0
    g = 0.5 * (1.0 - math.sqrt(c_bar**7 / (c_bar**7 + 25.0**7)))
    ap1, ap2 = (1.0 + g) * a1, (1.0 + g) * a2
    cp1, cp2 = math.hypot(ap1, b1), math.hypot(ap2, b2)
    hp1 = math.degrees(math.atan2(b1, ap1)) % 360.0 if cp1 else 0.0
    hp2 = math.degrees(math.atan2(b2, ap2)) % 360.0 if cp2 else 0.0
    dl = l2 - l1
    dc = cp2 - cp1
    dh_angle = hp2 - hp1
    if cp1 * cp2 == 0:
        dh_angle = 0.0
    elif dh_angle > 180.0:
        dh_angle -= 360.0
    elif dh_angle < -180.0:
        dh_angle += 360.0
    dh = 2.0 * math.sqrt(cp1 * cp2) * math.sin(math.radians(dh_angle / 2.0))
    l_bar = (l1 + l2) / 2.0
    c_prime_bar = (cp1 + cp2) / 2.0
    if cp1 * cp2 == 0:
        h_bar = hp1 + hp2
    elif abs(hp1 - hp2) <= 180.0:
        h_bar = (hp1 + hp2) / 2.0
    elif hp1 + hp2 < 360.0:
        h_bar = (hp1 + hp2 + 360.0) / 2.0
    else:
        h_bar = (hp1 + hp2 - 360.0) / 2.0
    t = 1.0 - 0.17 * math.cos(math.radians(h_bar - 30.0)) + 0.24 * math.cos(math.radians(2.0 * h_bar)) + 0.32 * math.cos(math.radians(3.0 * h_bar + 6.0)) - 0.20 * math.cos(math.radians(4.0 * h_bar - 63.0))
    sl = 1.0 + 0.015 * (l_bar - 50.0) ** 2 / math.sqrt(20.0 + (l_bar - 50.0) ** 2)
    sc = 1.0 + 0.045 * c_prime_bar
    sh = 1.0 + 0.015 * c_prime_bar * t
    rt = -2.0 * math.sqrt(c_prime_bar**7 / (c_prime_bar**7 + 25.0**7)) * math.sin(math.radians(60.0 * math.exp(-((h_bar - 275.0) / 25.0) ** 2)))
    return math.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (dh / sh) ** 2 + rt * (dc / sc) * (dh / sh))


_PROTANOPIA = np.asarray([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]])
_DEUTERANOPIA = np.asarray([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]])


def simulate_color_vision(rgb: Iterable[float], matrix: np.ndarray) -> np.ndarray:
    linear = _srgb_to_linear(np.asarray(list(rgb), dtype=np.float64))
    return np.clip(_linear_to_srgb(np.clip(matrix @ linear, 0.0, 1.0)), 0.0, 1.0)


def _relative_luminance(rgb: np.ndarray) -> float:
    linear = _srgb_to_linear(rgb)
    return float(0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2])


def _metrics(left: np.ndarray, right: np.ndarray) -> dict[str, float]:
    return {
        "deltaE2000": delta_e_2000(rgb_to_lab(left), rgb_to_lab(right)),
        "protanopiaDeltaE2000": delta_e_2000(
            rgb_to_lab(simulate_color_vision(left, _PROTANOPIA)),
            rgb_to_lab(simulate_color_vision(right, _PROTANOPIA)),
        ),
        "deuteranopiaDeltaE2000": delta_e_2000(
            rgb_to_lab(simulate_color_vision(left, _DEUTERANOPIA)),
            rgb_to_lab(simulate_color_vision(right, _DEUTERANOPIA)),
        ),
        "leftLuminance": _relative_luminance(left),
        "rightLuminance": _relative_luminance(right),
    }


def palette_passes(metrics: dict[str, float], left: np.ndarray, right: np.ndarray) -> bool:
    saturations = [colorsys.rgb_to_hsv(*color)[1] for color in (left, right)]
    return (
        metrics["deltaE2000"] >= 30.0
        and metrics["protanopiaDeltaE2000"] >= 20.0
        and metrics["deuteranopiaDeltaE2000"] >= 20.0
        and all(0.055 <= metrics[name] <= 0.88 for name in ("leftLuminance", "rightLuminance"))
        and all(value >= 0.30 for value in saturations)
    )


def _color_object(rgb: np.ndarray, scale: float = 1.0) -> dict[str, float]:
    adjusted = np.clip(rgb * scale, 0.0, 1.0)
    return {"r": round(float(adjusted[0]), 7), "g": round(float(adjusted[1]), 7), "b": round(float(adjusted[2]), 7), "a": 1.0}


def beat_saber_color_scheme(left: np.ndarray, right: np.ndarray) -> dict[str, Any]:
    return {
        "useOverride": True,
        "colorScheme": {
            "colorSchemeId": "BeatForge Cover Palette",
            "saberAColor": _color_object(left),
            "saberBColor": _color_object(right),
            "environmentColor0": _color_object(left, 0.88),
            "environmentColor1": _color_object(right, 0.88),
            "obstaclesColor": _color_object(left * 0.72 + right * 0.28, 0.72),
            "environmentColor0Boost": _color_object(np.clip(left * 1.18, 0.0, 1.0)),
            "environmentColor1Boost": _color_object(np.clip(right * 1.18, 0.0, 1.0)),
        },
    }


def palette_from_rgb(
    left: Iterable[float],
    right: Iterable[float],
    *,
    source: str,
    rationale: str,
) -> dict[str, Any]:
    left_rgb = np.asarray(list(left), dtype=np.float64)
    right_rgb = np.asarray(list(right), dtype=np.float64)
    if left_rgb.shape != (3,) or right_rgb.shape != (3,) or np.any(left_rgb < 0) or np.any(left_rgb > 1) or np.any(right_rgb < 0) or np.any(right_rgb > 1):
        return {"status": "needs_palette", "reason": "AI palette channels must be numeric values between 0 and 1"}
    metrics = _metrics(left_rgb, right_rgb)
    if not palette_passes(metrics, left_rgb, right_rgb):
        return {"status": "needs_palette", "reason": "AI palette failed the normal or color-vision readability thresholds", "metrics": {key: round(float(value), 6) for key, value in metrics.items()}}
    return {
        "status": "needs_approval",
        "source": source,
        "adjusted": False,
        "assignment": "provider-mood-palette",
        "rationale": rationale,
        "left": [round(float(value), 7) for value in left_rgb],
        "right": [round(float(value), 7) for value in right_rgb],
        "metrics": {key: round(float(value), 6) for key, value in metrics.items()},
        "thresholds": {"deltaE2000": 30.0, "colorVisionDeltaE2000": 20.0},
        "colorScheme": beat_saber_color_scheme(left_rgb, right_rgb),
    }


def write_palette_cover(path: Path, left: Iterable[float], right: Iterable[float], size: int = 512) -> None:
    left_rgb = np.asarray(list(left), dtype=np.float64)
    right_rgb = np.asarray(list(right), dtype=np.float64)
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            amount = (x + y * 0.35) / (size * 1.35)
            glow = max(0.0, 1.0 - math.hypot(x - size * 0.5, y - size * 0.45) / (size * 0.72))
            rgb = np.clip(left_rgb * (1.0 - amount) + right_rgb * amount + glow * 0.08, 0.0, 1.0)
            rows.extend(int(round(value * 255.0)) for value in rgb)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _adjust_preserving_hue(rgb: np.ndarray, lightness: float, chroma_scale: float) -> np.ndarray:
    lab = rgb_to_lab(rgb)
    lab[0] = lightness
    lab[1:] *= chroma_scale
    return lab_to_rgb(lab)


def derive_palette(cover_path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError:
        Image = None
    candidates: list[tuple[int, np.ndarray]] = []
    if Image is not None:
        try:
            with Image.open(cover_path) as source:
                image = source.convert("RGB")
                image.thumbnail((192, 192))
                quantized = image.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
                counts = sorted(quantized.getcolors() or [], reverse=True)
                raw_palette = quantized.getpalette() or []
        except (OSError, ValueError) as error:
            return {"status": "needs_palette", "reason": f"cover decode failed: {type(error).__name__}"}
        weighted_colors = [
            (int(count), np.asarray(raw_palette[index * 3 : index * 3 + 3], dtype=np.float64) / 255.0)
            for count, index in counts
        ]
    else:
        executable = find_ffmpeg()
        if not executable:
            return {"status": "needs_palette", "reason": "Pillow and FFmpeg are unavailable"}
        result = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", "-i", str(cover_path), "-vf", "scale=96:96", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            check=False,
            capture_output=True,
        )
        if result.returncode or len(result.stdout) != 96 * 96 * 3:
            return {"status": "needs_palette", "reason": "cover decode failed through FFmpeg"}
        pixels = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 3)
        buckets = (pixels // 32) * 32 + 16
        unique, counts = np.unique(buckets, axis=0, return_counts=True)
        order = np.argsort(counts)[::-1][:24]
        weighted_colors = [(int(counts[index]), unique[index].astype(np.float64) / 255.0) for index in order]
    for count, rgb in weighted_colors:
        saturation = colorsys.rgb_to_hsv(*rgb)[1]
        luminance = _relative_luminance(rgb)
        if saturation >= 0.18 and 0.025 <= luminance <= 0.94:
            candidates.append((int(count), rgb))
    pairs: list[tuple[float, np.ndarray, np.ndarray, dict[str, float], bool]] = []
    for left_index, (_left_count, first) in enumerate(candidates):
        for _right_count, second in candidates[left_index + 1 :]:
            metrics = _metrics(first, second)
            score = min(metrics["deltaE2000"], metrics["protanopiaDeltaE2000"], metrics["deuteranopiaDeltaE2000"])
            pairs.append((score, first, second, metrics, False))
    pairs.sort(key=lambda item: item[0], reverse=True)
    selected: tuple[np.ndarray, np.ndarray, dict[str, float], bool] | None = None
    for _score, first, second, metrics, adjusted in pairs:
        if palette_passes(metrics, first, second):
            selected = first, second, metrics, adjusted
            break
    if selected is None:
        for _score, first, second, _metrics_before, _adjusted in pairs[:8]:
            for lightness_left, lightness_right in ((42.0, 72.0), (68.0, 38.0), (48.0, 78.0)):
                for chroma in (1.15, 1.35, 1.55):
                    adjusted_left = _adjust_preserving_hue(first, lightness_left, chroma)
                    adjusted_right = _adjust_preserving_hue(second, lightness_right, chroma)
                    metrics = _metrics(adjusted_left, adjusted_right)
                    if palette_passes(metrics, adjusted_left, adjusted_right):
                        selected = adjusted_left, adjusted_right, metrics, True
                        break
                if selected:
                    break
            if selected:
                break
    if selected is None:
        return {
            "status": "needs_palette",
            "reason": "cover colors could not meet normal, protanopia, and deuteranopia thresholds",
            "thresholds": {"deltaE2000": 30.0, "colorVisionDeltaE2000": 20.0},
        }
    first, second, metrics, adjusted = selected
    first_hue = colorsys.rgb_to_hsv(*first)[0]
    second_hue = colorsys.rgb_to_hsv(*second)[0]
    first_warm = first_hue < 0.17 or first_hue > 0.90
    second_warm = second_hue < 0.17 or second_hue > 0.90
    if second_warm and not first_warm:
        first, second = second, first
        metrics = _metrics(first, second)
    return {
        "status": "needs_approval",
        "source": "cover",
        "adjusted": adjusted,
        "assignment": "warm-left/cool-right" if first_warm != second_warm else "maximum-distinguishability",
        "left": [round(float(value), 7) for value in first],
        "right": [round(float(value), 7) for value in second],
        "metrics": {key: round(float(value), 6) for key, value in metrics.items()},
        "thresholds": {"deltaE2000": 30.0, "colorVisionDeltaE2000": 20.0},
        "colorScheme": beat_saber_color_scheme(first, second),
    }
