from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from beatforge import providers


ALIGNED_TIMING_AUDIT = {
    "gridSource": "confirmed",
    "clickTrackStartSample": 882,
    "clickTrackMiddleSample": 7_591_936,
    "clickTrackEndSample": 15_181_952,
    "anchorsReviewed": True,
    "alignment": "aligned",
    "notes": "Start, middle, and end click-track samples match analysis.json.",
}


def review_job(tmp_path: Path, provider: str = "codex") -> tuple[Path, dict]:
    job = tmp_path / "job"
    map_dir = job / "map"
    map_dir.mkdir(parents=True)
    (map_dir / "Info.dat").write_text("{}", encoding="utf-8")
    (map_dir / "song.ogg").write_bytes(b"licensed-audio")
    (map_dir / "cover.png").write_bytes(b"cover")
    (map_dir / "private-corpus.sqlite3").write_bytes(b"must-not-leave")
    (map_dir / "credentials.json").write_text('{"CODEX_API_KEY":"must-not-leave"}', encoding="utf-8")
    (map_dir / ".env").write_text("CODEX_API_KEY=must-not-leave\n", encoding="utf-8")
    (map_dir / "secret.key").write_text("must-not-leave", encoding="utf-8")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# validator", encoding="utf-8")
    consent = providers.prepare_review_bundle(
        job_dir=job,
        skill_dir=skill,
        provider=provider,
        model="studio-model",
        maximum_cost_usd=2.5,
    )
    return job, consent


def test_codex_is_the_only_configured_review_connector() -> None:
    assert list(providers.PROVIDERS) == ["codex"]
    assert providers.PROVIDERS["codex"]["label"] == "OpenAI Codex"


def test_review_bundle_excludes_official_corpus(tmp_path: Path) -> None:
    job, consent = review_job(tmp_path)
    names = {item["name"] for item in consent["contents"]}
    assert "map/private-corpus.sqlite3" not in names
    assert "map/credentials.json" not in names
    assert "map/.env" not in names
    assert "map/secret.key" not in names
    assert "map/song.ogg" in names
    assert consent["officialCorpusIncluded"] is False
    assert consent["containsAudioAndArtwork"] is True


def test_click_track_and_jpeg_cover_count_as_review_media(tmp_path: Path) -> None:
    job = tmp_path / "job"
    map_dir = job / "map" / "_beatforge"
    map_dir.mkdir(parents=True)
    (job / "map" / "Info.dat").write_text("{}", encoding="utf-8")
    (job / "map" / "cover.jpeg").write_bytes(b"jpeg-cover")
    (map_dir / "click_track.wav").write_bytes(b"RIFF")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# validator", encoding="utf-8")
    consent = providers.prepare_review_bundle(
        job_dir=job,
        skill_dir=skill,
        provider="codex",
        model="studio-model",
        maximum_cost_usd=1.0,
    )
    names = {item["name"] for item in consent["contents"]}
    assert "map/cover.jpeg" in names
    assert "map/_beatforge/click_track.wav" in names
    assert consent["containsAudioAndArtwork"] is True


def test_review_parser_accepts_jsonl_and_rejects_malformed_output() -> None:
    payload = {"verdict": "pass", "summary": "clear", "findings": [], "timingAudit": ALIGNED_TIMING_AUDIT}
    events = "noise\n" + json.dumps({"result": json.dumps(payload)})
    assert providers._parse_result(events) == payload
    with pytest.raises(ValueError, match="review schema"):
        providers._parse_result("not json")
    with pytest.raises(ValueError, match="invalid verdict"):
        providers._parse_result(json.dumps({"verdict": "ok", "summary": "x", "findings": [], "timingAudit": ALIGNED_TIMING_AUDIT}))
    with pytest.raises(ValueError, match="invalid findings"):
        providers._parse_result(json.dumps({"verdict": "pass", "summary": "x", "findings": "nope"}))
    with pytest.raises(ValueError, match="timingAudit"):
        providers._parse_result(json.dumps({"verdict": "pass", "summary": "x", "findings": []}))
    with pytest.raises(ValueError, match="misaligned cannot pass"):
        providers._parse_result(json.dumps({"verdict": "pass", "summary": "x", "findings": [], "timingAudit": {**ALIGNED_TIMING_AUDIT, "alignment": "misaligned"}}))
    with pytest.raises(ValueError, match="review schema"):
        providers._parse_result("{")


