"""Explicit training and held-out song lists, identified by audio content hash."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .features import AnalysisFeatures, file_sha256, load_analysis_features


def load_library(manifest: Path) -> list[tuple[dict[str, Any], AnalysisFeatures]]:
    manifest = Path(manifest).resolve()
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != 1 or not isinstance(document.get("tracks"), list):
        raise ValueError("Library manifest requires schemaVersion 1 and a tracks list")
    result = []
    identities: set[str] = set()
    hashes: set[str] = set()
    for track in document["tracks"]:
        identity = str(track["id"])
        if identity in identities:
            raise ValueError(f"Duplicate track ID: {identity}")
        if track.get("split") not in {"train", "validation", "test"}:
            raise ValueError(f"Track {identity} requires split train, validation, or test")
        path = Path(track["analysisDir"])
        path = path if path.is_absolute() else manifest.parent / path
        bundle = load_analysis_features(path)
        source = bundle.provenance.get("sourceSha256")
        if not isinstance(source, str) or len(source) != 64 or any(c not in "0123456789abcdef" for c in source.lower()):
            raise ValueError(f"Track {identity} requires an analyzed audio SHA-256")
        source = source.lower()
        if track.get("audioPath"):
            audio = Path(track["audioPath"])
            audio = audio if audio.is_absolute() else manifest.parent / audio
            if file_sha256(audio) != source:
                raise ValueError(f"Audio content changed since analysis: {identity}")
            bundle.provenance["sourceAudioVerified"] = True
        if source in hashes:
            raise ValueError(f"Duplicate audio content or split leakage: {identity}")
        hashes.add(source)
        identities.add(identity)
        result.append(({**track, "analysisDir": str(path.resolve())}, bundle))
    if not result:
        raise ValueError("Library manifest contains no tracks")
    return result
