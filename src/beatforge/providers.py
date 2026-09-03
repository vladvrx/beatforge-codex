"""Secure, read-only Codex review connector for BeatForge."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
import zipfile
import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PROVIDERS = {
    "codex": {
        "label": "OpenAI Codex",
        "credential": "CODEX_API_KEY",
        "defaultModel": "gpt-5.6-codex",
        "site": "https://developers.openai.com/codex/",
    },
}
SERVICE_NAME = "BeatForge"


REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "changes_required", "unable_to_review"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["error", "warning", "advisory"]},
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "difficulty": {"type": ["string", "null"]},
                    "file": {"type": ["string", "null"]},
                    "beat": {"type": ["number", "null"]},
                    "color": {"type": ["integer", "null"], "enum": [0, 1, None]},
                },
                "required": ["severity", "code", "message", "difficulty", "file", "beat", "color"],
                "additionalProperties": False,
            },
        },
        "timingAudit": {
            "type": "object",
            "properties": {
                "gridSource": {"type": ["string", "null"]},
                "clickTrackStartSample": {"type": ["integer", "null"]},
                "clickTrackMiddleSample": {"type": ["integer", "null"]},
                "clickTrackEndSample": {"type": ["integer", "null"]},
                "anchorsReviewed": {"type": "boolean"},
                "alignment": {"type": "string", "enum": ["aligned", "misaligned", "unverified"]},
                "notes": {"type": "string"},
            },
            "required": [
                "gridSource",
                "clickTrackStartSample",
                "clickTrackMiddleSample",
                "clickTrackEndSample",
                "anchorsReviewed",
                "alignment",
                "notes",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["verdict", "summary", "findings", "timingAudit"],
    "additionalProperties": False,
}

PALETTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "left": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 3, "maxItems": 3},
        "right": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 3, "maxItems": 3},
        "rationale": {"type": "string"},
    },
    "required": ["left", "right", "rationale"],
    "additionalProperties": False,
}

ANCHOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "anchors": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "beat": {"type": "number"},
                    "timeSeconds": {"type": "number"},
                    "kind": {"type": "string", "enum": ["downbeat", "beat"]},
                },
                "required": ["beat", "timeSeconds", "kind"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["anchors", "rationale"],
    "additionalProperties": False,
}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _wincred() -> Any:
    if os.name != "nt":
        raise RuntimeError("BeatForge provider credentials require Windows Credential Manager")
    return ctypes.WinDLL("Advapi32.dll", use_last_error=True)


def _target(provider: str) -> str:
    return f"BeatForge:{provider}"


def set_secret(provider: str, value: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    value = value.strip()
    if not value:
        raise ValueError("API key cannot be empty")
    encoded = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = _Credential()
    credential.Type = 1
    credential.TargetName = _target(provider)
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2
    credential.UserName = provider
    library = _wincred()
    library.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    if not library.CredWriteW(ctypes.byref(credential), 0):
        raise RuntimeError(f"Windows Credential Manager rejected the credential: {ctypes.get_last_error()}")


def get_secret(provider: str) -> str | None:
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    if os.name != "nt":
        return None
    library = _wincred()
    pointer = ctypes.POINTER(_Credential)()
    library.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
    library.CredReadW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    if not library.CredReadW(_target(provider), 1, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == 1168:
            return None
        raise RuntimeError(f"Windows Credential Manager could not read the credential: {error}")
    try:
        credential = pointer.contents
        data = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return data.decode("utf-16-le")
    finally:
        library.CredFree(pointer)


def delete_secret(provider: str) -> None:
    library = _wincred()
    library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    if not library.CredDeleteW(_target(provider), 1, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise RuntimeError(f"Windows Credential Manager could not delete the credential: {error}")


def runner_status(provider: str) -> dict[str, Any]:
    configured = False
    credential_error = None
    try:
        configured = bool(get_secret(provider))
    except RuntimeError as error:
        credential_error = str(error)
    executable = _find_codex()
    return {
        "provider": provider,
        "label": PROVIDERS[provider]["label"],
        "configured": configured,
        "credentialBackend": "Windows Credential Manager" if credential_error is None else "unavailable",
        "credentialError": credential_error,
        "runnerInstalled": bool(executable),
        "runner": executable,
        "site": PROVIDERS[provider]["site"],
    }


def _find_codex() -> str | None:
    local_data = os.environ.get("LOCALAPPDATA")
    if local_data:
        candidates = sorted((Path(local_data) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"), reverse=True)
        if candidates:
            return str(candidates[0])
    return shutil.which("codex") or shutil.which("codex.exe")


def discover_models(provider: str, key: str) -> list[str]:
    """Fetch the models available to the configured provider account."""

    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    try:
        import httpx
    except ImportError as error:
        raise RuntimeError("httpx is required for provider model discovery") from error
    response = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=20.0,
    )
    try:
        response.raise_for_status()
        models = [str(item["id"]) for item in response.json().get("data", []) if isinstance(item, dict) and item.get("id")]
    except (ValueError, KeyError, httpx.HTTPError) as error:
        raise RuntimeError(f"{PROVIDERS[provider]['label']} model discovery failed") from error
    models = [model for model in models if "codex" in model.casefold()]
    return sorted(set(models))


def suggest_mood_palette(
    *,
    job_dir: Path,
    provider: str,
    model: str,
    metadata_approved: bool,
) -> dict[str, Any]:
    """Ask a configured provider for two colors using metadata and analysis only."""

    if not metadata_approved:
        raise ValueError("approve metadata and analysis transfer before AI palette selection")
    key = get_secret(provider)
    if not key:
        raise ValueError(f"{PROVIDERS[provider]['label']} API key is not configured")
    analysis_path = job_dir / "map" / "_beatforge" / "analysis.json"
    if not analysis_path.is_file():
        raise ValueError("audio analysis summary is missing")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    summary = {
        "title": status.get("title"),
        "artist": status.get("artist"),
        "bpm": analysis.get("bpm"),
        "durationSeconds": analysis.get("durationSeconds"),
        "tempoRegions": analysis.get("tempoRegions", [])[:12],
        "onsetStrengthSummary": {
            "eventCount": len(analysis.get("events", [])),
            "maximum": max([float(event.get("strength", 0)) for event in analysis.get("events", [])] + [0.0]),
        },
    }
    prompt = (
        "Choose two readable Beat Saber note colors for this song. Left and right are RGB arrays from 0 to 1. "
        "They must be visually distinct under normal, protanopia, and deuteranopia vision. Prefer warm left and cool right when musically appropriate. "
        "Return only the requested JSON. Song summary: "
        + json.dumps(summary, separators=(",", ":"))
    )
    suggestion, run_id = _call_provider_json(
        provider=provider,
        model=model,
        key=key,
        prompt=prompt,
        schema=PALETTE_SCHEMA,
        schema_name="beatforge_palette",
        output_name="palette",
        job_dir=job_dir,
    )
    scripts = Path(__file__).resolve().parents[2] / "skills" / "beat-saber-mapping" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from artwork import palette_from_rgb, write_palette_cover

    palette = palette_from_rgb(suggestion.get("left", []), suggestion.get("right", []), source=f"{provider}-mood-analysis", rationale=str(suggestion.get("rationale", "")))
    if palette.get("status") != "needs_approval":
        return {"status": "needs_palette", "palette": palette, "provider": provider, "model": model, "runId": run_id}
    cover_path = job_dir / "map" / "_beatforge" / "ai_palette_cover.png"
    write_palette_cover(cover_path, palette["left"], palette["right"])
    artwork = {"status": "found", "source": "generated-palette-card", "path": str(cover_path), "sha256": _hash_file(cover_path), "frontCover": False}
    candidate = {"status": "needs_approval", "artwork": artwork, "palette": palette, "approvalRequired": True, "provider": provider, "model": model, "runId": run_id, "transferred": summary}
    candidate_path = job_dir / "map" / "_beatforge" / "palette_candidate.json"
    candidate_path.write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    return candidate


def _call_provider_json(
    *,
    provider: str,
    model: str,
    key: str,
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    output_name: str,
    job_dir: Path,
) -> tuple[dict[str, Any], Any]:
    try:
        import httpx
    except ImportError as error:
        raise RuntimeError(f"httpx is required for AI {output_name} selection") from error
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "input": prompt, "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}}},
        timeout=60.0,
    )
    response.raise_for_status()
    body = response.json()
    text_value = body.get("output_text")
    if not text_value:
        text_value = "".join(
            str(content.get("text", ""))
            for item in body.get("output", [])
            for content in item.get("content", [])
            if isinstance(content, dict) and content.get("type") == "output_text"
        )
    return _parse_json_object(str(text_value), output_name), body.get("id")


def _load_analyze_audio():
    scripts = Path(__file__).resolve().parents[2] / "skills" / "beat-saber-mapping" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import analyze_audio

    return analyze_audio


def _post_ai_dual_clock(job_dir: Path) -> dict[str, Any]:
    """Re-check Beat This and BeatNet+ against the adopted grid after an AI timing pass."""
    analysis_path = job_dir / "map" / "_beatforge" / "analysis.json"
    if not analysis_path.is_file():
        return {"stage": "post-ai", "passes": False, "reason": "analysis missing"}
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    check = _load_analyze_audio().dual_clock_vs_grid(analysis)
    analysis["dualClockCheckAfterAi"] = check
    analysis_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    return check


def _timing_summary(analysis: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    trackers = []
    for item in analysis.get("mixTrackers", analysis.get("trackers", [])) or []:
        if not isinstance(item, dict):
            continue
        beats = item.get("beatsSeconds") or item.get("beats") or item.get("beatTimes") or []
        times = [float(value) for value in beats[:8]] if isinstance(beats, list) else []
        trackers.append(
            {
                "name": item.get("name") or item.get("model"),
                "beatCount": len(beats) if isinstance(beats, list) else item.get("beatCount"),
                "firstBeatsSeconds": times,
            }
        )
    return {
        "title": status.get("title"),
        "artist": status.get("artist"),
        "status": analysis.get("status"),
        "gridSource": analysis.get("gridSource"),
        "primaryTracker": analysis.get("primaryTracker"),
        "bpm": analysis.get("bpm"),
        "durationSeconds": analysis.get("durationSeconds"),
        "sampleRate": analysis.get("sampleRate"),
        "residuals": analysis.get("residuals"),
        "modelErrors": analysis.get("modelErrors", []),
        "clickTrackEvidence": analysis.get("clickTrackEvidence"),
        "suggestions": analysis.get("anchorSuggestions") or analysis.get("suggestions") or [],
        "tempoRegions": analysis.get("tempoRegions", [])[:12],
        "mixTrackers": trackers[:8],
        "dualClockCheck": analysis.get("dualClockCheck"),
        "clockAgreement": analysis.get("externalAgreement"),
    }


def _normalize_proposed_anchors(raw: Any, sample_rate: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("provider must return at least two strictly increasing anchors")
    anchors: list[dict[str, Any]] = []
    last_beat = last_sample = None
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or "beat" not in item:
            raise ValueError(f"anchor {index} is missing beat")
        if "sample" not in item and "timeSeconds" not in item:
            raise ValueError(f"anchor {index} requires timeSeconds or sample")
        beat = float(item["beat"])
        time_seconds = float(item["timeSeconds"]) if "timeSeconds" in item else int(item["sample"]) / float(sample_rate)
        sample = int(item["sample"]) if "sample" in item else int(round(time_seconds * sample_rate))
        if last_beat is not None and (beat <= last_beat or sample <= int(last_sample or 0)):
            raise ValueError("provider anchors must increase strictly in beat and sample")
        kind = str(item.get("kind") or ("downbeat" if index == 0 else "beat"))
        if kind not in {"downbeat", "beat"}:
            kind = "beat"
        last_beat, last_sample = beat, sample
        anchors.append({"beat": beat, "timeSeconds": time_seconds, "sample": sample, "kind": kind})
    return anchors


def suggest_timing_anchors(
    *,
    job_dir: Path,
    provider: str,
    model: str,
    analysis_approved: bool,
) -> dict[str, Any]:
    """Ask a configured provider for piecewise anchors using analysis JSON only. Never sends song audio."""

    if not analysis_approved:
        raise ValueError("approve analysis-summary transfer before AI timing anchors")
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    key = get_secret(provider)
    if not key:
        raise ValueError(f"{PROVIDERS[provider]['label']} API key is not configured")
    analysis_path = job_dir / "map" / "_beatforge" / "analysis.json"
    if not analysis_path.is_file():
        raise ValueError("audio analysis summary is missing")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    summary = _timing_summary(analysis, status)
    encoded = json.dumps(summary, separators=(",", ":"))
    prompt = (
        "The Beat This transformer is the mix clock. BeatNet+ is the challenger only. "
        "All-In-One is structure, not a beat clock. Propose two or more strictly increasing "
        "piecewise beat anchors for a 44100 Hz grid. Use integer-sample-aligned timeSeconds from clickTrackEvidence "
        "and tracker first beats. Do not invent a constant BPM that fights live drift. Do not request or assume song audio. "
        "Return only JSON with anchors and rationale. Analysis summary: "
        + encoded
    )
    suggestion, run_id = _call_provider_json(
        provider=provider,
        model=model,
        key=key,
        prompt=prompt,
        schema=ANCHOR_SCHEMA,
        schema_name="beatforge_anchors",
        output_name="anchors",
        job_dir=job_dir,
    )
    sample_rate = int(analysis.get("sampleRate") or 44100)
    anchors = _normalize_proposed_anchors(suggestion.get("anchors"), sample_rate)
    proposal = {
        "status": "proposed",
        "provider": provider,
        "model": model,
        "runId": run_id,
        "rationale": str(suggestion.get("rationale", "")),
        "anchors": anchors,
        "transferred": summary,
        "audioTransferred": False,
    }
    (job_dir / "map" / "_beatforge").mkdir(parents=True, exist_ok=True)
    (job_dir / "map" / "_beatforge" / "anchor_proposal.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    proposal["dualClockCheckAfterAi"] = _post_ai_dual_clock(job_dir)
    (job_dir / "map" / "_beatforge" / "anchor_proposal.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    return proposal


def _review_path_is_private(relative: str) -> bool:
    lower = relative.casefold()
    name = Path(relative).name.casefold()
    if "official-corpus" in lower or lower.endswith(".sqlite3"):
        return True
    if name in {".env", "credentials.json", "secrets.json"} or name.endswith((".pem", ".key")):
        return True
    if "credential" in name or "api_key" in name or "apikey" in name:
        return True
    return False


_REVIEW_MEDIA_SUFFIXES = {
    ".ogg",
    ".egg",
    ".wav",
    ".mp3",
    ".flac",
    ".m4a",
    ".mp4",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}


def _review_entry_is_media(name: str) -> bool:
    return Path(name).suffix.casefold() in _REVIEW_MEDIA_SUFFIXES


def _safe_review_files(job_dir: Path, skill_dir: Path) -> list[tuple[Path, str]]:
    map_dir = job_dir / "map"
    allowed: list[tuple[Path, str]] = []
    for path in sorted(map_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(map_dir).as_posix()
        if _review_path_is_private(relative):
            continue
        allowed.append((path, f"map/{relative}"))
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(skill_dir).as_posix()
        if relative.endswith(".pyc") or _review_path_is_private(relative):
            continue
        allowed.append((path, f"skill/beat-saber-mapping/{relative}"))
    manifest = job_dir / "documentation-manifest.json"
    if manifest.is_file():
        allowed.append((manifest, "documentation-manifest.json"))
    return allowed


def prepare_review_bundle(
    *,
    job_dir: Path,
    skill_dir: Path,
    provider: str,
    model: str,
    maximum_cost_usd: float,
) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    files = _safe_review_files(job_dir, skill_dir)
    if not files:
        raise ValueError("generated review files are missing")
    destination = job_dir / f"review-{provider}.zip"
    entries: list[dict[str, Any]] = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source, name in files:
            archive.write(source, name)
            entries.append({"name": name, "bytes": source.stat().st_size, "sha256": _hash_file(source)})
    bundle_hash = _hash_file(destination)
    token = secrets.token_urlsafe(32)
    manifest = {
        "provider": provider,
        "model": model,
        "bundleHash": bundle_hash,
        "bundleBytes": destination.stat().st_size,
        "estimatedMaximumCostUsd": round(maximum_cost_usd, 2),
        "contents": entries,
        "containsAudioAndArtwork": any(_review_entry_is_media(entry["name"]) for entry in entries),
        "rightsAttestationRequired": True,
        "officialCorpusIncluded": False,
        "expiresAt": int(time.time()) + 1800,
        "consentToken": token,
    }
    (job_dir / f"review-{provider}-consent.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _review_prompt(bundle_hash: str) -> str:
    return f"""Run a read-only Beat Saber release review for bundle {bundle_hash}.
