"""Local BeatForge studio server."""

from __future__ import annotations

import json
import shutil
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from beatforge.install import find_custom_levels, install_generated_map_elevated, install_map, launch_beat_saber
from beatforge.premium import ROOT, SKILL, package_map, run_premium_pipeline, summarize_map
from beatforge.release_route import ai_release_route_enabled
from beatforge.providers import (
    PROVIDERS,
    delete_secret,
    discover_models,
    get_secret,
    prepare_review_bundle,
    run_review,
    runner_status,
    set_secret,
    suggest_mood_palette,
    suggest_timing_anchors,
)


WEB_INDEX = ROOT / "web" / "index.html"
JOBS_DIR = ROOT / "data" / "jobs"
IMPORTS_DIR = ROOT / "data" / "imports"
SETTINGS_FILE = ROOT / "data" / "provider-settings.json"
JOB_TTL_SECONDS = 24 * 3600
STUDIO_STATES = (
    "needs_anchors",
    "needs_palette",
    "corpus_incomplete",
    "validating",
    "review_required",
    "playtest_candidate",
    "studio_reviewed",
    "release_candidate",
    "invalid",
    "error",
)
TERMINAL_STATES = {state for state in STUDIO_STATES if state != "validating"}
STUDIO_DIFFICULTIES = ("Easy", "Normal", "Hard", "Expert", "ExpertPlus")
DIFFICULTY_ALIASES = {
    "easy": "Easy",
    "normal": "Normal",
    "hard": "Hard",
    "expert": "Expert",
    "expertplus": "ExpertPlus",
    "expert+": "ExpertPlus",
}


def parse_studio_difficulties(raw: str | None) -> list[str]:
    if raw is None:
        return list(STUDIO_DIFFICULTIES)
    tokens = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    if not tokens:
        raise HTTPException(400, "Select at least one difficulty")
    selected: list[str] = []
    for token in tokens:
        name = DIFFICULTY_ALIASES.get(token.casefold())
        if name is None:
            raise HTTPException(400, f"Unknown difficulty {token}")
        if name not in selected:
            selected.append(name)
    return [name for name in STUDIO_DIFFICULTIES if name in selected]

app = FastAPI(title="BeatForge Studio", version="0.2.0")
app.mount("/assets", StaticFiles(directory=ROOT / "web" / "assets"), name="assets")
app.mount("/web", StaticFiles(directory=ROOT / "web"), name="web")


class PaletteApproval(BaseModel):
    approved: bool
    artwork_approved: bool = Field(alias="artworkApproved")
    note: str = ""


class PaletteSuggestion(BaseModel):
    metadata_approved: bool = Field(alias="metadataApproved")


class TimingSuggestion(BaseModel):
    analysis_approved: bool = Field(alias="analysisApproved")


class ProviderSettings(BaseModel):
    api_key: str | None = Field(default=None, alias="apiKey")
    model: str
    maximum_cost_usd: float = Field(default=5.0, ge=0.01, le=500.0, alias="maximumCostUsd")


class ReviewPrepare(BaseModel):
    model: str | None = None
    maximum_cost_usd: float | None = Field(default=None, ge=0.01, le=500.0, alias="maximumCostUsd")


class ReviewAuthorization(BaseModel):
    consent_token: str = Field(alias="consentToken")
    bundle_hash: str = Field(alias="bundleHash")
    rights_attested: bool = Field(alias="rightsAttested")
    maximum_cost_usd: float = Field(ge=0.01, le=500.0, alias="maximumCostUsd")


class PlaytestEvidence(BaseModel):
    difficulty: str
    speed: str
    passed: bool
    tester: str = Field(min_length=1, max_length=120)
    fresh_sight_read: bool = Field(default=False, alias="freshSightRead")
    notes: str = Field(default="", max_length=2000)


class ImportInstalledMap(BaseModel):
    folder: str = Field(min_length=1, max_length=200)
    source: str = "customLevels"


_IMPORT_SKIP_NAMES = {".env", "credentials.json", "secret.key", "private-corpus.sqlite3"}


def _ignore_imported_secrets(directory: str, names: list[str]) -> set[str]:
    skipped = set()
    for name in names:
        lower = name.casefold()
        if name in _IMPORT_SKIP_NAMES or lower.endswith(".sqlite3") or lower.endswith(".sqlite"):
            skipped.add(name)
    return skipped


def _import_roots() -> dict[str, Path]:
    roots: dict[str, Path] = {}
    custom = find_custom_levels()
    if custom is not None:
        roots["customLevels"] = custom.resolve()
    roots["imports"] = IMPORTS_DIR.resolve()
    return roots