def test_codex_runner_normalizes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, consent = review_job(tmp_path)
    payload = {"verdict": "pass", "summary": "No findings", "findings": [], "timingAudit": ALIGNED_TIMING_AUDIT}
    monkeypatch.setattr(providers, "get_secret", lambda _provider: "provider-secret")
    monkeypatch.setattr(providers, "_find_codex", lambda: "codex.exe")
    monkeypatch.setattr(providers, "_run_process", lambda **_kwargs: (payload, {"runId": "codex-run"}))
    report = providers.run_review(
        job_dir=job,
        provider="codex",
        consent_token=consent["consentToken"],
        approved_bundle_hash=consent["bundleHash"],
        rights_attested=True,
        maximum_cost_usd=2.5,
    )
    assert report["provider"] == "codex"
    assert report["model"] == "studio-model"
    assert report["verdict"] == "pass"
    assert report["unresolvedFindings"] == 0


def test_review_refuses_bad_consent_rights_budget_and_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, consent = review_job(tmp_path)
    monkeypatch.setattr(providers, "get_secret", lambda _provider: None)
    common = dict(job_dir=job, provider="codex", approved_bundle_hash=consent["bundleHash"], maximum_cost_usd=2.5)
    with pytest.raises(ValueError, match="consent token"):
        providers.run_review(consent_token="wrong", rights_attested=True, **common)
    with pytest.raises(ValueError, match="rights attestation"):
        providers.run_review(consent_token=consent["consentToken"], rights_attested=False, **common)
    with pytest.raises(ValueError, match="prepared maximum"):
        providers.run_review(consent_token=consent["consentToken"], rights_attested=True, **{**common, "maximum_cost_usd": 3.0})
    with pytest.raises(ValueError, match="not configured"):
        providers.run_review(consent_token=consent["consentToken"], rights_attested=True, **common)


def test_child_process_secrets_are_scoped_and_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    secret = "top-secret-provider-key"
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs["env"])
        payload = {"verdict": "pass", "summary": "ok", "findings": [], "timingAudit": ALIGNED_TIMING_AUDIT}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload) + "\n" + secret, stderr="")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    payload, usage = providers._run_process(command=["reviewer"], staging=tmp_path, env_key="CODEX_API_KEY", secret=secret, output_file=tmp_path / "missing.json")
    assert payload["verdict"] == "pass"
    assert captured["CODEX_API_KEY"] == secret
    assert "OPENAI_API_KEY" not in captured
    assert secret not in usage["events"]
    assert secret not in os.environ.values()


def test_child_process_cancellation_does_not_leak_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "cancel-secret"
    monkeypatch.setattr(providers.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 130, stdout="", stderr=f"cancelled {secret}"))
    with pytest.raises(RuntimeError) as error:
        providers._run_process(command=["reviewer"], staging=tmp_path, env_key="CODEX_API_KEY", secret=secret, output_file=tmp_path / "missing.json")
    assert secret not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_child_process_timeout_redacts_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "timeout-secret"

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") == 180.0
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=180, output="", stderr=secret.encode())

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out") as error:
        providers._run_process(command=["reviewer"], staging=tmp_path, env_key="CODEX_API_KEY", secret=secret, output_file=tmp_path / "missing.json")
    assert secret not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_child_process_budget_exhaustion_redacts_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "budget-secret"
    monkeypatch.setattr(providers.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr=f"max budget exceeded {secret}"))
    with pytest.raises(RuntimeError, match="exit code 1") as error:
        providers._run_process(command=["reviewer"], staging=tmp_path, env_key="CODEX_API_KEY", secret=secret, output_file=tmp_path / "missing.json")
    assert secret not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_review_refuses_unavailable_cli_and_unknown_model_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, consent = review_job(tmp_path)
    monkeypatch.setattr(providers, "get_secret", lambda _provider: "provider-secret")
    monkeypatch.setattr(providers, "_find_codex", lambda: None)
    with pytest.raises(RuntimeError, match="OpenAI Codex CLI is not installed"):
        providers.run_review(job_dir=job, provider="codex", consent_token=consent["consentToken"], approved_bundle_hash=consent["bundleHash"], rights_attested=True, maximum_cost_usd=2.5)
    with pytest.raises(ValueError, match="unknown provider"):
        providers.discover_models("unknown", "key")


