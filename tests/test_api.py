"""API endpoint tests: health, generation job cycle, direct install."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from beatforge.api import app
from test_pipeline import write_click_track


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def click_wav(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_click_track(tmp_path_factory.mktemp("api_click") / "click_120.wav")


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["pipeline"] == "official-premium"


def test_index_has_codex_checker(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert 'data-provider="codex"' in r.text
    assert r.text.count('data-provider="codex"') == 1
    assert "Independent AI review" not in r.text
    assert "Prepare Codex review" not in r.text
    assert "sr-only" in r.text
    assert "prefers-reduced-motion" in r.text
    assert "/assets/beat-forge-logo.png" in r.text
    assert 'alt="BeatForge"' in r.text


def test_generate_rejects_unknown_difficulty(client: TestClient, click_wav: Path) -> None:
    from beatforge.api import parse_studio_difficulties
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        parse_studio_difficulties("")
    assert refused.value.status_code == 400
    empty = client.post(
        "/api/generate",
        files={"audio": ("song.wav", click_wav.read_bytes(), "audio/wav")},
        data={"difficulties": ","},
    )
    assert empty.status_code == 400
    omitted = client.post(
        "/api/generate",
        files={"audio": ("song.wav", click_wav.read_bytes(), "audio/wav")},
    )
    assert omitted.status_code == 200
    job = client.get(f"/api/jobs/{omitted.json()['id']}")
    assert job.status_code == 200
    assert job.json()["difficulties"] == ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
    response = client.post(
        "/api/generate",
        files={"audio": ("song.wav", click_wav.read_bytes(), "audio/wav")},
        data={"difficulties": "Insane"},
    )
    assert response.status_code == 400


def test_beat_saber_status(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BEATSABER_CUSTOM_LEVELS", str(tmp_path))
    r = client.get("/api/beat-saber/status")
    assert r.status_code == 200
    data = r.json()
    assert data["found"] is True
    assert data["path"] == str(tmp_path)


def _write_installed_beatforge_map(folder: Path, *, errors: list[Any] | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    difficulties = []
    for index, name in enumerate(["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]):
        filename = f"{name}Standard.dat"
        payload = {
            "version": "3.3.0",
            "colorNotes": [
                {"b": 1.0, "x": 0, "y": 1, "c": 0, "d": 1, "a": 0},
                {"b": 2.0, "x": 3, "y": 1, "c": 1, "d": 1, "a": 0},
            ],
            "bombNotes": [],
            "obstacles": [],
            "sliders": [],
            "burstSliders": [],
        }
        (folder / filename).write_text(json.dumps(payload), encoding="utf-8")
        difficulties.append(
            {
                "_difficulty": name,
                "_difficultyRank": index * 2 + 1,
                "_beatmapFilename": filename,
                "_noteJumpMovementSpeed": 10,
                "_noteJumpStartBeatOffset": 0,
                "_beatmapColorSchemeIdx": 0,
                "_environmentNameIdx": 0,
            }
        )
    info = {
        "_version": "2.1.0",
        "_songName": "Pacific Coast Highway",
        "_songAuthorName": "Kavinsky",
        "_levelAuthorName": "BeatForge",
        "_beatsPerMinute": 120,
        "_songFilename": "song.ogg",
        "_coverImageFilename": "cover.png",
        "_environmentName": "DefaultEnvironment",
        "_environmentNames": ["DefaultEnvironment"],
        "_colorSchemes": [],
        "_difficultyBeatmapSets": [{"_beatmapCharacteristicName": "Standard", "_difficultyBeatmaps": difficulties}],
    }
    (folder / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (folder / "song.ogg").write_bytes(b"ogg")
    (folder / "cover.png").write_bytes(b"png")
    (folder / ".env").write_text("SECRET=1", encoding="utf-8")
    reports = folder / "_beatforge"
    reports.mkdir(exist_ok=True)
    (reports / "qa_report.json").write_text(
        json.dumps({"status": "invalid" if errors else "playtest_candidate", "errors": errors or [], "warnings": []}),
        encoding="utf-8",
    )
    (reports / "provenance.json").write_text(json.dumps({"releaseGate": {}}), encoding="utf-8")


def test_import_installed_map_attaches_job_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from beatforge import api

    levels = tmp_path / "CustomLevels"
    installed = levels / "Pacific_Coast_Highway_8"
    _write_installed_beatforge_map(installed)
    (levels / "OfficialSong").mkdir()
    (levels / "OfficialSong" / "Info.dat").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BEATSABER_CUSTOM_LEVELS", str(levels))
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(api, "IMPORTS_DIR", tmp_path / "empty-imports")
    (tmp_path / "empty-imports").mkdir()
    listed = client.get("/api/beat-saber/playtest-maps")
    assert listed.status_code == 200
    folders = [item["folder"] for item in listed.json()["maps"]]
    assert folders == ["Pacific_Coast_Highway_8"]
    assert listed.json()["maps"][0]["source"] == "customLevels"
    traversal = client.post("/api/jobs/import", json={"folder": ".."})
    assert traversal.status_code == 400
    escaped = client.post("/api/jobs/import", json={"folder": str(installed)})
    assert escaped.status_code == 400
    imported = client.post("/api/jobs/import", json={"folder": "Pacific_Coast_Highway_8"})
    assert imported.status_code == 200
    body = imported.json()
    assert body["imported"] is True
    assert body["localStatus"] == "playtest_candidate"
    assert body["status"] == "review_required"
    assert body["customLevelsPath"] == str(installed)
    assert "releaseGate" in body
    assert body["releaseGate"]["allFiveFullSpeed"] is False
    job_map = tmp_path / "jobs" / body["id"] / "map"
    assert not (job_map / ".env").exists()
    assert (job_map / "song.ogg").is_file()
    assert (tmp_path / "jobs" / body["id"] / "map.zip").is_file()


def test_import_workspace_map_from_data_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from beatforge import api

    imports = tmp_path / "imports"
    workspace = imports / "pch-lighting"
    _write_installed_beatforge_map(workspace)
    monkeypatch.delenv("BEATSABER_CUSTOM_LEVELS", raising=False)
    monkeypatch.setattr(api, "find_custom_levels", lambda: None)
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(api, "IMPORTS_DIR", imports)
    listed = client.get("/api/beat-saber/playtest-maps")
    assert listed.status_code == 200
    assert listed.json()["maps"] == [
        {
            "folder": "pch-lighting",
            "source": "imports",
            "title": "Pacific Coast Highway",
            "artist": "Kavinsky",
            "qaStatus": "playtest_candidate",
            "hardFailures": 0,
        }
    ]
    refused = client.post("/api/jobs/import", json={"folder": "pch-lighting", "source": "customLevels"})
    assert refused.status_code in {400, 404}
    imported = client.post("/api/jobs/import", json={"folder": "pch-lighting", "source": "imports"})
    assert imported.status_code == 200
    body = imported.json()
    assert body["importSource"] == "imports"
    assert body["customLevelsPath"] is None
    assert body["sourcePath"] == str(workspace)
    assert body["localStatus"] == "playtest_candidate"


def test_generate_and_install_cycle(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    click_wav: Path,
) -> None:
    levels = tmp_path / "levels"
    levels.mkdir()
    monkeypatch.setenv("BEATSABER_CUSTOM_LEVELS", str(levels))

    def premium_stub(**kwargs: object) -> dict[str, object]:
        output = Path(kwargs["output"])
        output.mkdir(parents=True, exist_ok=True)
        difficulties = ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
        info_difficulties = []
        for index, name in enumerate(difficulties):
            filename = f"{name}Standard.dat"
            payload = {
                "version": "3.3.0",
                "colorNotes": [
                    {"b": 1.0, "x": 0, "y": 1, "c": 0, "d": 1, "a": 0},
                    {"b": 2.0, "x": 3, "y": 1, "c": 1, "d": 1, "a": 0},
                ],
                "bombNotes": [],
                "obstacles": [],
                "sliders": [],
                "burstSliders": [],
            }
            (output / filename).write_text(__import__("json").dumps(payload), encoding="utf-8")
            info_difficulties.append(
                {
                    "_difficulty": name,
                    "_difficultyRank": index * 2 + 1,
                    "_beatmapFilename": filename,
                    "_noteJumpMovementSpeed": 10 + index * 2,
                    "_noteJumpStartBeatOffset": 0,
                    "_beatmapColorSchemeIdx": 0,
                    "_environmentNameIdx": 0,
                }
            )
        color = {"r": 0.9, "g": 0.1, "b": 0.2, "a": 1}
        blue = {"r": 0.1, "g": 0.4, "b": 1, "a": 1}
        scheme = {
            "useOverride": True,
            "colorScheme": {
                "colorSchemeId": "Test",
                "saberAColor": color,
                "saberBColor": blue,
                "environmentColor0": color,
                "environmentColor1": blue,
                "obstaclesColor": color,
                "environmentColor0Boost": color,
                "environmentColor1Boost": blue,
            },
        }
        info = {
            "_version": "2.1.0",
            "_songName": "API Test",
            "_songAuthorName": "Tests",
            "_beatsPerMinute": 120,
            "_songFilename": "song.ogg",
            "_coverImageFilename": "cover.png",
            "_environmentName": "DefaultEnvironment",
            "_environmentNames": ["DefaultEnvironment"],
            "_colorSchemes": [scheme],
            "_difficultyBeatmapSets": [{"_beatmapCharacteristicName": "Standard", "_difficultyBeatmaps": info_difficulties}],
        }
        (output / "Info.dat").write_text(__import__("json").dumps(info), encoding="utf-8")
        (output / "song.ogg").write_bytes(b"test-audio")
        (output / "cover.png").write_bytes(b"test-cover")
        reports = output / "_beatforge"
        reports.mkdir()
        (reports / "qa_report.json").write_text('{"status":"playtest_candidate","errors":[],"warnings":[]}', encoding="utf-8")
        (reports / "analysis.json").write_text('{"status":"timing_verified"}', encoding="utf-8")
        (reports / "provenance.json").write_text('{"status":"playtest_candidate"}', encoding="utf-8")
        return {"status": "playtest_candidate", "returnCode": 0, "payload": {}}

    monkeypatch.setattr("beatforge.api.run_premium_pipeline", premium_stub)

    with open(click_wav, "rb") as f:
        r = client.post(
            "/api/generate",
            files={"audio": ("click.wav", f, "audio/wav")},
            data={
                "title": "API Test",
                "artist": "Tests",
                "seed": "99",
            },
        )
    assert r.status_code == 200
    job_id = r.json()["id"]

    status = None
    for _ in range(90):
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("review_required", "error"):
            break
        time.sleep(1)
    assert status is not None and status["status"] == "review_required", status
    assert status["seed"] == 99
    assert set(status["summary"]["difficulties"]) == {"Easy", "Normal", "Hard", "Expert", "ExpertPlus"}

    prepared = client.post(
        f"/api/jobs/{job_id}/reviews/codex/prepare",
        json={"model": "test-codex", "maximumCostUsd": 1.5},
    )
    assert prepared.status_code == 200
    manifest = prepared.json()
    assert manifest["provider"] == "codex"
    assert manifest["officialCorpusIncluded"] is False
    assert manifest["rightsAttestationRequired"] is True
    assert len(manifest["bundleHash"]) == 64

    inst = client.post(f"/api/jobs/{job_id}/install")
    assert inst.status_code == 200
    dest = Path(inst.json()["path"])
    assert dest.is_dir()
    assert (dest / "Info.dat").is_file()
    assert (dest / "ExpertStandard.dat").is_file()

    elevated_dest = levels / "elevated"
    elevated_dest.mkdir()

    def permission_denied(*args: object, **kwargs: object) -> Path:
        raise PermissionError("protected CustomLevels folder")

    monkeypatch.setattr("beatforge.api.install_map", permission_denied)
    monkeypatch.setattr(
        "beatforge.api.install_generated_map_elevated",
        lambda requested_job_id: elevated_dest,
    )
    elevated = client.post(f"/api/jobs/{job_id}/install")
    assert elevated.status_code == 200
    assert elevated.json() == {
        "installed": True,
        "path": str(elevated_dest),
        "status": "playtest_candidate",
    }

    def elevation_cancelled(job_id: str) -> Path:
        raise RuntimeError("Windows administrator approval was cancelled.")

    monkeypatch.setattr(
        "beatforge.api.install_generated_map_elevated", elevation_cancelled
    )
    cancelled = client.post(f"/api/jobs/{job_id}/install")
    assert cancelled.status_code == 403
    assert cancelled.headers["content-type"].startswith("application/json")
    assert "cancelled" in cancelled.json()["detail"]

    def unexpected_install_error(*args: object, **kwargs: object) -> Path:
        raise TypeError("unexpected installer failure")

    monkeypatch.setattr("beatforge.api.install_map", unexpected_install_error)
    failed = client.post(f"/api/jobs/{job_id}/install")
    assert failed.status_code == 500
    assert failed.headers["content-type"].startswith("application/json")
    assert "unexpected installer failure" in failed.json()["detail"]

    dl = client.get(f"/api/jobs/{job_id}/download")
    assert dl.status_code == 200
    assert len(dl.content) > 1000


def test_studio_states_are_the_ten_consumed_release_states() -> None:
    from beatforge.api import STUDIO_STATES, TERMINAL_STATES

    assert STUDIO_STATES == (
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
    assert "validating" not in TERMINAL_STATES
    assert len(STUDIO_STATES) == 10


def test_click_track_serves_analysis_wav_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient, click_wav: Path
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    job_id = "clickjob"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    api._write_status(job_id, {"id": job_id, "status": "needs_anchors", "stages": []})
    missing = client.get(f"/api/jobs/{job_id}/click-track")
    assert missing.status_code == 404
    dest = job_dir / "map" / "_beatforge"
    dest.mkdir(parents=True)
    (dest / "click_track.wav").write_bytes(click_wav.read_bytes())
    ok = client.get(f"/api/jobs/{job_id}/click-track")
    assert ok.status_code == 200
    assert "audio/wav" in ok.headers["content-type"]
    assert len(ok.content) == click_wav.stat().st_size


def test_continue_unconfirmed_only_from_needs_anchors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(api, "_run_job", lambda job_id: None)
    job_id = "unconf"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    api._write_status(job_id, {"id": job_id, "status": "needs_palette", "stages": []})
    refused = client.post(f"/api/jobs/{job_id}/continue-unconfirmed")
    assert refused.status_code == 409
    api._write_status(job_id, {"id": job_id, "status": "needs_anchors", "stages": []})
    ok = client.post(f"/api/jobs/{job_id}/continue-unconfirmed")
    assert ok.status_code == 200
    assert ok.json()["continueUnconfirmed"] is True
    saved = api._read_status(job_id)
    assert saved["continueUnconfirmed"] is True
    assert saved["status"] == "queued"


def test_unconfirmed_pack_auto_installs_into_custom_levels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    levels = tmp_path / "CustomLevels"
    levels.mkdir()
    monkeypatch.setenv("BEATSABER_CUSTOM_LEVELS", str(levels))
    job_id = "autoinst"
    job_dir = tmp_path / job_id
    map_dir = job_dir / "map"
    map_dir.mkdir(parents=True)
    (job_dir / "input.wav").write_bytes(b"RIFF")
    (map_dir / "Info.dat").write_text('{"_songName":"Draft"}', encoding="utf-8")
    api._write_status(
        job_id,
        {
            "id": job_id,
            "status": "queued",
            "title": "Draft Song",
            "artist": "A",
            "mapper": "M",
            "seed": 1,
            "continueUnconfirmed": True,
            "stages": [],
        },
    )

    def fake_pipeline(**kwargs: object) -> dict:
        return {"status": "invalid", "payload": {"message": "unconfirmed"}}

    monkeypatch.setattr(api, "run_premium_pipeline", fake_pipeline)
    monkeypatch.setattr(api, "summarize_map", lambda _path: {"difficulties": {}})
    api._run_job(job_id)
    status = api._read_status(job_id)
    assert status["localStatus"] == "unconfirmed_pack"
    assert status["installed"] is True
    dest = Path(status["customLevelsPath"])
    assert dest.is_dir()
    assert dest.parent == levels
    assert (dest / "Info.dat").is_file()
    assert dest.name != "Pacific_Coast_Highway"


def test_unconfirmed_pack_can_be_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    job_id = "zipjob"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "map.zip").write_bytes(b"PK" + b"\x00" * 1200)
    api._write_status(
        job_id,
        {"id": job_id, "status": "invalid", "localStatus": "unconfirmed_pack", "title": "Draft"},
    )
    download = client.get(f"/api/jobs/{job_id}/download")
    assert download.status_code == 200
    assert len(download.content) > 1000


def test_launch_does_not_claim_a_software_playtest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    job_id = "launchjob"
    api._write_status(job_id, {"id": job_id, "status": "needs_anchors", "localStatus": "needs_anchors"})
    refused = client.post(f"/api/jobs/{job_id}/launch")
    assert refused.status_code == 409
    api._write_status(
        job_id,
        {"id": job_id, "status": "playtest_candidate", "localStatus": "playtest_candidate"},
    )
    monkeypatch.setattr(
        api,
        "launch_beat_saber",
        lambda: {
            "launched": True,
            "path": r"C:\Beat Saber.exe",
            "playedBySoftware": False,
            "message": "human headset only",
        },
    )
    ok = client.post(f"/api/jobs/{job_id}/launch")
    assert ok.status_code == 200
    body = ok.json()
    assert body["playedBySoftware"] is False
    assert body["jobStatus"] == "playtest_candidate"
    assert "human" in body["message"].casefold()
    leftover = json.loads((tmp_path / job_id / "status.json").read_text(encoding="utf-8"))
    assert leftover["status"] == "playtest_candidate"


def test_job_status_includes_release_gate_without_promoting_to_release_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from beatforge import api

    monkeypatch.delenv("BEATFORGE_AI_RELEASE_ROUTE", raising=False)
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    job_id = "gatejob"
    api._write_status(job_id, {"id": job_id, "status": "playtest_candidate", "localStatus": "playtest_candidate"})
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "playtest_candidate"
    assert "releaseGate" in body
    assert body["releaseGate"]["allFiveFullSpeed"] is False
    assert body["releaseGate"]["separateFreshSightRead"] is False


def test_playtest_endpoint_records_human_evidence_without_software_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    job_id = "headset"
    api._write_status(job_id, {"id": job_id, "status": "playtest_candidate", "localStatus": "playtest_candidate"})
    response = client.post(
        f"/api/jobs/{job_id}/playtests",
        json={
            "difficulty": "ExpertPlus",
            "speed": "full",
            "passed": True,
            "tester": "mapper",
            "freshSightRead": False,
            "notes": "full-speed pass",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] != "release_candidate"
    assert body["evidence"][0]["tester"] == "mapper"
    assert body["releaseGate"]["allFiveFullSpeed"] is False
    stored = json.loads((tmp_path / job_id / "playtests.json").read_text(encoding="utf-8"))
    assert stored[0]["difficulty"] == "ExpertPlus"


def test_launch_beat_saber_popen_does_not_write_playtests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from beatforge import install

    exe = tmp_path / "Beat Saber.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setenv("BEATSABER_GAME_ROOT", str(tmp_path))
    captured: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        return object()

    monkeypatch.setattr(install.subprocess, "Popen", fake_popen)
    result = install.launch_beat_saber()
    assert result["playedBySoftware"] is False
    assert captured["command"] == [str(exe)]
    assert captured["cwd"] == str(tmp_path)
    assert not list(tmp_path.glob("playtests.json"))


def test_release_candidate_requires_headset_and_sight_read_in_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from beatforge import api

    monkeypatch.delenv("BEATFORGE_AI_RELEASE_ROUTE", raising=False)
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    job_id = "job1"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    api._write_status(job_id, {"status": "playtest_candidate", "localStatus": "playtest_candidate"})
    status = api._update_release_status(job_id)
    assert status["status"] == "playtest_candidate"
    (job_dir / "review-codex-report.json").write_text(
        '{"verdict":"pass","findings":[]}', encoding="utf-8"
    )
    status = api._update_release_status(job_id)
    assert status["status"] == "studio_reviewed"
    evidence = []
    for difficulty in ("Easy", "Normal", "Hard", "Expert", "ExpertPlus"):
        evidence.append({"difficulty": difficulty, "speed": "full", "passed": True, "tester": "mapper", "freshSightRead": False})
    evidence.append({"difficulty": "Expert", "speed": "slow", "passed": True, "tester": "mapper", "freshSightRead": False})
    evidence.append({"difficulty": "ExpertPlus", "speed": "slow", "passed": True, "tester": "mapper", "freshSightRead": False})
    (job_dir / "playtests.json").write_text(json.dumps(evidence), encoding="utf-8")
    status = api._update_release_status(job_id)
    assert status["status"] == "studio_reviewed"
    evidence.append({"difficulty": "ExpertPlus", "speed": "full", "passed": True, "tester": "fresh-player", "freshSightRead": True})
    (job_dir / "playtests.json").write_text(json.dumps(evidence), encoding="utf-8")
    status = api._update_release_status(job_id)
    assert status["status"] == "studio_reviewed"
    assert status["status"] != "release_candidate"
    assert status["releaseGate"]["aiReleaseRoute"] is False
    provenance = job_dir / "map" / "_beatforge" / "provenance.json"
    provenance.parent.mkdir(parents=True)
    provenance.write_text(
        json.dumps(
            {
                "status": "playtest_candidate",
                "releaseGate": {
                    "structuralInspection": True,
                    "fullSpeedVrPlaytest": False,
                    "slowVrPlaytest": False,
                    "freshSightRead": False,
                },
            }
        ),
        encoding="utf-8",
    )
    status = api._update_release_status(job_id)
    assert status["status"] == "studio_reviewed"
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    assert payload["releaseGate"]["fullSpeedVrPlaytest"] is True
    assert payload["releaseGate"]["freshSightRead"] is True
    assert status["releaseGate"]["separateFreshSightRead"] is True
    assert status["releaseGate"]["provenance"]["freshSightRead"] is True


def test_release_candidate_only_when_ai_release_route_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from beatforge import api

    monkeypatch.setenv("BEATFORGE_AI_RELEASE_ROUTE", "1")
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    job_id = "ai-route"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    api._write_status(job_id, {"status": "playtest_candidate", "localStatus": "playtest_candidate"})
    (job_dir / "review-codex-report.json").write_text(
        '{"verdict":"pass","findings":[]}', encoding="utf-8"
    )
    evidence = []
    for difficulty in ("Easy", "Normal", "Hard", "Expert", "ExpertPlus"):
        evidence.append({"difficulty": difficulty, "speed": "full", "passed": True, "tester": "mapper", "freshSightRead": False})
    evidence.append({"difficulty": "Expert", "speed": "slow", "passed": True, "tester": "mapper", "freshSightRead": False})
    evidence.append({"difficulty": "ExpertPlus", "speed": "slow", "passed": True, "tester": "mapper", "freshSightRead": False})
    evidence.append({"difficulty": "ExpertPlus", "speed": "full", "passed": True, "tester": "fresh-player", "freshSightRead": True})
    (job_dir / "playtests.json").write_text(json.dumps(evidence), encoding="utf-8")
    provenance = job_dir / "map" / "_beatforge" / "provenance.json"
    provenance.parent.mkdir(parents=True)
    provenance.write_text(json.dumps({"releaseGate": {}}), encoding="utf-8")
    status = api._update_release_status(job_id)
    assert status["status"] == "release_candidate"
    assert status["releaseGate"]["aiReleaseRoute"] is True


def test_codex_suggest_uses_runner_installed_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(api, "get_secret", lambda _provider: "codex-secret")
    monkeypatch.setattr(
        api,
        "runner_status",
        lambda _provider: {"configured": True, "runnerInstalled": True, "label": "OpenAI Codex"},
    )
    monkeypatch.setattr(
        api,
        "suggest_timing_anchors",
        lambda **kwargs: {"anchors": [{"beat": 0.0, "sample": 882, "kind": "downbeat"}]},
    )
    monkeypatch.setattr(api, "_run_job", lambda *_args, **_kwargs: None)
    ready = "anchors-ready"
    api._write_status(ready, {"id": ready, "status": "needs_anchors"})
    ok = client.post(
        f"/api/jobs/{ready}/anchors/suggest/codex",
        json={"analysisApproved": True},
    )
    assert ok.status_code == 200
    blocked = "anchors-blocked"
    api._write_status(blocked, {"id": blocked, "status": "needs_anchors"})
    monkeypatch.setattr(
        api,
        "runner_status",
        lambda _provider: {"configured": True, "runnerInstalled": False, "label": "OpenAI Codex"},
    )
    refused = client.post(
        f"/api/jobs/{blocked}/anchors/suggest/codex",
        json={"analysisApproved": True},
    )
    assert refused.status_code == 409
    assert "OpenAI Codex CLI" in refused.json()["detail"]


def test_provider_settings_reject_unavailable_model(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("beatforge.api.get_secret", lambda _provider: "provider-secret")
    monkeypatch.setattr("beatforge.api.discover_models", lambda _provider, _key: ["real-model"])
    response = client.put(
        "/api/settings/providers/codex",
        json={"model": "missing-model", "maximumCostUsd": 1.0},
    )
    assert response.status_code == 409
    assert "unavailable" in response.json()["detail"].casefold()


def test_premium_exit_codes_map_to_studio_refusal_states(monkeypatch: pytest.MonkeyPatch) -> None:
    from beatforge import premium

    class FakeProcess:
        def __init__(self, code: int) -> None:
            self.returncode = code
            self.stdout = io.StringIO(json.dumps({"status": "x", "message": "pipeline"}))
            self.stderr = io.StringIO("BEATFORGE_PROGRESS\tanalysis\tdecoding audio\n")

        def wait(self) -> int:
            return self.returncode

    def run_with(code: int) -> str:
        monkeypatch.setattr(premium.subprocess, "Popen", lambda *args, **kwargs: FakeProcess(code))
        result = premium.run_premium_pipeline(
            audio=Path("song.wav"),
            output=Path("out"),
            title="T",
            artist="A",
            mapper="M",
            seed=1,
            anchors=None,
            palette=None,
            progress=lambda *_args: None,
        )
        return result["status"]

    assert run_with(0) == "playtest_candidate"
    assert run_with(1) == "invalid"
    assert run_with(3) == "needs_anchors"
    assert run_with(4) == "corpus_incomplete"
    assert run_with(5) == "needs_palette"
    assert run_with(99) == "error"


def test_premium_forwards_progress_lines_from_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    from beatforge import premium

    class FakeProcess:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.returncode = 5
            self.stdout = io.StringIO(json.dumps({"status": "needs_palette"}))
            self.stderr = io.StringIO(
                "BEATFORGE_PROGRESS\ttiming\trunning Beat This on the mix\n"
                "unrelated warning\n"
                "BEATFORGE_PROGRESS\tdecode\tdecoded 42%\t42.0\n"
                "BEATFORGE_PROGRESS\tchoreography\tsolving joint CP-SAT for ExpertPlus\n"
            )

        def wait(self) -> int:
            return self.returncode

    seen: list[tuple[str, str]] = []
    percents: list[float] = []

    def capture(stage: str, detail: str, percent: float | None = None, log: bool = True) -> None:
        seen.append((stage, detail))
        if percent is not None:
            percents.append(percent)

    monkeypatch.setattr(premium.subprocess, "Popen", FakeProcess)
    result = premium.run_premium_pipeline(
        audio=Path("song.wav"),
        output=Path("out"),
        title="Highway",
        artist="Kavinsky",
        mapper="M",
        seed=1,
        anchors=None,
        palette=None,
        progress=capture,
    )
    assert result["status"] == "needs_palette"
    assert ("track", "Highway · Kavinsky") in seen
    assert ("timing", "running Beat This on the mix") in seen
    assert ("decode", "decoded 42%") in seen
    assert ("choreography", "solving joint CP-SAT for ExpertPlus") in seen
    assert percents == [42.0]


@pytest.mark.parametrize(
    ("pipeline_status", "expected"),
    [
        ("needs_anchors", "needs_anchors"),
        ("needs_palette", "needs_palette"),
        ("corpus_incomplete", "corpus_incomplete"),
        ("invalid", "invalid"),
        ("error", "error"),
    ],
)
def test_run_job_produces_pipeline_refusal_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pipeline_status: str, expected: str
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    job_id = "job-state"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "input.wav").write_bytes(b"RIFF")
    api._write_status(job_id, {"id": job_id, "status": "queued", "title": "T", "artist": "A", "mapper": "M", "seed": 1, "stages": []})
    seen: list[str] = []
    original = api._write_status

    def capture(identifier: str, data: dict) -> None:
        seen.append(str(data.get("status")))
        original(identifier, data)

    monkeypatch.setattr(api, "_write_status", capture)

    def fake_pipeline(**kwargs: object) -> dict:
        progress = kwargs["progress"]
        progress("validating", "running hard gates")
        return {"status": pipeline_status, "payload": {"message": pipeline_status}}

    monkeypatch.setattr(api, "run_premium_pipeline", fake_pipeline)
    api._run_job(job_id)
    assert expected in seen
    assert "validating" in seen
    assert api._read_status(job_id)["status"] == expected


def test_generate_starts_queued_and_exceptions_become_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, click_wav: Path, client: TestClient
) -> None:
    from beatforge import api

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(
        api,
        "run_premium_pipeline",
        lambda **kwargs: {"status": "needs_anchors", "payload": {"message": "timing"}},
    )
    with open(click_wav, "rb") as handle:
        response = client.post(
            "/api/generate",
            files={"audio": ("click.wav", handle, "audio/wav")},
            data={"title": "Queued", "artist": "Tests", "seed": "1"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    job_id = "explode"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "input.wav").write_bytes(b"RIFF")
    api._write_status(job_id, {"id": job_id, "status": "queued", "title": "T", "artist": "A", "mapper": "M", "seed": 1, "stages": []})
    monkeypatch.setattr(api, "run_premium_pipeline", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    api._run_job(job_id)
    assert api._read_status(job_id)["status"] == "error"