This review is the timing and playability audit for OpenAI Codex.

Read documentation-manifest.json, the portable beat-saber-mapping skill, map/Info.dat,
all five Standard difficulty files, map/_beatforge/analysis.json, beat_grid.json,
anchors if present, clickTrackEvidence, provenance.json, and qa_report.json.
Do not edit any file. Local hard failures cannot be dismissed.

Timing audit is mandatory. Quote integer sample checkpoints for the click track at
the start, middle, and end. Compare them to analysis.json clickTrackEvidence.
The mix clock is Beat This (`primaryTracker`). After this review, BeatForge will
re-check that grid against both Beat This and BeatNet+. Read dualClockCheck and
both mix tracker beat lists. All-In-One sections are not a vote on BPM. If
gridSource is confirmed or explicit-bpm-offset, inspect the authored anchors
and say whether the piecewise grid still lines up with those samples. Do not treat
model disagreement as a reason to invent a new BPM. If the click-track samples
drift from the music, set timingAudit.alignment to misaligned and verdict to
changes_required.

Report each finding with exact difficulty, file, beat, and color when applicable.
Return only the requested JSON object, including timingAudit."""


def _parse_json_object(value: Any, output_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        payload = value
    else:
        text = str(value).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
            for line in reversed(text.splitlines()):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and ({"verdict", "left", "right", "anchors"} & set(event)):
                    payload = event
                    break
                candidate = event.get("structured_output") or event.get("result") or event.get("output")
                if candidate is None:
                    continue
                try:
                    payload = candidate if isinstance(candidate, dict) else json.loads(str(candidate))
                except json.JSONDecodeError:
                    continue
                break
            if payload is None:
                raise ValueError(f"provider output did not contain the {output_name} schema")
    if "structured_output" in payload:
        payload = payload["structured_output"]
    elif "result" in payload and not ({"verdict", "left", "right", "anchors"} & set(payload)):
        payload = payload["result"] if isinstance(payload["result"], dict) else json.loads(str(payload["result"]))
    if not isinstance(payload, dict):
        raise ValueError(f"provider returned an invalid {output_name} object")
    return payload


def _parse_result(value: Any) -> dict[str, Any]:
    payload = _parse_json_object(value, "review")
    if payload.get("verdict") not in {"pass", "changes_required", "unable_to_review"}:
        raise ValueError("provider returned an invalid verdict")
    if not isinstance(payload.get("findings"), list):
        raise ValueError("provider returned invalid findings")
    audit = payload.get("timingAudit")
    if not isinstance(audit, dict):
        raise ValueError("provider returned invalid timingAudit")
    if audit.get("alignment") not in {"aligned", "misaligned", "unverified"}:
        raise ValueError("provider returned invalid timingAudit")
    if not isinstance(audit.get("anchorsReviewed"), bool):
        raise ValueError("provider returned invalid timingAudit")
    if audit.get("alignment") == "misaligned" and payload.get("verdict") == "pass":
        raise ValueError("timingAudit misaligned cannot pass")
    return payload


def _run_process(
    *,
    command: list[str],
    staging: Path,
    env_key: str,
    secret: str,
    output_file: Path,
    timeout: float = 180.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    def redacted(value: str) -> str:
        return value.replace(secret, "[REDACTED]") if secret else value

    child_env = os.environ.copy()
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        child_env.pop(name, None)
    child_env[env_key] = secret
    try:
        result = subprocess.run(
            command,
            cwd=staging,
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        stderr = error.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raise RuntimeError(f"provider process timed out: {redacted(str(stderr)[-1200:])}") from error
    if result.returncode:
        raise RuntimeError(
            f"provider process failed with exit code {result.returncode}: {redacted(result.stderr[-1200:])}"
        )
    text = output_file.read_text(encoding="utf-8") if output_file.is_file() else result.stdout
    return _parse_result(text), {"events": redacted(result.stdout[-12000:])}


def run_review(
    *,
    job_dir: Path,
    provider: str,
    consent_token: str,
    approved_bundle_hash: str,
    rights_attested: bool,
    maximum_cost_usd: float,
    progress: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    consent_path = job_dir / f"review-{provider}-consent.json"
    if not consent_path.is_file():
        raise ValueError("prepare the exact review bundle first")
    consent = json.loads(consent_path.read_text(encoding="utf-8"))
    if not secrets.compare_digest(str(consent.get("consentToken", "")), consent_token):
        raise ValueError("consent token does not match the prepared bundle")
    if approved_bundle_hash != consent.get("bundleHash"):
        raise ValueError("approved bundle hash does not match")
    if int(consent.get("expiresAt", 0)) < int(time.time()):
        raise ValueError("consent expired; prepare the bundle again")
    if not rights_attested:
        raise ValueError("rights attestation is required before audio or artwork leaves this PC")
    if maximum_cost_usd > float(consent.get("estimatedMaximumCostUsd", 0.0)) + 1e-9:
        raise ValueError("approved cost exceeds the prepared maximum")
    key = get_secret(provider)
    if not key:
        raise ValueError(f"{PROVIDERS[provider]['label']} API key is not configured")
    bundle = job_dir / f"review-{provider}.zip"
    if _hash_file(bundle) != approved_bundle_hash:
        raise ValueError("review bundle changed after consent")
    staging = job_dir / f"review-{provider}-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(staging)
    before = {path.relative_to(staging).as_posix(): _hash_file(path) for path in staging.rglob("*") if path.is_file()}
    schema_path = staging / "review-schema.json"
    schema_path.write_text(json.dumps(REVIEW_SCHEMA, indent=2), encoding="utf-8")
    prompt = _review_prompt(approved_bundle_hash)
    model = str(consent["model"])
    if progress:
        progress("reviewing", f"{PROVIDERS[provider]['label']} is reviewing the approved read-only bundle")
    executable = _find_codex()
    if not executable:
        raise RuntimeError("OpenAI Codex CLI is not installed or unavailable")
    output_file = staging / "review-output.json"
    payload, usage = _run_process(
        command=[
            executable,
            "exec",
            "--ephemeral",
            "--json",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--model",
            model,
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_file),
            prompt,
        ],
        staging=staging,
        env_key="CODEX_API_KEY",
        secret=key,
        output_file=output_file,
    )
    after = {path.relative_to(staging).as_posix(): _hash_file(path) for path in staging.rglob("*") if path.is_file() and path.name not in {"review-schema.json", "review-output.json"}}
    if before != after:
        raise RuntimeError("read-only reviewer changed the approved bundle")
    report = {
        "provider": provider,
        "model": model,
        "runId": usage.get("runId"),
        "bundleHash": approved_bundle_hash,
        "usage": usage,
        "verdict": payload["verdict"],
        "summary": payload["summary"],
        "findings": payload["findings"],
        "timingAudit": payload.get("timingAudit"),
        "dualClockCheckAfterAi": _post_ai_dual_clock(job_dir),
        "unresolvedFindings": len(payload["findings"]),
    }
    (job_dir / f"review-{provider}-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