def _installed_map_dir(folder: str, source: str = "customLevels") -> Path:
    if Path(folder).name != folder or folder in {".", ".."}:
        raise HTTPException(400, "folder must be a single directory name")
    if source not in {"customLevels", "imports"}:
        raise HTTPException(400, "source must be customLevels or imports")
    roots = _import_roots()
    root = roots.get(source)
    if root is None:
        raise HTTPException(404, "Beat Saber CustomLevels folder not found")
    candidate = (root / folder).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise HTTPException(400, f"folder is outside {source}") from error
    if not candidate.is_dir():
        raise HTTPException(404, "Installed map folder not found")
    return candidate


def _scan_playtest_maps(root: Path, source: str) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    maps: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if not child.is_dir():
            continue
        info_path = child / "Info.dat"
        qa_path = child / "_beatforge" / "qa_report.json"
        if not info_path.is_file() or not qa_path.is_file():
            continue
        try:
            info = json.loads(info_path.read_text(encoding="utf-8-sig"))
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        errors = qa.get("errors") or []
        maps.append(
            {
                "folder": child.name,
                "source": source,
                "title": info.get("_songName") or child.name,
                "artist": info.get("_songAuthorName") or "",
                "qaStatus": qa.get("status"),
                "hardFailures": len(errors) if isinstance(errors, list) else 1,
            }
        )
    return maps


def _installed_playtest_maps() -> list[dict[str, Any]]:
    maps: list[dict[str, Any]] = []
    for source, root in _import_roots().items():
        maps.extend(_scan_playtest_maps(root, source))
    return maps


def _cleanup_old_jobs(max_age: float = JOB_TTL_SECONDS) -> int:
    if not JOBS_DIR.is_dir():
        return 0
    now = time.time()
    removed = 0
    for directory in JOBS_DIR.iterdir():
        if not directory.is_dir():
            continue
        try:
            if now - directory.stat().st_mtime > max_age:
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


@app.on_event("startup")
def _on_startup() -> None:
    _cleanup_old_jobs()


def _job_dir(job_id: str) -> Path:
    if not job_id.replace("-", "").isalnum():
        raise HTTPException(400, "Invalid job id")
    return JOBS_DIR / job_id