def test_documentation_manifest_covers_required_authorities() -> None:
    manifest = json.loads((Path(__file__).resolve().parents[1] / "skills" / "beat-saber-mapping" / "references" / "documentation-manifest.json").read_text(encoding="utf-8"))
    urls = {item["url"] for item in manifest["sources"]}
    required = {
        "https://beatsaber.com/documentation/level-mappers/index.html",
        "https://bsmg.wiki/mapping/map-format.html",
        "https://bsmg.wiki/mapping/basic-mapping.html",
        "https://developers.google.com/optimization/cp",
        "https://musicbrainz.org/doc/Cover_Art_Archive/API",
        "https://developers.openai.com/codex/noninteractive/",
        "https://developers.openai.com/codex/non-interactive-mode",
    }
    assert required <= urls
    assert manifest["accessedDate"] >= "2026-08-22"


def test_codex_review_prompt_requires_click_track_anchor_audit() -> None:
    prompt = providers._review_prompt("abc123")
    assert "click track" in prompt.lower() or "click-track" in prompt.lower()
    assert "timingAudit" in prompt
    assert "OpenAI Codex" in prompt
    assert "misaligned" in prompt
    assert providers.REVIEW_SCHEMA["required"][-1] == "timingAudit"
    skill = (Path(__file__).resolve().parents[1] / "skills" / "beat-saber-mapping" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Codex review" in skill
    assert "timingAudit.alignment" in skill


def test_codex_exec_is_ephemeral_read_only_and_allows_non_git_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, consent = review_job(tmp_path)
    captured: dict = {}
    payload = {"verdict": "pass", "summary": "ok", "findings": [], "timingAudit": ALIGNED_TIMING_AUDIT}

    def fake_process(**kwargs: object):
        captured["command"] = list(kwargs["command"])
        captured["env_key"] = kwargs["env_key"]
        return payload, {"events": "ok"}

    monkeypatch.setattr(providers, "get_secret", lambda _provider: "codex-secret")
    monkeypatch.setattr(providers, "_find_codex", lambda: "codex.exe")
    monkeypatch.setattr(providers, "_run_process", fake_process)
    providers.run_review(job_dir=job, provider="codex", consent_token=consent["consentToken"], approved_bundle_hash=consent["bundleHash"], rights_attested=True, maximum_cost_usd=2.5)
    command = captured["command"]
    assert command[:8] == ["codex.exe", "exec", "--ephemeral", "--json", "--sandbox", "read-only", "--ask-for-approval", "never"]
    assert "--ignore-user-config" in command
    assert "--skip-git-repo-check" in command
    assert "--output-schema" in command
    assert "--output-last-message" in command
    assert captured["env_key"] == "CODEX_API_KEY"


@pytest.mark.codex
@pytest.mark.network
def test_live_codex_model_fetch_is_explicitly_labeled() -> None:
    """Live connector probe. Skips without credentials; never treats mocks as live."""

    if os.name != "nt":
        pytest.skip("live Codex credentials require Windows Credential Manager")

    missing = [name for name in providers.PROVIDERS if not providers.get_secret(name)]
    if missing:
        pytest.skip(f"Codex credentials missing for {', '.join(missing)}")
    for name in providers.PROVIDERS:
        models = providers.discover_models(name, providers.get_secret(name) or "")
        assert models, f"{name} live model list was empty"
        assert "studio-model" not in models
