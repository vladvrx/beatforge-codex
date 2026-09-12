"""Browser acceptance of real sample playback and isolated local-workflow contracts."""

from __future__ import annotations

import copy
import json
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlparse

import pytest

from test_browser import _playwright_chromium, studio_url

ROOT = Path(__file__).resolve().parents[1]
DEMO = json.loads((ROOT / "web" / "assets" / "demo" / "preview.json").read_text())
A = "aaaaaaaaaaaa"
B = "bbbbbbbbbbbb"
C = "cccccccccccc"


@pytest.fixture
def browser_page(studio_url):
    playwright, browser = _playwright_chromium()
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page, studio_url
    context.close()
    browser.close()
    playwright.stop()
    assert errors == [], errors


def job(identifier=A, state="review_required", **extra):
    return {"id": identifier, "title": "Saved score", "artist": "Fixture", "status": state, "difficulties": ["Hard"], "localStatus": "playtest_candidate" if state == "review_required" else None, "canRetry": state in {"review_required", "interrupted", "cancelled"}, "canCancel": state in {"running", "queued"}, "mappingPlan": {"density": 0.75, "style": "flow"}, "stages": [], "releaseGate": {"evidenceCount": 0, "localHardGates": True, "aiReleaseRoute": False}, **extra}


def preview(identifier=A, notes="Hard"):
    result = {key: copy.deepcopy(value) for key, value in DEMO.items() if key not in {"charts", "summary"}}
    result.update(jobId=identifier, difficulty="Hard", chart=copy.deepcopy(DEMO["charts"][notes]), difficulties=["Hard"], chartHash=identifier[0] * 64, audioHash="f" * 64, audioUrl="/assets/demo/song.ogg", canRevise=True)
    return result


