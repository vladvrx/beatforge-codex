"""Rights-aware song metadata and album-art lookup for BeatForge."""

from __future__ import annotations

import hashlib
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


USER_AGENT = "BeatForge/0.3 (local Beat Saber mapping tool; metadata lookup)"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ARTWORK_HOST_SUFFIXES = (".mzstatic.com",)


class SongMetadataNotFound(ValueError):
    """Raised when the provider has no sufficiently matching track."""


def normalize_credit(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"\([^)]*(official|audio|video|lyrics?)[^)]*\)", " ", value)
    value = re.sub(r"\b(feat|ft)\.?\b", " featuring ", value)
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


def metadata_match_confidence(title: str, artist: str, candidate_title: str, candidate_artist: str) -> float:
    title_score = SequenceMatcher(None, normalize_credit(title), normalize_credit(candidate_title)).ratio()
    artist_norm = normalize_credit(artist)
    if not artist_norm or artist_norm in {"unknown", "unknown artist"}:
        return title_score
    artist_score = SequenceMatcher(None, artist_norm, normalize_credit(candidate_artist)).ratio()
    return 0.68 * title_score + 0.32 * artist_score


def _safe_artwork_url(value: object) -> str | None:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not host or not host.endswith(ARTWORK_HOST_SUFFIXES):
        return None
    return raw


def _large_artwork_url(value: object) -> str | None:
    safe = _safe_artwork_url(value)
    if not safe:
        return None
    return re.sub(r"\d+x\d+(?:bb)?", "600x600bb", safe)


def _extension(content_type: str, data: bytes) -> str:
    if "png" in content_type.casefold() or data.startswith(b"\x89PNG"):
        return ".png"
    return ".jpg"


def _derive_palette(cover_path: Path) -> dict[str, Any] | None:
    scripts = Path(__file__).resolve().parents[2] / "skills" / "beat-saber-mapping" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from artwork import derive_palette

        return derive_palette(cover_path)
    except (ImportError, OSError, ValueError):
        return None


def _public_track(item: dict[str, Any], confidence: float) -> dict[str, Any]:
    duration_ms = item.get("trackTimeMillis")
    preview_url = str(item.get("previewUrl") or "").strip()
    if preview_url.startswith("http://"):
        preview_url = "https://" + preview_url[7:]
    artwork_url = _large_artwork_url(item.get("artworkUrl100"))
    return {
        "trackId": item.get("trackId"),
        "title": str(item.get("trackName") or "").strip(),
        "artist": str(item.get("artistName") or "").strip(),
        "album": str(item.get("collectionName") or "").strip() or None,
        "albumArtist": str(item.get("collectionArtistName") or "").strip() or None,
        "releaseDate": str(item.get("releaseDate") or "").strip() or None,
        "genre": str(item.get("primaryGenreName") or "").strip() or None,
        "country": str(item.get("country") or "").strip() or None,
        "trackNumber": item.get("trackNumber"),
        "discNumber": item.get("discNumber"),
        "durationSeconds": round(float(duration_ms) / 1000.0, 3) if isinstance(duration_ms, (int, float)) else None,
        "explicit": str(item.get("trackExplicitness") or "").casefold() == "explicit",
        "matchConfidence": round(confidence, 6),
        "trackUrl": item.get("trackViewUrl"),
        "albumUrl": item.get("collectionViewUrl"),
        "artworkUrl": artwork_url,
        "previewUrl": preview_url or None,
    }


def search_song_metadata(title: str, artist: str = "", *, cache_dir: Path) -> dict[str, Any]:
    """Resolve a track from title/artist and cache its provider artwork locally.

    The returned audio URL is only the provider's short preview. No full-song
    download URL is inferred from metadata.
    """

    title = str(title or "").strip()
    artist = str(artist or "").strip()
    if not title:
        raise SongMetadataNotFound("A song title is required")
    term = f"{title} {artist}".strip()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
            response = client.get(ITUNES_SEARCH_URL, params={"term": term, "media": "music", "entity": "song", "limit": 25})
            response.raise_for_status()
            raw_results = response.json().get("results", [])
            ranked: list[tuple[float, dict[str, Any]]] = []
            for item in raw_results:
                if not isinstance(item, dict) or item.get("kind") not in {None, "song"}:
                    continue
                score = metadata_match_confidence(title, artist, str(item.get("trackName") or ""), str(item.get("artistName") or ""))
                if score >= 0.62:
                    ranked.append((score, item))
            ranked.sort(key=lambda value: value[0], reverse=True)
            if not ranked:
                raise SongMetadataNotFound(f"No close match found for {title} · {artist or 'unknown artist'}")
            confidence, item = ranked[0]
            track = _public_track(item, confidence)
            metadata_id = hashlib.sha256(f"{track.get('trackId')}|{track['title']}|{track['artist']}".encode("utf-8")).hexdigest()[:20]
            palette: dict[str, Any] | None = None
            cache_path: Path | None = None
            artwork_url = track.get("artworkUrl")
            if artwork_url:
                cover_response = client.get(artwork_url)
                cover_response.raise_for_status()
                cover_data = cover_response.content
                if len(cover_data) >= 2000:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_path = cache_dir / f"{metadata_id}{_extension(cover_response.headers.get('content-type', ''), cover_data)}"
                    cache_path.write_bytes(cover_data)
                    palette = _derive_palette(cache_path)
            audio = None
            if track.get("previewUrl"):
                audio = {
                    "kind": "provider-preview",
                    "url": track["previewUrl"],
                    "durationSeconds": track.get("durationSeconds"),
                    "downloadable": False,
                    "label": "Short provider preview; not the full recording",
                }
            alternatives = [_public_track(candidate, score) for score, candidate in ranked[1:5]]
            return {
                "status": "found",
                "source": "itunes-search",
                "query": {"title": title, "artist": artist},
                "metadataId": metadata_id,
                "track": track,
                "alternatives": alternatives,
                "artwork": {"url": artwork_url, "source": "itunes-search", "cached": bool(cache_path)} if artwork_url else None,
                "palette": palette,
                "audio": audio,
                "fullRecordingDownload": None,
                "_cachePath": str(cache_path) if cache_path else None,
            }
    except SongMetadataNotFound:
        raise
    except (OSError, ValueError, httpx.HTTPError) as error:
        raise RuntimeError(f"Song metadata lookup failed: {type(error).__name__}") from error