def _read_status(job_id: str) -> dict[str, Any]:
    path = _job_dir(job_id) / "status.json"
    if not path.is_file():
        raise HTTPException(404, "Job not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_status(job_id: str, data: dict[str, Any]) -> None:
    directory = _job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "status.json.tmp"
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(directory / "status.json")


def _audio_path(job_id: str) -> Path:
    matches = sorted(_job_dir(job_id).glob("input.*"))
    if not matches:
        raise HTTPException(404, "Uploaded audio is missing")
    return matches[0]


def _append_stage(data: dict[str, Any], stage: str, detail: str) -> dict[str, Any]:
    stages = list(data.get("stages", []))
    stages.append({"stage": stage, "detail": detail, "at": time.time()})
    return {**data, "stage": stage, "detail": detail, "stages": stages}


def _install_pack_to_custom_levels(job_id: str, data: dict[str, Any]) -> Path:
    zip_path = _job_dir(job_id) / "map.zip"
    if not zip_path.is_file():
        raise FileNotFoundError("Generated map zip is missing")
    title = str(data.get("title") or "BeatForge")
    try:
        return install_map(zip_path, title=title)
    except PermissionError:
        return install_generated_map_elevated(job_id)


def _run_job(job_id: str) -> None:
    initial = _read_status(job_id)
    start = time.time()
    initial = {**initial, "startedAt": initial.get("startedAt") or start, "elapsed": 0}
    _write_status(job_id, initial)

    def progress(stage: str, detail: str, percent: float | None = None, log: bool = True) -> None:
        current = _read_status(job_id)
        if log:
            current = _append_stage(current, stage, detail)
        else:
            current = {**current, "stage": stage, "detail": detail}
        current.update(
            {
                "status": "validating" if stage == "validating" else "running",
                "elapsed": round(time.time() - start, 2),
                "startedAt": current.get("startedAt") or start,
            }
        )
        if percent is not None:
            current["decodePercent"] = round(float(percent), 1)
        _write_status(job_id, current)

    try:
        job_dir = _job_dir(job_id)
        map_dir = job_dir / "map"
        anchors = job_dir / "anchors.json"
        palette = job_dir / "approved_palette.json"
        result = run_premium_pipeline(
            audio=_audio_path(job_id),
            output=map_dir,
            title=str(initial.get("title") or _audio_path(job_id).stem),
            artist=str(initial.get("artist") or "Unknown Artist"),
            mapper=str(initial.get("mapper") or "BeatForge"),
            seed=int(initial.get("seed", 42)),
            anchors=anchors if anchors.is_file() else None,
            palette=palette if palette.is_file() else None,
            progress=progress,
            allow_unconfirmed=True,
            difficulties=list(initial.get("difficulties") or STUDIO_DIFFICULTIES),
        )
        status = result["status"]
        map_ready = (map_dir / "Info.dat").is_file()
        if map_ready and status in {"playtest_candidate", "invalid"}:
            progress("validating", "checking schema, timing, same-color flow, collisions, hazards, and packaging")
            package_map(map_dir, job_dir / "map.zip")
            summary = summarize_map(map_dir)
            qa_path = map_dir / "_beatforge" / "qa_report.json"
            qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.is_file() else {"status": "invalid"}
            qa_failed = bool(qa.get("errors"))
            analysis_path = map_dir / "_beatforge" / "analysis.json"
            timing_verified = False
            if analysis_path.is_file():
                try:
                    timing_verified = json.loads(analysis_path.read_text(encoding="utf-8")).get("status") == "timing_verified"
                except (OSError, json.JSONDecodeError):
                    timing_verified = False
            unconfirmed = not timing_verified
            if qa_failed:
                status = "invalid"
                local_status = "invalid"
                detail = "local hard gates failed"
            elif unconfirmed:
                status = "review_required"
                local_status = "unconfirmed_pack"
                detail = "pack is ready to download; timing is unverified. AI timing buttons can check the grid."
            else:
                status = "review_required"
                local_status = "playtest_candidate"
                detail = "local hard gates passed; AI timing review is optional"
            current = _append_stage(_read_status(job_id), "ready", detail)
            current.update(
                {
                    "status": status,
                    "localStatus": local_status,
                    "summary": summary,
                    "qa": {"status": qa.get("status"), "errors": len(qa.get("errors", [])), "warnings": len(qa.get("warnings", []))},
                    "elapsed": round(time.time() - start, 2),
                    "continueUnconfirmed": unconfirmed,
                    "timingVerified": timing_verified,
                }
            )
            try:
                destination = _install_pack_to_custom_levels(job_id, current)
            except Exception as error:
                current = _append_stage(
                    current,
                    "install",
                    f"CustomLevels install failed: {type(error).__name__}: {error}",
                )
                current["installError"] = str(error)
            else:
                current = _append_stage(
                    current,
                    "install",
                    f"copied into Beat Saber CustomLevels at {destination}",
                )
                current["customLevelsPath"] = str(destination)
                current["installed"] = True
            current["elapsed"] = round(time.time() - start, 2)
            _write_status(job_id, current)
            return
        current = _append_stage(_read_status(job_id), status, str(result.get("payload", {}).get("message") or status.replace("_", " ")))
        current.update({"status": status, "pipeline": result.get("payload", {}), "elapsed": round(time.time() - start, 2)})
        if status in {"error", "invalid", "corpus_incomplete"}:
            current["error"] = (result.get("stderr") or result.get("stdout") or "pipeline failed")[-4000:]
        _write_status(job_id, current)
    except Exception as error:
        _write_status(job_id, {**initial, "status": "error", "error": str(error), "trace": traceback.format_exc(), "elapsed": round(time.time() - start, 2)})


def _settings() -> dict[str, Any]:
    if not SETTINGS_FILE.is_file():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_settings(value: dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _provider_settings(provider: str) -> dict[str, Any]:
    metadata = _settings().get(provider, {})
    return {
        "model": metadata.get("model", PROVIDERS[provider]["defaultModel"]),
        "maximumCostUsd": float(metadata.get("maximumCostUsd", 5.0)),
    }


def _review_passes(job_dir: Path) -> tuple[bool, list[dict[str, Any]]]:
    reports = []
    for provider in PROVIDERS:
        path = job_dir / f"review-{provider}-report.json"
        if path.is_file():
            reports.append(json.loads(path.read_text(encoding="utf-8")))
    return any(report.get("verdict") == "pass" and not report.get("findings") for report in reports), reports


def _playtest_gate(job_dir: Path, required: list[str] | None = None) -> dict[str, Any]:
    path = job_dir / "playtests.json"
    evidence = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    passing = [item for item in evidence if item.get("passed")]
    full = {item.get("difficulty") for item in passing if item.get("speed") == "full"}
    slow = {item.get("difficulty") for item in passing if item.get("speed") == "slow"}
    fresh = [item for item in passing if item.get("freshSightRead")]
    primary_testers = {item.get("tester") for item in passing if not item.get("freshSightRead")}
    required_difficulties = set(required or STUDIO_DIFFICULTIES)
    expert_slow = {name for name in ("Expert", "ExpertPlus") if name in required_difficulties}
    return {
        "allFiveFullSpeed": required_difficulties.issubset(full),
        "expertSlow": expert_slow.issubset(slow),
        "separateFreshSightRead": any(item.get("tester") not in primary_testers for item in fresh),
        "evidenceCount": len(evidence),
    }


def _provenance_path(job_dir: Path) -> Path:
    return job_dir / "map" / "_beatforge" / "provenance.json"


def _sync_playtest_provenance(job_dir: Path, playtests: dict[str, Any]) -> dict[str, Any]:
    path = _provenance_path(job_dir)
    if not path.is_file():
        return {
            "fullSpeedVrPlaytest": False,
            "slowVrPlaytest": False,
            "freshSightRead": False,
            "structuralInspection": False,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    gate = payload.setdefault("releaseGate", {})
    gate["fullSpeedVrPlaytest"] = bool(playtests.get("allFiveFullSpeed"))
    gate["slowVrPlaytest"] = bool(playtests.get("expertSlow"))
    gate["freshSightRead"] = bool(playtests.get("separateFreshSightRead"))
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return gate


def _update_release_status(job_id: str) -> dict[str, Any]:
    job_dir = _job_dir(job_id)
    status = _read_status(job_id)
    review_passed, reports = _review_passes(job_dir)
    playtests = _playtest_gate(job_dir, list(status.get("difficulties") or STUDIO_DIFFICULTIES))
    provenance_gate = _sync_playtest_provenance(job_dir, playtests)
    local_passed = status.get("localStatus") == "playtest_candidate"
    no_external_findings = bool(reports) and all(not report.get("findings") for report in reports)
    if local_passed and review_passed and no_external_findings:
        status["status"] = "studio_reviewed"
    headset = bool(provenance_gate.get("fullSpeedVrPlaytest")) and bool(provenance_gate.get("freshSightRead"))
    ai_route = ai_release_route_enabled()
    if (
        ai_route
        and local_passed
        and review_passed
        and no_external_findings
        and headset
        and all(playtests[key] for key in ("allFiveFullSpeed", "expertSlow", "separateFreshSightRead"))
    ):
        status["status"] = "release_candidate"
    elif status.get("status") == "release_candidate":
        status["status"] = "studio_reviewed" if local_passed and review_passed and no_external_findings else "playtest_candidate"
    status["releaseGate"] = {
        "localHardGates": local_passed,
        "providerReview": review_passed,
        "noExternalFindings": no_external_findings,
        "aiReleaseRoute": ai_route,
        **playtests,
        "provenance": provenance_gate,
    }
    _write_status(job_id, status)
    return status


@app.get("/")
def index() -> FileResponse:
    if not WEB_INDEX.is_file():
        raise HTTPException(404, "web/index.html missing")
    return FileResponse(WEB_INDEX, media_type="text/html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "pipeline": "official-premium"}


@app.post("/api/generate")
async def generate(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    title: str = Form(""),
    artist: str = Form(""),
    mapper: str = Form("BeatForge"),
    seed: int = Form(42),
    difficulties: str | None = Form(None),
) -> JSONResponse:
    suffix = Path(audio.filename or "song.mp3").suffix.casefold() or ".mp3"
    if suffix not in {".mp3", ".wav", ".ogg", ".egg", ".flac", ".m4a", ".mp4"}:
        raise HTTPException(400, "Upload MP3, WAV, OGG, FLAC, M4A, or MP4 audio")
    chosen = parse_studio_difficulties(difficulties)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_jobs()
    job_id = uuid.uuid4().hex[:12]
    destination = _job_dir(job_id)
    destination.mkdir(parents=True)
    (destination / f"input{suffix}").write_bytes(await audio.read())
    status = {
        "id": job_id,
        "status": "queued",
        "title": title or Path(audio.filename or "song").stem,
        "artist": artist or "Unknown Artist",
        "mapper": mapper,
        "seed": seed,
        "profile": "official-premium",
        "difficulties": chosen,
        "stages": [],
        "startedAt": time.time(),
        "elapsed": 0,
        "decodePercent": None,
    }
    _write_status(job_id, status)
    background_tasks.add_task(_run_job, job_id)
    return JSONResponse({"id": job_id, "status": "queued"})


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    data = _read_status(job_id)
    if data.get("localStatus") in {"playtest_candidate", "studio_reviewed"} or data.get("status") in {
        "playtest_candidate",
        "studio_reviewed",
        "review_required",
        "release_candidate",
    }:
        data = _update_release_status(job_id)
    data.pop("trace", None)
    if data.get("startedAt") and data.get("status") in {"queued", "running", "validating"}:
        data["elapsed"] = round(time.time() - float(data["startedAt"]), 2)
    return data


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str) -> StreamingResponse:
    _read_status(job_id)

    def stream() -> Any:
        previous = ""
        deadline = time.time() + 3600
        while time.time() < deadline:
            data = _read_status(job_id)
            if data.get("startedAt") and data.get("status") in {"queued", "running", "validating"}:
                data = {**data, "elapsed": round(time.time() - float(data["startedAt"]), 2)}
            encoded = json.dumps(data, separators=(",", ":"))
            if encoded != previous:
                yield f"event: progress\ndata: {encoded}\n\n"
                previous = encoded
            if data.get("status") in TERMINAL_STATES:
                yield "event: end\ndata: {}\n\n"
                break
            time.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/jobs/{job_id}/anchors")
def job_anchors(job_id: str, payload: dict[str, Any], background_tasks: BackgroundTasks) -> dict[str, Any]:
    status = _read_status(job_id)
    if status.get("status") != "needs_anchors":
        raise HTTPException(409, "This job is not waiting for timing anchors")
    anchors = payload.get("anchors")
    if not isinstance(anchors, list) or len(anchors) < 2:
        raise HTTPException(400, "At least two anchors are required")
    last_beat = last_sample = None
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict) or "beat" not in anchor or not ({"sample", "timeSeconds"} & set(anchor)):
            raise HTTPException(400, f"Anchor {index} requires beat and sample or timeSeconds")
        beat = float(anchor["beat"])
        sample = int(anchor.get("sample", round(float(anchor["timeSeconds"]) * 44100)))
        if last_beat is not None and (beat <= last_beat or sample <= int(last_sample)):
            raise HTTPException(400, "Anchors must increase strictly in beat and sample")
        last_beat, last_sample = beat, sample
    (_job_dir(job_id) / "anchors.json").write_text(json.dumps({"anchors": anchors}, indent=2), encoding="utf-8")
    status["status"] = "queued"
    status = _append_stage(status, "anchors", "confirmed anchors saved; refitting the beat grid")
    _write_status(job_id, status)
    background_tasks.add_task(_run_job, job_id)
    return {"id": job_id, "status": "queued"}


@app.post("/api/jobs/{job_id}/continue-unconfirmed")
def job_continue_unconfirmed(job_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    status = _read_status(job_id)
    if status.get("status") != "needs_anchors":
        raise HTTPException(409, "Download anyway is only available while timing anchors are required")
    status["continueUnconfirmed"] = True
    status["status"] = "queued"
    status = _append_stage(
        status,
        "unconfirmed",
        "generating from the unconfirmed beat grid for download; timing stays unverified",
    )
    _write_status(job_id, status)
    background_tasks.add_task(_run_job, job_id)
    return {"id": job_id, "status": "queued", "continueUnconfirmed": True}


@app.post("/api/jobs/{job_id}/anchors/suggest/{provider}")
def job_anchors_suggest(job_id: str, provider: str, request: TimingSuggestion, background_tasks: BackgroundTasks) -> dict[str, Any]:
    status = _read_status(job_id)
    if status.get("status") != "needs_anchors":
        raise HTTPException(409, "This job is not waiting for timing anchors")
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    settings = _provider_settings(provider)
    if not get_secret(provider):
        raise HTTPException(409, f"Configure {PROVIDERS[provider]['label']} before requesting timing anchors")
    if not runner_status(provider).get("runnerInstalled"):
        raise HTTPException(409, "The OpenAI Codex CLI is not installed")
    try:
        proposal = suggest_timing_anchors(
            job_dir=_job_dir(job_id),
            provider=provider,
            model=str(settings["model"]),
            analysis_approved=request.analysis_approved,
        )
    except (ValueError, RuntimeError) as error:
        raise HTTPException(409, str(error)) from error
    (_job_dir(job_id) / "anchors.json").write_text(json.dumps({"anchors": proposal["anchors"]}, indent=2), encoding="utf-8")
    status["status"] = "queued"
    status = _append_stage(
        status,
        "anchors",
        f"{PROVIDERS[provider]['label']} proposed sample anchors from the analysis summary; song audio was not sent; refitting the beat grid",
    )
    _write_status(job_id, status)
    background_tasks.add_task(_run_job, job_id)
    return {"id": job_id, "status": "queued", "provider": provider, "audioTransferred": False, "anchors": proposal["anchors"]}


@app.post("/api/jobs/{job_id}/palette/approve")
def job_palette_approve(job_id: str, approval: PaletteApproval, background_tasks: BackgroundTasks) -> dict[str, Any]:
    status = _read_status(job_id)
    if status.get("status") != "needs_palette":
        raise HTTPException(409, "This job is not waiting for palette approval")
    candidate_path = _job_dir(job_id) / "map" / "_beatforge" / "palette_candidate.json"
    if not candidate_path.is_file():
        raise HTTPException(409, "No cover-derived palette is available; configure an AI provider or choose artwork first")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if not approval.approved or not approval.artwork_approved:
        raise HTTPException(409, "Artwork and palette approval are both required")
    if candidate.get("palette", {}).get("status") != "needs_approval":
        raise HTTPException(409, "The proposed palette does not meet the readability thresholds")
    approved = {**candidate, "status": "approved", "approvedAt": time.time(), "approval": {"artworkApproved": True, "paletteApproved": True, "note": approval.note}}
    (_job_dir(job_id) / "approved_palette.json").write_text(json.dumps(approved, indent=2), encoding="utf-8")
    status["status"] = "queued"
    status = _append_stage(status, "palette", "artwork and readable colors approved; generating all five difficulties")
    _write_status(job_id, status)
    background_tasks.add_task(_run_job, job_id)
    return {"id": job_id, "status": "queued"}


@app.post("/api/jobs/{job_id}/palette/suggest/{provider}")
def job_palette_suggest(job_id: str, provider: str, request: PaletteSuggestion) -> dict[str, Any]:
    status = _read_status(job_id)
    if status.get("status") != "needs_palette":
        raise HTTPException(409, "This job is not waiting for a palette")
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    settings = _provider_settings(provider)
    if not get_secret(provider):
        raise HTTPException(409, f"Configure {PROVIDERS[provider]['label']} before requesting a mood palette")
    if not runner_status(provider).get("runnerInstalled"):
        raise HTTPException(409, "The OpenAI Codex CLI is not installed")
    try:
        candidate = suggest_mood_palette(
            job_dir=_job_dir(job_id),
            provider=provider,
            model=str(settings["model"]),
            metadata_approved=request.metadata_approved,
        )
    except (ValueError, RuntimeError) as error:
        raise HTTPException(409, str(error)) from error
    artwork = candidate.get("artwork", {})
    artwork.pop("path", None)
    artwork["previewUrl"] = f"/api/jobs/{job_id}/palette/artwork"
    status = _append_stage(status, "palette", f"{PROVIDERS[provider]['label']} proposed a readable mood palette; approval is still required")
    _write_status(job_id, status)
    return candidate


@app.get("/api/jobs/{job_id}/palette")
def job_palette(job_id: str) -> Any:
    _read_status(job_id)
    path = _job_dir(job_id) / "map" / "_beatforge" / "palette_candidate.json"
    if not path.is_file():
        raise HTTPException(404, "Palette candidate is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    artwork = payload.get("artwork", {})
    if artwork.get("path"):
        artwork["previewUrl"] = f"/api/jobs/{job_id}/palette/artwork"
        artwork.pop("path", None)
    return payload


@app.get("/api/jobs/{job_id}/palette/artwork")
def job_palette_artwork(job_id: str) -> FileResponse:
    path = _job_dir(job_id) / "map" / "_beatforge" / "palette_candidate.json"
    if not path.is_file():
        raise HTTPException(404, "Palette candidate is missing")
    candidate = json.loads(path.read_text(encoding="utf-8"))
    artwork_path = Path(str(candidate.get("artwork", {}).get("path", "")))
    if not artwork_path.is_file():
        raise HTTPException(404, "Artwork preview is missing")
    return FileResponse(artwork_path)


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str) -> FileResponse:
    data = _read_status(job_id)
    if data.get("localStatus") not in {"playtest_candidate", "unconfirmed_pack"}:
        raise HTTPException(409, "Map package is not ready to download")
    path = _job_dir(job_id) / "map.zip"
    if not path.is_file():
        raise HTTPException(404, "Map package is missing")
    safe = "".join(character if character.isalnum() or character in "-_. " else "_" for character in f"{data.get('title') or 'BeatForge'}.zip")
    return FileResponse(path, filename=safe, media_type="application/zip")


@app.get("/api/jobs/{job_id}/critic")
def job_critic(job_id: str) -> Any:
    _read_status(job_id)
    path = _job_dir(job_id) / "map" / "_beatforge" / "qa_report.json"
    if not path.is_file():
        raise HTTPException(404, "QA report is missing")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/chart")
def job_chart(job_id: str) -> Any:
    data = _read_status(job_id)
    if data.get("localStatus") != "playtest_candidate":
        raise HTTPException(409, "Map has not passed local hard gates")
    return summarize_map(_job_dir(job_id) / "map")


@app.get("/api/beat-saber/status")
def beat_saber_status() -> dict[str, Any]:
    found = find_custom_levels()
    return {"found": found is not None, "path": str(found) if found else None}


@app.get("/api/beat-saber/playtest-maps")
def beat_saber_playtest_maps() -> dict[str, Any]:
    maps = _installed_playtest_maps()
    return {"found": find_custom_levels() is not None, "maps": maps}


@app.post("/api/jobs/import")
def import_installed_map(payload: ImportInstalledMap) -> dict[str, Any]:
    source_dir = _installed_map_dir(payload.folder, payload.source)
    info_path = source_dir / "Info.dat"
    qa_path = source_dir / "_beatforge" / "qa_report.json"
    if not info_path.is_file() or not qa_path.is_file():
        raise HTTPException(409, "Only BeatForge-generated folders with a QA report can be attached")
    try:
        info = json.loads(info_path.read_text(encoding="utf-8-sig"))
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(409, f"Installed map metadata is unreadable: {error}") from error
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_jobs()
    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    map_dir = job_dir / "map"
    shutil.copytree(source_dir, map_dir, ignore=_ignore_imported_secrets)
    package_map(map_dir, job_dir / "map.zip")
    errors = qa.get("errors") or []
    local_ok = not errors
    status_name = "review_required" if local_ok else "invalid"
    status = {
        "id": job_id,
        "status": status_name,
        "localStatus": "playtest_candidate" if local_ok else "invalid",
        "title": info.get("_songName") or payload.folder,
        "artist": info.get("_songAuthorName") or "Unknown Artist",
        "mapper": info.get("_levelAuthorName") or "BeatForge",
        "profile": "official-premium",
        "imported": True,
        "importSource": payload.source,
        "customLevelsPath": str(source_dir) if payload.source == "customLevels" else None,
        "sourcePath": str(source_dir),
        "difficulties": ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"],
        "summary": summarize_map(map_dir),
        "qa": {"status": qa.get("status"), "errors": len(errors) if isinstance(errors, list) else 1, "warnings": len(qa.get("warnings") or [])},
        "stages": [
            {
                "stage": "import",
                "detail": f"attached installed map {payload.folder} for headset evidence and review; software did not play it",
                "at": time.time(),
            }
        ],
    }
    _write_status(job_id, status)
    return _update_release_status(job_id) if local_ok else status


@app.post("/api/jobs/{job_id}/install")
def job_install(job_id: str) -> dict[str, Any]:
    data = _read_status(job_id)
    if data.get("localStatus") not in {"playtest_candidate", "unconfirmed_pack"}:
        raise HTTPException(409, "Only a packaged playtest or unconfirmed pack can be installed")
    try:
        destination = _install_pack_to_custom_levels(job_id, data)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except (OSError, RuntimeError, TimeoutError, ValueError, zipfile.BadZipFile) as error:
        detail = str(error)
        code = 403 if "administrator" in detail.casefold() or "cancelled" in detail.casefold() else 500
        raise HTTPException(code, f"Install failed: {type(error).__name__}: {error}") from error
    except Exception as error:
        raise HTTPException(500, f"Install failed: {type(error).__name__}: {error}") from error
    data["customLevelsPath"] = str(destination)
    data["installed"] = True
    _write_status(job_id, _append_stage(data, "install", f"copied into Beat Saber CustomLevels at {destination}"))
    return {"installed": True, "path": str(destination), "status": data.get("localStatus")}


@app.post("/api/jobs/{job_id}/launch")
def job_launch(job_id: str) -> dict[str, Any]:
    data = _read_status(job_id)
    if data.get("localStatus") not in {"playtest_candidate", "studio_reviewed"}:
        raise HTTPException(409, "Launch Beat Saber only after local hard gates pass")
    try:
        result = launch_beat_saber()
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except OSError as error:
        raise HTTPException(500, f"Launch failed: {type(error).__name__}: {error}") from error
    return {**result, "jobStatus": data.get("status"), "playedBySoftware": False}


@app.get("/api/settings/providers/{provider}")
def provider_get(provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    metadata = _settings().get(provider, {})
    return {**runner_status(provider), "model": metadata.get("model", PROVIDERS[provider]["defaultModel"]), "maximumCostUsd": metadata.get("maximumCostUsd", 5.0), "models": metadata.get("models", [])}


@app.put("/api/settings/providers/{provider}")
def provider_put(provider: str, payload: ProviderSettings) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    if payload.api_key:
        try:
            set_secret(provider, payload.api_key)
        except (RuntimeError, ValueError) as error:
            raise HTTPException(503, str(error)) from error
    try:
        secret = payload.api_key.strip() if payload.api_key else get_secret(provider)
        if not secret:
            raise ValueError("API key is required for model discovery")
        models = discover_models(provider, secret)
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(503, str(error)) from error
    aliases = {"codex": set()}[provider]
    if payload.model not in models and payload.model not in aliases:
        raise HTTPException(409, f"Configured model is unavailable: {payload.model}")
    settings = _settings()
    settings[provider] = {"model": payload.model, "maximumCostUsd": payload.maximum_cost_usd, "models": models}
    _write_settings(settings)
    return provider_get(provider)


@app.delete("/api/settings/providers/{provider}")
def provider_delete(provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    try:
        delete_secret(provider)
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
    settings = _settings()
    settings.pop(provider, None)
    _write_settings(settings)
    return {"provider": provider, "configured": False}


@app.post("/api/jobs/{job_id}/reviews/{provider}/prepare")
def review_prepare(job_id: str, provider: str, payload: ReviewPrepare) -> dict[str, Any]:
    data = _read_status(job_id)
    if data.get("localStatus") != "playtest_candidate":
        raise HTTPException(409, "Local hard gates must pass before external review")
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    settings = _settings().get(provider, {})
    model = payload.model or settings.get("model") or PROVIDERS[provider]["defaultModel"]
    maximum = payload.maximum_cost_usd or float(settings.get("maximumCostUsd", 5.0))
    try:
        return prepare_review_bundle(job_dir=_job_dir(job_id), skill_dir=SKILL, provider=provider, model=model, maximum_cost_usd=maximum)
    except (OSError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/jobs/{job_id}/reviews/{provider}/run")
def review_run(job_id: str, provider: str, authorization: ReviewAuthorization) -> Any:
    _read_status(job_id)
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")

    def progress(stage: str, detail: str) -> None:
        status = _append_stage(_read_status(job_id), stage, detail)
        status["status"] = "validating"
        _write_status(job_id, status)

    try:
        report = run_review(
            job_dir=_job_dir(job_id),
            provider=provider,
            consent_token=authorization.consent_token,
            approved_bundle_hash=authorization.bundle_hash,
            rights_attested=authorization.rights_attested,
            maximum_cost_usd=authorization.maximum_cost_usd,
            progress=progress,
        )
        _update_release_status(job_id)
        return report
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        status = _read_status(job_id)
        status["status"] = "review_required"
        _write_status(job_id, status)
        raise HTTPException(400, str(error)) from error


@app.post("/api/jobs/{job_id}/playtests")
def playtests(job_id: str, evidence: PlaytestEvidence) -> dict[str, Any]:
    data = _read_status(job_id)
    if data.get("localStatus") != "playtest_candidate":
        raise HTTPException(409, "Install and validate a playtest candidate first")
    if evidence.difficulty not in {"Easy", "Normal", "Hard", "Expert", "ExpertPlus"}:
        raise HTTPException(400, "Unknown difficulty")
    if evidence.speed not in {"slow", "full"}:
        raise HTTPException(400, "Speed must be slow or full")
    path = _job_dir(job_id) / "playtests.json"
    items = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    items.append(evidence.model_dump(by_alias=True) | {"recordedAt": time.time()})
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    status = _update_release_status(job_id)
    return {"status": status["status"], "releaseGate": status.get("releaseGate"), "evidence": items}


_AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".mp4": "audio/mp4"}


@app.get("/api/jobs/{job_id}/click-track")
def job_click_track(job_id: str) -> FileResponse:
    _read_status(job_id)
    path = _job_dir(job_id) / "map" / "_beatforge" / "click_track.wav"
    if not path.is_file():
        raise HTTPException(404, "Click track is not available yet")
    return FileResponse(path, media_type="audio/wav", filename="click_track.wav")
