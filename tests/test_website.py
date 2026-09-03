"""Website contract tests for the studio console (no live headset claims)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from beatforge.api import app


ROOT = Path(__file__).resolve().parents[1]
WEB = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
API = (ROOT / "src" / "beatforge" / "api.py").read_text(encoding="utf-8")


def test_generate_uses_premium_endpoint_not_legacy_mapper() -> None:
    assert "api('/api/generate'" in WEB
    assert "beatforge.place" not in WEB
    assert "generate_chart" not in WEB
    assert "from beatforge.place" not in API
    assert "generate_chart" not in API


def test_timing_and_palette_use_codex_only() -> None:
    assert WEB.count('data-provider="codex"') == 1
    assert "Ask Codex" in WEB
    assert "Independent AI review" not in WEB
    assert 'class="connectors"' not in WEB


def test_hero_keeps_neon_logo() -> None:
    assert "/assets/beat-forge-logo.png" in WEB
    assert 'alt="BeatForge"' in WEB
    assert 'class="brand"' not in WEB
    assert "BeatForge <small>Studio</small>" not in WEB
    assert 'rel="icon" href="/assets/beat-forge-logo.png"' in WEB
    assert "<title>BeatForge Studio</title>" not in WEB
    assert "Refuse the guesswork" not in WEB
    assert "Official-corpus choreography system" not in WEB
    logo = ROOT / "web" / "assets" / "beat-forge-logo.png"
    assert logo.is_file() and logo.stat().st_size > 1000


def test_run_state_is_the_only_stage_panel() -> None:
    assert 'class="stage-deck"' in WEB
    assert 'id="palette-title"' not in WEB
    assert "Artwork and map colors" not in WEB
    assert WEB.count('id="run-title"') == 1
    assert WEB.count('id="statusTrack"') == 1
    assert 'id="palettePreview"' not in WEB
    assert 'id="approvePalette"' not in WEB


def test_accessible_names_focus_live_region_and_motion() -> None:
    assert 'aria-live="polite"' in WEB
    assert 'class="sr-only"' in WEB
    assert 'aria-label="Difficulties to generate"' in WEB
    assert 'aria-label="Mastered audio file"' in WEB
    assert 'role="status"' in WEB
    assert 'aria-label="Left saber color"' not in WEB
    assert "Ask Codex" in WEB
    assert "Prepare Codex review" not in WEB
    assert 'for="advancedSettings"' in WEB
    assert 'role="switch"' in WEB
    assert "input:not([type=\"file\"]):focus-visible" in WEB
    assert "prefers-reduced-motion" in WEB
    assert "prefers-contrast: more" in WEB


def test_failure_states_and_unbypassable_gates_are_explained() -> None:
    from beatforge.api import STUDIO_STATES

    assert "needs_anchors" in WEB
    assert 'id="clickTrack"' in WEB
    assert 'id="advancedSettings"' in WEB
    assert 'id="advancedFields" hidden' in WEB
    assert 'id="mapper"' in WEB
    assert 'id="seed"' in WEB
    assert 'id="timingPanel" hidden' in WEB
    assert 'id="timingAi"' in WEB
    assert 'id="downloadAnyway"' in WEB
    assert "Install to Beat Saber anyway" in WEB
    assert "Install to Beat Saber CustomLevels" in WEB
    assert "Save ZIP copy" in WEB
    assert "/api/jobs/${jobId}/continue-unconfirmed" in WEB
    assert "/api/jobs/" in WEB and "click-track" in WEB
    assert "anchors/suggest" in WEB
    assert "unconfirmed grid" in WEB
    assert "Download is still available" in WEB
    assert "Release candidate is not required to download" in WEB
    assert 'id="palettePreview"' not in WEB
    assert 'id="leftSwatch"' not in WEB
    assert "Approve artwork and colors" not in WEB
    assert "Install to Beat Saber CustomLevels" in WEB
    assert "Launch Beat Saber" in WEB
    assert "/api/jobs/${jobId}/launch" in WEB
    assert 'id="playtestForm"' in WEB
    assert 'id="installChip"' in WEB
    assert 'id="releaseChip"' in WEB
    assert "not a candidate" in WEB
    assert "/api/jobs/${jobId}/playtests" in WEB
    assert "Attach for headset evidence" not in WEB
    assert "Installed BeatForge map" not in WEB
    assert "cannot play or clear" in WEB
    assert "Release contract" not in WEB
    assert "Headset evidence remains" in WEB
    for state in STUDIO_STATES:
        assert f"{state}:" in WEB or f"'{state}':" in WEB


def test_index_serves_logo_asset() -> None:
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "/assets/beat-forge-logo.png" in page.text
    asset = client.get("/assets/beat-forge-logo.png")
    assert asset.status_code == 200
    assert len(asset.content) > 1000
    assert asset.content[:3] in {b"\x89PN", b"\xff\xd8\xff"}


def test_keyboard_viewport_and_responsive_contracts() -> None:
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in WEB
    assert "min-width: 320px" in WEB
    assert "@media (max-width: 930px)" in WEB
    assert "@media (max-width: 680px)" in WEB
    assert "clamp(38px, 5.2vw, 72px)" in WEB
    assert 'for="audioFile"' in WEB
    assert 'class="dropzone-input"' in WEB
    assert "opacity: 0" in WEB
    assert 'id="statusDecode"' in WEB
    assert 'id="decodeMeter"' in WEB
    assert "startElapsedClock" in WEB
    assert "selectedDifficulties" in WEB
    assert 'form.append(\'difficulties\'' in WEB or 'form.append("difficulties"' in WEB or "form.append('difficulties', chosen.join(','))" in WEB
    assert "input:not([type=\"file\"]):focus-visible" in WEB
    assert "if (!audioFile) return setState('Select a mastered audio file first.'" in WEB
    assert ".connectors, .result-grid { grid-template-columns: 1fr; }" in WEB or "grid-template-columns: 1fr" in WEB


def test_generate_without_audio_is_a_client_failure() -> None:
    client = TestClient(app)
    response = client.post("/api/generate")
    assert response.status_code in {400, 422}
    page = client.get("/")
    assert page.status_code == 200
    assert "Select a mastered audio file first" in page.text
    assert "needs_anchors" in page.text
    assert "needs_palette" in page.text
    assert "corpus_incomplete" in page.text or "Official corpus is incomplete" in page.text


def test_live_studio_http_generate_failure_and_logo_if_server_running() -> None:
    import httpx

    try:
        page = httpx.get("http://127.0.0.1:8001/", timeout=1.5)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        pytest.skip("studio server is not reachable on 127.0.0.1:8001")
    assert page.status_code == 200
    assert 'alt="BeatForge"' in page.text
    assert "/assets/beat-forge-logo.png" in page.text
    assert "Generate premium map" in page.text
    assert "if (!audioFile) return setState('Select a mastered audio file first.'" in page.text
    missing = httpx.post("http://127.0.0.1:8001/api/generate", timeout=2.0)
    assert missing.status_code in {400, 422}
    logo = httpx.get("http://127.0.0.1:8001/assets/beat-forge-logo.png", timeout=2.0)
    assert logo.status_code == 200
    assert logo.content[:3] in {b"\x89PN", b"\xff\xd8\xff"}
