"""Bridge the local web app to the canonical beat-saber-mapping pipeline."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable

from .mapping_plan import normalize_mapping_plan

PROGRESS_PREFIX = "BEATFORGE_PROGRESS\t"


class PipelineCancelled(RuntimeError):
    """The Studio job cancelled its mapper and child analysis processes."""


def _stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def _notify_progress(progress: Callable[..., None], stage: str, detail: str, percent: float | None = None) -> None:
    kwargs: dict[str, Any] = {}
    if percent is not None:
        kwargs["percent"] = percent
        kwargs["log"] = False
    try:
        progress(stage, detail, **kwargs)
    except TypeError:
        progress(stage, detail)


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "beat-saber-mapping"
SCRIPTS = SKILL / "scripts"


def corpus_database() -> Path:
    configured = os.environ.get("BEATFORGE_CORPUS_DB")
    if configured:
        return Path(configured)
    workspace_candidate = ROOT.parents[1] / "work" / "official-corpus.sqlite3"
    if workspace_candidate.is_file():
        return workspace_candidate
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Codex" / "beat-saber-mapping" / "official-corpus.sqlite3"
    return ROOT / "data" / "official-corpus.sqlite3"


def run_premium_pipeline(
    *,
    audio: Path,
    output: Path,
    title: str,
    artist: str,
    mapper: str,
    seed: int,
    anchors: Path | None,
    palette: Path | None,
    progress: Callable[..., None],
    cover: Path | None = None,
    allow_unconfirmed: bool = False,
    difficulties: list[str] | None = None,
    mapping_plan: dict[str, Any] | None = None,
    engine: str = "premium",
    rl_model: Path | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    analysis_dir: Path | None = None,
) -> dict[str, Any]:
    if engine not in {"premium", "rl"}:
        raise ValueError("engine must be premium or rl")
    if engine == "rl" and (rl_model is None or not Path(rl_model).is_file()):
        raise ValueError("The RL engine requires an existing trained checkpoint")
    if cancel_requested and cancel_requested():
        raise PipelineCancelled("Mapping cancelled before the pipeline started")
    command = [
        sys.executable,
        str(SCRIPTS / "generate_map.py"),
        str(audio),
        "--out",
        str(output),
        "--profile",
        "official-premium",
        "--full-spread",
        "--title",
        title,
        "--artist",
        artist,
        "--mapper",
        mapper,
        "--seed",
        str(seed),
        "--corpus-database",
        str(corpus_database()),
    ]
    if anchors:
        command += ["--anchors", str(anchors)]
    if palette:
        command += ["--palette", str(palette)]
    if cover:
        command += ["--cover", str(cover)]
    if allow_unconfirmed:
        command += ["--continue-unconfirmed"]
    for name in difficulties or []:
        command += ["--difficulty", name]
    if mapping_plan is not None:
        plan = normalize_mapping_plan(mapping_plan)
        plan_path = output / "_beatforge" / "mapping_request.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        command += ["--mapping-plan", str(plan_path)]
    if engine == "rl":
        command += ["--engine", "rl", "--rl-model", str(Path(rl_model).resolve())]
    if analysis_dir is not None:
        command += ["--analysis-dir", str(analysis_dir)]
    progress("track", f"{title} · {artist}")
    progress("pipeline", f"starting {engine} generate_map")
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        bufsize=1,
        start_new_session=os.name != "nt",
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    progress_errors: list[Exception] = []

    def consume_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_chunks.append(line)
            stripped = line.strip()
            if stripped.startswith(PROGRESS_PREFIX):
                parts = stripped.split("\t")
                if len(parts) >= 3:
                    percent = None
                    if len(parts) >= 4 and parts[3]:
                        try:
                            percent = float(parts[3])
                        except ValueError:
                            percent = None
                    try:
                        _notify_progress(progress, parts[1], parts[2], percent)
                    except Exception as error:
                        progress_errors.append(error)

    def consume_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_chunks.append(line)

    stderr_thread = threading.Thread(target=consume_stderr, daemon=True)
    stdout_thread = threading.Thread(target=consume_stdout, daemon=True)
    stderr_thread.start()
    stdout_thread.start()
    try:
        while True:
            if progress_errors:
                raise progress_errors[0]
            if cancel_requested and cancel_requested():
                _stop_process_tree(process)
                raise PipelineCancelled("Mapping cancelled")
            try:
                returncode = process.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        _stop_process_tree(process)
        raise
    finally:
        stderr_thread.join()
        stdout_thread.join()
    if progress_errors:
        raise progress_errors[0]
    stdout_text = "".join(stdout_chunks)
    stderr_text = "".join(stderr_chunks)
    status = {
        0: "playtest_candidate",
        1: "invalid",
        3: "needs_anchors",
        4: "corpus_incomplete",
        5: "needs_palette",
    }.get(returncode, "error")
    payload: dict[str, Any] = {}
    output_text = stdout_text.strip()
    if output_text:
        for position in range(len(output_text)):
            if output_text[position] != "{":
                continue
            try:
                parsed = json.loads(output_text[position:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                payload = parsed
                break
    return {
        "status": status,
        "returnCode": returncode,
        "payload": payload,
        "stdout": stdout_text[-12000:],
        "stderr": stderr_text[-12000:],
    }


def package_map(map_folder: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(map_folder.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(map_folder).as_posix()
            if relative.startswith("_beatforge/cover_candidate") or relative.endswith(".tmp"):
                continue
            archive.write(path, relative)


def summarize_map(map_folder: Path) -> dict[str, Any]:
    info = json.loads((map_folder / "Info.dat").read_text(encoding="utf-8-sig"))
    counts: dict[str, dict[str, int]] = {}
    for group in info.get("_difficultyBeatmapSets", []):
        for difficulty in group.get("_difficultyBeatmaps", []):
            name = str(difficulty.get("_difficulty"))
            filename = str(difficulty.get("_beatmapFilename"))
            payload = json.loads((map_folder / filename).read_text(encoding="utf-8-sig"))
            counts[name] = {
                "notes": len(payload.get("colorNotes", [])),
                "arcs": len(payload.get("sliders", [])),
                "chains": len(payload.get("burstSliders", [])),
                "bombs": len(payload.get("bombNotes", [])),
                "walls": len(payload.get("obstacles", [])),
            }
    return {
        "title": info.get("_songName"),
        "artist": info.get("_songAuthorName"),
        "bpm": info.get("_beatsPerMinute"),
        "difficulties": counts,
        "colorScheme": (info.get("_colorSchemes") or [None])[0],
    }
