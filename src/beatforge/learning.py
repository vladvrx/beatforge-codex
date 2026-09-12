"""Local, explicit human feedback and preferences; no automatic model training."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from beatforge.feedback_guidance import derive_feedback_adjustment
from beatforge.mapping_plan import normalize_mapping_plan
from beatforge.premium import ROOT, SCRIPTS
from beatforge.preview import chart_identity, chart_refs, confined, read_json

router = APIRouter()
LEARNING_FILE = ROOT / "data" / "learning.json"
LEARNING_LOCK = threading.RLock()
Difficulty = Literal["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
FeedbackTag = Literal["too_dense", "too_sparse", "awkward", "tiring", "repetitive", "off_beat", "good_flow"]
Tester = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Rating = Annotated[int, Field(strict=True, ge=1, le=5)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Ratings(Request):
    overall: Rating | None = None
    flow: Rating | None = None
    readability: Rating | None = None
    musicality: Rating | None = None
    variety: Rating | None = None


class FeedbackRequest(Request):
    jobId: str = Field(min_length=1, max_length=80)
    difficulty: Difficulty
    tester: Tester = "local"
    ratings: Ratings = Field(default_factory=Ratings)
    tags: list[FeedbackTag] = Field(default_factory=list, max_length=7)
    notes: str = Field(default="", max_length=2000)
    startBeat: float | None = Field(default=None, ge=0)
    endBeat: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def meaningful_feedback(self):
        if (self.startBeat is None) != (self.endBeat is None):
            raise ValueError("Provide both startBeat and endBeat for passage feedback")
        if self.startBeat is not None and self.endBeat <= self.startBeat:
            raise ValueError("endBeat must follow startBeat")
        if not self.ratings.model_dump(exclude_none=True) and not self.tags and not self.notes.strip():
            raise ValueError("Add a rating, tag, or note")
        return self


class ComparisonRequest(Request):
    preferredJob: str = Field(min_length=1, max_length=80)
    alternateJob: str = Field(min_length=1, max_length=80)
    difficulty: Difficulty
    tester: Tester = "local"
    notes: str = Field(default="", max_length=2000)


class PresetRequest(Request):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    mappingPlan: dict[str, Any]


class SuggestionsRequest(Request):
    mappingPlan: dict[str, Any] = Field(default_factory=dict)
    tester: Tester = "local"
    difficulty: Difficulty | None = None


class AcceptanceRequest(Request):
    tester: Tester = "local"
    notes: str = Field(default="", max_length=2000)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _empty() -> dict[str, Any]:
    return {"schemaVersion": 1, "feedback": [], "comparisons": [], "presets": [], "acceptedRevisions": []}


def _read_store() -> dict[str, Any]:
    if not LEARNING_FILE.is_file():
        return _empty()
    try:
        data = json.loads(LEARNING_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schemaVersion") != 1:
            raise ValueError("unsupported learning schema")
        for key in ("feedback", "comparisons", "presets", "acceptedRevisions"):
            if not isinstance(data.get(key), list) or any(not isinstance(item, dict) for item in data[key]):
                raise ValueError("invalid learning records")
        return data
    except (OSError, ValueError) as error:
        raise HTTPException(503, "Saved learning data is unreadable or unsupported. The original file has been preserved.") from error


def _write_store(data: dict[str, Any]) -> None:
    LEARNING_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = LEARNING_FILE.with_name(f"learning.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            import os
            os.fsync(handle.fileno())
        temporary.replace(LEARNING_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def _plan(value: dict[str, Any] | None) -> dict[str, Any]:
    try:
        return normalize_mapping_plan(value)
    except (TypeError, ValueError) as error:
        raise HTTPException(422, f"Invalid mapping plan: {error}") from error


def _artifact(job_id: str, difficulty: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind evidence to disk contents, including playback settings for this chart."""
    from beatforge import api
    import soundfile as sf

    status = api._read_status(job_id)
    if status.get("status") in api.ACTIVE_STATES:
        raise HTTPException(409, "Wait for this map to finish before recording feedback")
    folder = api._job_dir(job_id) / "map"
    info = read_json(confined(folder, "Info.dat"))
    entry = next((item for item in chart_refs(info) if item.get("_difficulty") == difficulty), None)
    if entry is None:
        raise HTTPException(422, "This difficulty is not present in the selected map")
    chart = confined(folder, str(entry["_beatmapFilename"]))
    chart_payload = read_json(chart)
    audio = confined(folder, str(info.get("_songFilename", "")))
    playback = {"bpm": info.get("_beatsPerMinute"), "offset": info.get("_songTimeOffset", 0), "difficulty": difficulty, "njs": entry.get("_noteJumpMovementSpeed"), "spawnOffset": entry.get("_noteJumpStartBeatOffset")}
    digest = hashlib.sha256()
    try:
        digest.update(json.dumps(playback, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        digest.update(json.dumps(chart_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    except (TypeError, ValueError) as error:
        raise HTTPException(422, "The selected chart contains invalid playback data") from error
    audio_digest = hashlib.sha256()
    with audio.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            audio_digest.update(chunk)
    try:
        duration = sf.info(audio).duration
        bpm = float(info.get("_beatsPerMinute", 120))
        if not math.isfinite(bpm) or bpm <= 0:
            raise ValueError("Invalid tempo")
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise HTTPException(422, "The selected map has unreadable audio or invalid tempo") from error
    snapshot = {"jobId": job_id, "difficulty": difficulty, "chartHash": digest.hexdigest(), "mapHash": chart_identity(folder), "audioHash": audio_digest.hexdigest(), "durationBeats": duration * bpm / 60, "mappingPlan": _plan(status.get("mappingPlan")), "title": str(info.get("_songName", "Untitled")), "engine": str(status.get("engine") or "unknown")}
    return snapshot, status


@router.get("/api/learning")
def learning_summary() -> dict[str, Any]:
    with LEARNING_LOCK:
        data = _read_store()
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from rl.registry import registry_status

    return {"schemaVersion": 1, "feedbackCount": len(data["feedback"]), "comparisonCount": len(data["comparisons"]), "acceptedRevisionCount": len(data["acceptedRevisions"]), "presets": data["presets"], "recentFeedback": data["feedback"][-20:][::-1], "suggestions": derive_feedback_adjustment(data["feedback"], {}, tester="local"), "modelRegistry": registry_status(LEARNING_FILE.parent / "model_registry")}


@router.post("/api/learning/suggestions")
def learning_suggestions(request: SuggestionsRequest) -> dict[str, Any]:
    plan = _plan(request.mappingPlan)
    with LEARNING_LOCK:
        data = _read_store()
    return derive_feedback_adjustment(data["feedback"], plan, tester=request.tester, difficulty=request.difficulty)


@router.post("/api/feedback")
def record_feedback(request: FeedbackRequest) -> dict[str, Any]:
    snapshot, _ = _artifact(request.jobId, request.difficulty)
    if request.endBeat is not None and request.endBeat > snapshot["durationBeats"] + 1e-6:
        raise HTTPException(422, "The feedback passage extends beyond the song")
    record = {**snapshot, "schemaVersion": 1, "id": uuid.uuid4().hex[:12], "source": "human", "createdAt": _now(), "tester": request.tester, "ratings": request.ratings.model_dump(exclude_none=True), "tags": sorted(set(request.tags)), "notes": request.notes, "startBeat": request.startBeat, "endBeat": request.endBeat}
    with LEARNING_LOCK:
        data = _read_store()
        data["feedback"].append(record)
        _write_store(data)
    return {"feedback": record, "feedbackCount": len(data["feedback"])}


@router.post("/api/comparisons")
def record_comparison(request: ComparisonRequest) -> dict[str, Any]:
    preferred, _ = _artifact(request.preferredJob, request.difficulty)
    alternate, _ = _artifact(request.alternateJob, request.difficulty)
    if preferred["audioHash"] != alternate["audioHash"]:
        raise HTTPException(409, "Compare maps made from the exact same audio file")
    if preferred["chartHash"] == alternate["chartHash"]:
        raise HTTPException(409, "The selected difficulty charts are identical")
    pair = sorted((preferred["chartHash"], alternate["chartHash"]))
    identity = hashlib.sha256(json.dumps([preferred["audioHash"], request.difficulty, request.tester.casefold(), pair]).encode()).hexdigest()
    record = {"schemaVersion": 1, "id": identity[:20], "source": "human", "createdAt": _now(), "tester": request.tester, "difficulty": request.difficulty, "audioHash": preferred["audioHash"], "chartPair": pair, "preferredJob": request.preferredJob, "alternateJob": request.alternateJob, "preferredChartHash": preferred["chartHash"], "alternateChartHash": alternate["chartHash"], "preferredMappingPlan": preferred["mappingPlan"], "alternateMappingPlan": alternate["mappingPlan"], "notes": request.notes}
    with LEARNING_LOCK:
        data = _read_store()
        replaced = any(item.get("id") == record["id"] for item in data["comparisons"])
        data["comparisons"] = [item for item in data["comparisons"] if item.get("id") != record["id"]] + [record]
        _write_store(data)
    return {"comparison": record, "comparisonCount": len(data["comparisons"]), "replaced": replaced}


@router.post("/api/presets")
def save_preset(request: PresetRequest) -> dict[str, Any]:
    preset = {"id": uuid.uuid4().hex[:12], "name": request.name, "mappingPlan": _plan(request.mappingPlan), "createdAt": _now()}
    with LEARNING_LOCK:
        data = _read_store()
        data["presets"].append(preset)
        _write_store(data)
    return preset


def record_accepted_revision(job_id: str, tester: str = "local", notes: str = "") -> dict[str, Any]:
    from beatforge import api

    request = AcceptanceRequest(tester=tester, notes=notes)
    status = api._read_status(job_id)
    revision = status.get("revision")
    if not status.get("revisionOf") or not isinstance(revision, dict):
        raise HTTPException(409, "Only a generated section revision can be accepted")
    if status.get("localStatus") not in {"playtest_candidate", "unconfirmed_pack"} or api._qa_report(api._job_dir(job_id) / "map")["errors"]:
        raise HTTPException(409, "Resolve the revision's structural validation failures first")
    snapshot, _ = _artifact(job_id, str(revision.get("difficulty", "")))
    record = {**snapshot, "schemaVersion": 1, "id": uuid.uuid4().hex[:12], "source": "human", "createdAt": _now(), "tester": request.tester, "notes": request.notes, "parentJob": status["revisionOf"], "revision": {key: revision.get(key) for key in ("difficulty", "startBeat", "endBeat", "seed", "baseHash")}, "acceptance": "preferred_section_revision"}
    with LEARNING_LOCK:
        data = _read_store()
        data["acceptedRevisions"] = [item for item in data["acceptedRevisions"] if not (item.get("chartHash") == record["chartHash"] and item.get("tester", "").casefold() == request.tester.casefold())] + [record]
        _write_store(data)
    return record


@router.post("/api/jobs/{job_id}/accept-revision")
def accept_revision(job_id: str, request: AcceptanceRequest) -> dict[str, Any]:
    return {"acceptedRevision": record_accepted_revision(job_id, request.tester, request.notes)}


@router.get("/api/learning/export")
def export_learning() -> dict[str, Any]:
    with LEARNING_LOCK:
        data = _read_store()
    return {"schemaVersion": 1, "kind": "beatforge-human-learning", "exportedAt": _now(), "containsAudio": False, "containsOfficialCorpus": False, "feedback": data["feedback"], "preferences": data["comparisons"], "presets": data["presets"], "acceptedRevisions": data["acceptedRevisions"]}
