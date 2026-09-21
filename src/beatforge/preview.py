"""Read chart previews and create isolated, fully validated section revisions."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from beatforge.premium import ROOT, SCRIPTS, package_map, summarize_map

router = APIRouter()
COLLECTIONS = ("colorNotes", "bombNotes", "obstacles", "sliders", "burstSliders", "basicBeatmapEvents", "colorBoostBeatmapEvents", "lightColorEventBoxGroups", "lightRotationEventBoxGroups", "lightTranslationEventBoxGroups", "vfxEventBoxGroups")


@router.post("/api/mapping-plan/resolve")
def resolve_mapping_plan(payload: dict[str, Any]) -> dict[str, Any]:
    from beatforge.mapping_plan import normalize_mapping_plan
    try:
        return normalize_mapping_plan(payload)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def confined(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404, "Referenced map file is missing or outside the map folder")
    return path


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (OSError, ValueError) as error:
        raise HTTPException(422, f"Cannot read {path.name}: {error}") from error


def chart_refs(info: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for group in info.get("_difficultyBeatmapSets", []) if group.get("_beatmapCharacteristicName") == "Standard" for entry in group.get("_difficultyBeatmaps", [])]


def chart_identity(folder: Path) -> str:
    info = read_json(confined(folder, "Info.dat"))
    names = ["Info.dat", str(info.get("_songFilename", "song.ogg"))] + [str(entry["_beatmapFilename"]) for entry in chart_refs(info)]
    digest = hashlib.sha256()
    for name in sorted(set(names)):
        digest.update(name.encode())
        with confined(folder, name).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1048576), b''):
                digest.update(chunk)
    return digest.hexdigest()


def _map(job_id: str) -> tuple[dict[str, Any], Path]:
    from beatforge import api
    status = api._read_status(job_id)
    folder = api._job_dir(job_id) / "map"
    if not (folder / "Info.dat").is_file():
        raise HTTPException(404, "This run has no chart to preview yet")
    return status, folder


def waveform(path: Path, bins: int = 1200) -> tuple[float, list[float]]:
    """Read bounded blocks, including multichannel audio, without loading a song in RAM."""
    import numpy as np
    import soundfile as sf
    with sf.SoundFile(path) as audio:
        duration = len(audio) / audio.samplerate
        width = max(1, math.ceil(len(audio) / bins))
        peaks: list[float] = []
        while len(peaks) < bins:
            block = audio.read(width, dtype="float32", always_2d=True)
            if not len(block):
                break
            peaks.append(round(float(np.max(np.abs(block))), 4))
    return duration, peaks


def preview_payload(folder: Path, difficulty: str | None = None) -> dict[str, Any]:
    if not (folder / "_beatforge").resolve().is_relative_to(folder.resolve()):
        raise HTTPException(404, "Map metadata is outside the map folder")
    info = read_json(confined(folder, "Info.dat"))
    refs = chart_refs(info)
    if not refs:
        raise HTTPException(422, "The preview currently supports Standard charts with v2 Info and v3 gameplay")
    selected = next((ref for ref in refs if ref.get("_difficulty") == difficulty), None) if difficulty else refs[0]
    if not selected:
        raise HTTPException(404, "Difficulty is not present in this map")
    filename = str(selected["_beatmapFilename"])
    chart = read_json(confined(folder, filename))
    if not str(chart.get("version", "")).startswith("3."):
        raise HTTPException(422, "The preview supports v3 gameplay. This chart's format is preserved unchanged.")
    audio_path = confined(folder, str(info.get("_songFilename", "song.ogg")))
    with audio_path.open('rb') as stream:
        audio_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    cache = folder / "_beatforge" / "preview-waveform.json"
    if not cache.resolve().is_relative_to(folder.resolve()):
        raise HTTPException(404, "Waveform cache is outside the map folder")
    cached: dict[str, Any] = {}
    if cache.is_file():
        try:
            cached = read_json(cache)
        except HTTPException:
            pass
    cache_duration = cached.get("duration")
    cache_peaks = cached.get("waveform")
    valid_cache = (
        isinstance(cache_duration, (int, float)) and math.isfinite(cache_duration) and cache_duration > 0
        and isinstance(cache_peaks, list) and 0 < len(cache_peaks) <= 1200
        and all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1 for value in cache_peaks)
    )
    if cached.get("audioHash") != audio_hash or not valid_cache:
        duration, peaks = waveform(audio_path)
        cached = {"audioHash": audio_hash, "duration": duration, "waveform": peaks}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(cached), encoding="utf-8")
    metadata: dict[str, Any] = {}
    for key, name in (("analysis", "analysis.json"), ("sections", "sections.json"), ("qa", "qa_report.json"), ("provenance", "provenance.json"), ("mappingPlan", "mapping_plan.json")):
        candidate = folder / "_beatforge" / name
        metadata[key] = read_json(confined(folder, str(candidate.relative_to(folder)))) if candidate.is_file() else {}
    bpm = float(info.get("_beatsPerMinute", 120))
    if not math.isfinite(bpm) or bpm <= 0:
        raise HTTPException(422, "Invalid chart tempo")
    color_schemes = info.get("_colorSchemes") or []
    scheme_index = int(selected.get("_beatmapColorSchemeIdx", -1))
    scheme = color_schemes[scheme_index].get("colorScheme", {}) if 0 <= scheme_index < len(color_schemes) else {}
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from timing_export import project_plan_timing
    section_plan = metadata["mappingPlan"]
    if not section_plan.get("sections"):
        section_plan = project_plan_timing(metadata["sections"], metadata["analysis"]) if metadata["analysis"].get("beatGrid") else metadata["sections"]
    qa = metadata["qa"]
    findings = [item for item in qa.get("errors", []) + qa.get("warnings", []) if item.get("file") in (None, filename) or item.get("difficulty") == selected["_difficulty"]]
    return {
        "schemaVersion": 1, "title": info.get("_songName", "Untitled"), "artist": info.get("_songAuthorName", ""),
        "difficulty": selected["_difficulty"], "difficulties": [item["_difficulty"] for item in refs], "filename": filename,
        "bpm": bpm, "offsetSeconds": float(info.get("_songTimeOffset", 0)), "duration": cached["duration"], "waveform": cached["waveform"],
        "chart": chart, "chartHash": chart_identity(folder), "audioHash": audio_hash, "sections": section_plan.get("sections", []), "findings": findings,
        "mappingPlan": metadata["mappingPlan"], "provenance": {key: metadata["provenance"].get(key) for key in ("seed", "engine", "model", "revision", "mappingPlan")},
        "colors": {"left": scheme.get("saberAColor"), "right": scheme.get("saberBColor")},
        "timingVerified": metadata["analysis"].get("status") == "timing_verified",
    }


@router.get("/api/jobs/{job_id}/preview")
def job_preview(job_id: str, difficulty: str | None = Query(None)) -> dict[str, Any]:
    status, folder = _map(job_id)
    result = preview_payload(folder, difficulty)
    analysis_path = folder / "_beatforge" / "analysis.json"
    has_grid = analysis_path.is_file() and len(read_json(confined(folder, "_beatforge/analysis.json")).get("beatGrid", [])) >= 2
    result.update({"jobId": job_id, "audioUrl": f"/api/jobs/{job_id}/audio", "canRevise": status.get("localStatus") in {"playtest_candidate", "unconfirmed_pack"} and has_grid and not result["chart"].get("bpmEvents") and (folder / "_beatforge" / "sections.json").is_file()})
    return result


@router.get("/api/jobs/{job_id}/audio")
def job_audio(job_id: str) -> FileResponse:
    _, folder = _map(job_id)
    info = read_json(confined(folder, "Info.dat"))
    return FileResponse(confined(folder, str(info.get("_songFilename", "song.ogg"))))


def interval(item: dict[str, Any], collection: str) -> tuple[float, float]:
    start = float(item.get("b", 0))
    end = float(item.get("tb", start)) if collection in {"sliders", "burstSliders"} else start + float(item.get("d", 0)) if collection == "obstacles" else start
    if collection.endswith("EventBoxGroups"):
        if collection == "vfxEventBoxGroups":
            raise ValueError("VFX event box timing cannot yet be revised safely")
        offsets = [0.0]
        for box in item.get("e", []):
            if float(box.get("w", 0)) != 0:
                raise ValueError("Distributed lighting timing cannot yet be revised safely")
            for key in ("e", "l", "t"):
                offsets.extend(float(event.get("b", 0)) for event in box.get(key, []))
        if any(not math.isfinite(offset) or offset < 0 for offset in offsets):
            raise ValueError("Invalid relative lighting timing")
        end = start + max(offsets)
    if not math.isfinite(start) or not math.isfinite(end) or end < start:
        raise ValueError("An object has invalid timing")
    return start, end


def merge_section(original: dict[str, Any], candidate: dict[str, Any], start: float, end: float) -> dict[str, Any]:
    """A half-open [start, end) edit. Crossing objects require a wider selection."""
    if not math.isfinite(start) or not math.isfinite(end) or end <= start or start < 0:
        raise ValueError("Select an increasing, nonnegative beat range")
    result = copy.deepcopy(original)
    for collection in COLLECTIONS:
        def inside(item: dict[str, Any]) -> bool:
            head, tail = interval(item, collection)
            overlaps = head < end and tail >= start
            included = start <= head < end and tail < end
            if tail > head and overlaps and not included:
                raise ValueError(f"Selection cuts through {collection} at beat {head:g}. Widen the selection to include the whole object.")
            return included
        outside = [copy.deepcopy(item) for item in original.get(collection, []) if not inside(item)]
        replacement = [copy.deepcopy(item) for item in candidate.get(collection, []) if inside(item)]
        if collection in original or replacement:
            result[collection] = sorted(outside + replacement, key=lambda item: float(item.get("b", 0)))
    return result


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    difficulty: str
    startBeat: float = Field(ge=0)
    endBeat: float = Field(gt=0)
    baseHash: str = Field(pattern="^[a-f0-9]{64}$")
    seed: int = Field(default=42, ge=0, le=2147483647)
    mappingPlan: dict[str, Any] = Field(default_factory=dict)


def _run_revision(job_id: str) -> None:
    from beatforge import api
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from choreography import generate_all
    from timing_export import project_map_timing, project_plan_timing
    from validate_map import validate_package
    from beatforge.mapping_plan import normalize_mapping_plan
    start_time = time.time()
    status = api._read_status(job_id)
    request = status["revision"]
    folder = api._job_dir(job_id) / "map"
    try:
        if api._cancel_requested(job_id):
            raise InterruptedError("Revision cancelled")
        status.update({"status": "running", "stage": "choreography", "detail": "Composing the selected passage from cached audio analysis"})
        api._write_status(job_id, status)
        analysis = read_json(folder / "_beatforge" / "analysis.json")
        sections = read_json(folder / "_beatforge" / "sections.json")
        plan = normalize_mapping_plan(request["mappingPlan"])
        maps, report = generate_all(analysis, sections, request["seed"], difficulties=[request["difficulty"]], mapping_plan=plan)
        if api._cancel_requested(job_id):
            raise InterruptedError("Revision cancelled")
        info = read_json(folder / "Info.dat")
        ref = next(item for item in chart_refs(info) if item["_difficulty"] == request["difficulty"])
        path = confined(folder, ref["_beatmapFilename"])
        original = read_json(path)
        candidate = project_map_timing(maps[request["difficulty"]], analysis)
        merged = merge_section(original, candidate, request["startBeat"], request["endBeat"])
        path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        provenance_path = folder / "_beatforge" / "provenance.json"
        provenance = read_json(provenance_path) if provenance_path.is_file() else {}
        provenance.update({"revision": {**request, "parentJob": status["revisionOf"], "preservedOutsideSelection": True}, "releaseGate": {}, "humanPlaytests": [], "status": "unreviewed_revision"})
        # A revision never inherits reviews or human evidence for the original artifact.
        for key in ("reviews", "providerReviews", "playtests", "vrPlaytestPassed", "freshSightReadPassed"):
            provenance.pop(key, None)
        provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        plan_path = folder / "_beatforge" / "mapping_plan.json"
        parent_plan = read_json(plan_path) if plan_path.is_file() else {}
        updated_plan = project_plan_timing(report.get("mappingPlan", {"controls": plan, "sections": sections.get("sections", [])}), analysis)
        updated_plan["appliesTo"] = {"difficulty": request["difficulty"], "startBeat": request["startBeat"], "endBeat": request["endBeat"]}
        updated_plan["parentPlan"] = parent_plan
        plan_path.write_text(json.dumps(updated_plan, indent=2), encoding="utf-8")
        (folder / "_beatforge" / "revision_choreography.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        status.update({"status": "validating", "stage": "validating", "detail": "Checking the complete chart, including both edit boundaries"})
        api._write_status(job_id, status)
        qa = validate_package(folder)
        qa_payload = qa.to_dict()
        (folder / "_beatforge" / "qa_report.json").write_text(json.dumps(qa_payload, indent=2), encoding="utf-8")
        if api._cancel_requested(job_id):
            raise InterruptedError("Revision cancelled")
        if qa.errors:
            status.update({"status": "invalid", "localStatus": "invalid", "detail": "The revision failed validation. The original map is unchanged; widen the range or adjust the plan."})
        else:
            package_map(folder, api._job_dir(job_id) / "map.zip")
            status.update({"status": "review_required", "localStatus": "playtest_candidate" if analysis.get("status") == "timing_verified" else "unconfirmed_pack", "detail": "Section revised and validated. Review the preview before installing.", "timingVerified": analysis.get("status") == "timing_verified"})
        status.update({"stage": "ready", "summary": summarize_map(folder), "qa": {"status": qa.status, "errors": len(qa.errors), "warnings": len(qa.warnings)}})
    except InterruptedError:
        status.update({"status": "cancelled", "detail": "Revision cancelled. Original map preserved."})
    except Exception as error:
        status.update({"status": "error", "error": str(error), "detail": f"Revision could not complete: {error}. Original map preserved."})
    status["elapsed"] = round(time.time() - start_time, 2)
    api._write_status(job_id, status)


@router.post("/api/jobs/{job_id}/revise")
def revise_section(job_id: str, request: RevisionRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    from beatforge import api
    from beatforge.mapping_plan import normalize_mapping_plan
    status, source = _map(job_id)
    if status.get("localStatus") not in {"playtest_candidate", "unconfirmed_pack"}:
        raise HTTPException(409, "Start a revision from a completed, structurally valid map")
    if api._qa_report(source)["errors"]:
        raise HTTPException(409, "The source map no longer passes QA")
    if any(path.is_symlink() or getattr(path, 'is_junction', lambda: False)() for path in source.rglob('*')):
        raise HTTPException(422, "Revision source must contain regular files, not linked files")
    if request.endBeat <= request.startBeat:
        raise HTTPException(422, "The end beat must follow the start beat")
    if chart_identity(source) != request.baseHash:
        raise HTTPException(409, "This map changed. Reload the preview before revising it.")
    try:
        plan = normalize_mapping_plan(request.mappingPlan)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    info = read_json(source / "Info.dat")
    ref = next((item for item in chart_refs(info) if item.get("_difficulty") == request.difficulty), None)
    if not ref:
        raise HTTPException(422, "Select a difficulty in this map")
    original = read_json(confined(source, ref["_beatmapFilename"]))
    if original.get("bpmEvents"):
        raise HTTPException(422, "Section revisions require a constant exported BPM clock. Charts with BPM events are preserved for preview only.")
    try:
        merge_section(original, original, request.startBeat, request.endBeat)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    for required in ("analysis.json", "sections.json"):
        if not (source / "_beatforge" / required).is_file():
            raise HTTPException(409, "This imported map has no cached analysis for regeneration")
    analysis = read_json(source / "_beatforge" / "analysis.json")
    if len(analysis.get("beatGrid", [])) < 2:
        raise HTTPException(409, "This map has no adopted beat/sample grid for regeneration")
    if request.endBeat * 60 / float(info["_beatsPerMinute"]) > float(analysis.get("durationSeconds", 0)) + 1e-6:
        raise HTTPException(422, "The selected range extends beyond the song")
    new_id = uuid.uuid4().hex[:12]
    destination = api._job_dir(new_id)
    destination.mkdir(parents=True)
    shutil.copytree(source, destination / "map")
    for path in api._job_dir(job_id).glob("input.*"):
        shutil.copy2(path, destination / path.name)
    revision = request.model_dump()
    revision["mappingPlan"] = plan
    new_status = {"id": new_id, "status": "queued", "title": status.get("title"), "artist": status.get("artist"), "mapper": status.get("mapper", "BeatForge"), "seed": request.seed, "difficulties": status.get("difficulties", []), "startedAt": time.time(), "stages": [], "revisionOf": job_id, "revision": revision, "mappingPlan": plan, "installed": False, "releaseGate": {}}
    api._write_status(new_id, new_status)
    background_tasks.add_task(_run_revision, new_id)
    return {"id": new_id, "status": "queued", "revisionOf": job_id}