def mock_api(page, jobs=None, previews=None, suggestions=None, feedback_error=None):
    """Mock service state only; sample bytes and all UI code are served unchanged."""
    jobs = jobs if jobs is not None else {}
    previews = previews if previews is not None else {}
    calls = []
    suggestions = suggestions or {"ready": False, "evidenceCount": 0, "reasons": ["Rate three distinct charts."], "changedFields": {}, "suggestedPlan": {"density": 1}}

    def respond(route, payload, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    def handler(route):
        request = route.request
        path = urlparse(request.url).path
        body = request.post_data_json if request.method == "POST" and "application/json" in request.headers.get("content-type", "") else None
        calls.append((request.method, path, body))
        if path == "/api/jobs":
            return respond(route, {"jobs": list(jobs.values()), "total": len(jobs)})
        if path == "/api/learning":
            return respond(route, {"feedbackCount": 0, "comparisonCount": 0, "presets": [], "recentFeedback": [], "suggestions": suggestions})
        if path == "/api/learning/suggestions":
            return respond(route, suggestions)
        if path == "/api/mapping-plan/resolve":
            return respond(route, body or {})
        if path == "/api/beat-saber/status":
            return respond(route, {"found": False, "path": None})
        if path == "/api/beat-saber/playtest-maps":
            return respond(route, {"found": False, "maps": []})
        if path.startswith("/api/settings/providers/"):
            return respond(route, {"configured": False, "runnerInstalled": False, "label": "OpenAI Codex", "model": "test-model", "maximumCostUsd": 5})
        if path == "/api/feedback":
            return respond(route, {"detail": feedback_error}, 409) if feedback_error else respond(route, {"feedback": body, "feedbackCount": 1})
        if path == "/api/comparisons":
            return respond(route, {"comparison": body, "comparisonCount": 1})
        if path.startswith("/api/jobs/"):
            parts = path.split("/")
            identifier = parts[3]
            action = parts[4] if len(parts) > 4 else ""
            if action == "preview" and identifier in previews:
                return respond(route, previews[identifier])
            if identifier not in jobs:
                return respond(route, {"detail": "Run not found"}, 404)
            if action == "events":
                return route.fulfill(status=200, content_type="text/event-stream", body=f"event: progress\ndata: {json.dumps(jobs[identifier])}\n\nevent: end\ndata: {{}}\n\n")
            if action == "retry":
                jobs[C] = job(C, "needs_anchors", retryOf=identifier)
                return respond(route, {"id": C, "status": "queued", "retryOf": identifier})
            if action == "cancel":
                jobs[identifier].update(status="cancelled", canCancel=False, canRetry=True)
                return respond(route, {"id": identifier, "status": "cancelled"})
            if not action:
                return respond(route, jobs[identifier])
        return respond(route, {"detail": f"Unexpected test request {path}"}, 404)

    page.route("**/api/**", handler)
    return calls


def test_sample_plays_actual_audio_and_switches_actual_difficulties(browser_page):
    page, url = browser_page
    calls = mock_api(page)
    page.goto(url, wait_until="networkidle")
    page.locator("#loadDemo").click()
    page.wait_for_function("window.BeatForgePreview.getState()?.notes > 0 && document.querySelector('#previewAudio').duration === 30")
    assert page.evaluate("window.BeatForgePreview.getState().notes") == len(DEMO["charts"]["Hard"]["colorNotes"])
    assert page.locator("#previewDifficulty option").all_text_contents() == DEMO["difficulties"]
    page.locator("#previewPlay").click()
    page.wait_for_function("document.querySelector('#previewAudio').currentTime > 0.15")
    page.locator("#previewPlay").click()
    assert page.evaluate("document.querySelector('#previewAudio').paused") is True
    page.locator("#previewSeek").fill("12")
    page.locator("#previewSeek").dispatch_event("input")
    page.wait_for_function("Number(document.querySelector('#chartCanvas').dataset.renderedNotes) > 0")
    assert float(page.locator("#chartCanvas").get_attribute("data-time")) == pytest.approx(12, abs=0.1)
    page.locator("#previewDifficulty").select_option("Easy")
    assert page.evaluate("window.BeatForgePreview.getState().notes") == len(DEMO["charts"]["Easy"]["colorNotes"])
    assert page.evaluate("window.BeatForgePreview.getState().time") == pytest.approx(12, abs=0.1)
    page.locator("#generate").click()
    page.wait_for_function("window.BeatForgeApp.getStudioState().job?.demo === true")
    assert "20 notes" in page.locator("#difficultyList").inner_text()
    assert page.locator("#download").get_attribute("href").endswith("assets/demo/map.zip")
    assert page.evaluate("window.BeatForgeApp.getStudioState().job.releaseGate.evidenceCount") == 0
    assert not any(method == "POST" and path == "/api/generate" for method, path, _ in calls)


def test_creative_controls_are_sent_with_the_real_upload(browser_page, monkeypatch):
    from beatforge import api

    page, url = browser_page
    monkeypatch.setattr(api, "_run_job", lambda identifier: None)
    jobs = {A: job(A, "needs_anchors")}
    mock_api(page, jobs=jobs)
    captured = {}
    def capture(route):
        request = route.request
        message = BytesParser(policy=default).parsebytes(b"Content-Type: " + request.headers["content-type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.post_data_buffer)
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            captured[name] = part.get_payload(decode=True)
        route.continue_()
    page.route("**/api/generate", capture)
    page.goto(url, wait_until="networkidle")
    page.locator("#audioFile").set_input_files(ROOT / "web" / "assets" / "demo" / "song.ogg")
    page.locator("#creativeBrief").fill("Flowing patterns with no bombs")
    page.locator("#planDensity").select_option("0.75")
    page.locator("#planInstrument").select_option("drums")
    page.locator("#planBombs").select_option("hide")
    page.locator('.diff-toggle[data-difficulty="Easy"]').click()
    with page.expect_response(lambda response: urlparse(response.url).path == "/api/generate") as generated:
        page.locator("#generate").click()
    response = generated.value
    assert response.status == 200
    identifier = response.json()["id"]
    assert (api._job_dir(identifier) / "input.ogg").read_bytes() == (ROOT / "web" / "assets" / "demo" / "song.ogg").read_bytes()
    plan = json.loads(captured["mappingPlan"])
    assert plan["density"] == 0.75
    assert plan["dominantInstrument"] == "drums"
    assert plan["noBombs"] is True
    assert plan["brief"] == "Flowing patterns with no bombs"
    assert captured["difficulties"].decode() == "Normal,Hard,Expert,ExpertPlus"
    assert captured["engine"] == b"premium"


def test_static_page_blocks_full_generation_and_explains_local_studio(browser_page):
    page, url = browser_page
    page.context.add_init_script("window.BEATFORGE_STATIC = true")
    calls = mock_api(page)
    page.goto(url, wait_until="networkidle")
    page.locator("#audioFile").set_input_files(ROOT / "web" / "assets" / "demo" / "song.ogg")
    page.locator("#generate").click()
    assert page.locator("#statusTitle").inner_text() == "Open your local Studio to generate"
    assert "Start-BeatForge.ps1" in page.locator("#statusDetail").inner_text()
    assert page.locator("#feedbackForm").is_hidden()
    assert not any(method == "POST" and path == "/api/generate" for method, path, _ in calls)


def test_history_restores_selection_retries_saved_input_and_cancels(browser_page):
    page, url = browser_page
    jobs = {A: job(A, "interrupted"), B: job(B, "running", title="Working score")}
    calls = mock_api(page, jobs=jobs)
    page.goto(url, wait_until="networkidle")
    page.locator(f'[data-open="{A}"]').click()
    page.wait_for_function("document.querySelector('#jobLabel').textContent.includes('aaaaaaaaaaaa')")
    assert page.locator("#planDensity").input_value() == "0.75"
    assert page.evaluate("localStorage.getItem('beatforge.lastJob')") == A
    page.reload(wait_until="networkidle")
    page.wait_for_function("document.querySelector('#jobLabel').textContent.includes('aaaaaaaaaaaa')")
    page.locator(f'[data-retry="{A}"]').click()
    page.wait_for_function("document.querySelector('#jobLabel').textContent.includes('cccccccccccc')")
    assert jobs[C]["retryOf"] == A
    page.locator(f'[data-cancel="{B}"]').click()
    page.wait_for_function("!document.querySelector('[data-cancel=bbbbbbbbbbbb]')")
    assert jobs[B]["status"] == "cancelled"
    posts = [path for method, path, _ in calls if method == "POST"]
    assert f"/api/jobs/{A}/retry" in posts
    assert f"/api/jobs/{B}/cancel" in posts
    assert "/api/generate" not in posts


def test_ab_switching_preserves_audio_time_and_saves_explicit_preference(browser_page):
    page, url = browser_page
    jobs = {A: job(A, summary=DEMO["summary"]), B: job(B, summary=DEMO["summary"])}
    previews = {A: preview(A, "Hard"), B: preview(B, "Easy")}
    calls = mock_api(page, jobs=jobs, previews=previews)
    page.goto(url, wait_until="networkidle")
    page.locator(f'[data-open="{A}"]').click()
    page.wait_for_function("window.BeatForgePreview.getState()?.jobId === 'aaaaaaaaaaaa'")
    page.locator("#compareControls summary").click()
    page.locator("#compareJob").select_option(B)
    page.locator("#compareLoad").click()
    page.wait_for_function("!document.querySelector('#compareB').disabled")
    page.locator("#previewSeek").fill("9.5")
    page.locator("#previewSeek").dispatch_event("input")
    page.locator("#compareB").click()
    page.wait_for_function("window.BeatForgePreview.getState()?.jobId === 'bbbbbbbbbbbb' && window.BeatForgePreview.getState().notes === 20 && Math.abs(window.BeatForgePreview.getState().time - 9.5) < 0.1")
    assert page.evaluate("window.BeatForgePreview.getState().notes") == len(DEMO["charts"]["Easy"]["colorNotes"])
    page.locator("#preferB").click()
    page.wait_for_function("document.querySelector('#feedbackMessage').textContent.includes('Preference for B saved')")
    body = next(body for method, path, body in reversed(calls) if method == "POST" and path == "/api/comparisons")
    assert body["preferredJob"] == B
    assert body["alternateJob"] == A
    assert not any(path.endswith("/playtests") for _, path, _ in calls)


def test_feedback_error_is_visible_and_suggestions_require_explicit_apply(browser_page):
    page, url = browser_page
    suggestions = {"ready": True, "evidenceCount": 3, "reasons": ["Three charts were too dense."], "changedFields": {"density": {"from": 1, "to": 0.85}}, "suggestedPlan": {"density": 0.85, "style": "flow"}}
    jobs = {A: job(A, summary=DEMO["summary"])}
    calls = mock_api(page, jobs=jobs, previews={A: preview()}, suggestions=suggestions, feedback_error="Feedback could not be saved; your previous notes are preserved.")
    page.goto(url, wait_until="networkidle")
    assert page.locator("#planDensity").input_value() == "auto"
    page.wait_for_function("!document.querySelector('#applySuggestion').disabled")
    page.locator("#applySuggestion").click()
    assert page.locator("#planDensity").input_value() == "0.85"
    assert not any(path == "/api/generate" for _, path, _ in calls)
    page.locator(f'[data-open="{A}"]').click()
    page.wait_for_function("window.BeatForgePreview.getState()?.jobId === 'aaaaaaaaaaaa'")
    page.locator("#rating-flow").select_option("3")
    page.locator("#feedbackNotes").fill("The chorus felt cramped.")
    page.locator('#feedbackForm button[type="submit"]').click()
    page.wait_for_function("document.querySelector('#feedbackMessage').textContent.includes('could not be saved')")
    assert not any(path.endswith("/playtests") for _, path, _ in calls)
    assert page.evaluate("window.BeatForgeApp.getStudioState().job.releaseGate.evidenceCount") == 0
