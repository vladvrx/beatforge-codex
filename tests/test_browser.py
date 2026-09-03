"""Headless Playwright coverage of the studio console. Not an MCP pass."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from beatforge.api import STUDIO_STATES, app


ROOT = Path(__file__).resolve().parents[1]
STUDIO_PORT = 18765


def _playwright_chromium():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
    except Exception as error:
        pytest.skip(f"Chromium is not installed for Playwright: {type(error).__name__}: {error}")
    return playwright, browser


@pytest.fixture(scope="module")
def studio_url():
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=STUDIO_PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while time.time() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        pytest.skip("could not bind the headless studio server")
    yield f"http://127.0.0.1:{STUDIO_PORT}"
    server.should_exit = True
    thread.join(timeout=4)


@pytest.fixture(scope="module")
def page(studio_url: str):
    playwright, browser = _playwright_chromium()
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    opened = context.new_page()
    opened.goto(studio_url, wait_until="networkidle")
    yield opened
    context.close()
    browser.close()
    playwright.stop()


def test_playwright_generate_without_audio_needs_anchors_copy_and_live_region(page) -> None:
    page.locator("#generate").click()
    assert page.locator("#statusTitle").inner_text() == "Select a mastered audio file first."
    live = page.locator(".status-box[aria-live='polite']")
    assert live.count() == 1
    assert page.locator("#filename").get_attribute("aria-live") == "polite"
    assert "needs_anchors" in page.content()
    assert page.locator("#clickTrack").get_attribute("aria-label") == "Click track for timing confirmation"
    assert "audio is not sent for this step" in page.content()
    assert "will not fill either gap silently" in page.locator(".microcopy").first.inner_text()
    assert page.locator("#statusDetail").inner_text() == "The mapper cannot run without local audio."


def test_playwright_difficulty_toggles_and_focus(page, studio_url: str) -> None:
    toggles = page.locator(".diff-toggle")
    assert toggles.count() == 5
    assert all(toggle.get_attribute("aria-pressed") == "true" for toggle in toggles.all())
    page.locator('.diff-toggle[data-difficulty="Easy"]').click()
    assert page.locator('.diff-toggle[data-difficulty="Easy"]').get_attribute("aria-pressed") == "false"
    generate = page.locator("#generate")
    generate.focus()
    assert page.evaluate("document.activeElement && document.activeElement.id") == "generate"
    html = page.content()
    for state in STUDIO_STATES:
        assert state in html
    assert page.locator('img[alt="BeatForge"]').count() == 1
    assert page.locator(".brand").count() == 0
    assert page.locator("#statusTrack").inner_text() == "No track loaded"
    assert page.locator("#statusAlgorithm").inner_text() == "Idle"
    assert page.locator(".review-btn").count() == 0
    assert page.locator("#gameStatus").get_attribute("role") == "status"
    assert page.locator("#audioFile").get_attribute("aria-label") == "Mastered audio file"


def _open_studio(studio_url: str, **context_kwargs):
    playwright, browser = _playwright_chromium()
    context = browser.new_context(**context_kwargs)
    opened = context.new_page()
    opened.goto(studio_url, wait_until="networkidle")
    return playwright, browser, context, opened


def test_playwright_mobile_tablet_and_200_percent_zoom(studio_url: str) -> None:
    cases = (
        {"viewport": {"width": 390, "height": 844}},
        {"viewport": {"width": 768, "height": 1024}},
        {"viewport": {"width": 1280, "height": 900}, "device_scale_factor": 2},
        {"viewport": {"width": 1280, "height": 900}, "reduced_motion": "reduce"},
        {"viewport": {"width": 1280, "height": 900}, "forced_colors": "active"},
    )
    for options in cases:
        playwright, browser, context, opened = _open_studio(studio_url, **options)
        try:
            assert opened.locator("article.connector").count() == 3
            assert opened.locator("#generate").is_visible()
            assert opened.locator('img[alt="BeatForge"]').count() == 1
            opened.locator("#generate").click()
            assert opened.locator("#statusTitle").inner_text() == "Select a mastered audio file first."
        finally:
            context.close()
            browser.close()
            playwright.stop()


def test_playwright_keyboard_reaches_generate_and_refuses_without_audio(studio_url: str) -> None:
    playwright, browser, context, opened = _open_studio(studio_url, viewport={"width": 1280, "height": 900})
    try:
        opened.locator("body").click(position={"x": 8, "y": 8})
        seen: list[str] = []
        for _ in range(24):
            opened.keyboard.press("Tab")
            focused = opened.evaluate("document.activeElement && document.activeElement.id")
            if focused:
                seen.append(str(focused))
            if focused == "generate":
                break
        assert "generate" in seen
        assert "audioFile" in seen or "title" in seen
        opened.keyboard.press("Enter")
        assert opened.locator("#statusTitle").inner_text() == "Select a mastered audio file first."
        assert opened.locator("#audioFile").get_attribute("accept")
        drop = opened.locator("#drop")
        assert drop.get_attribute("for") == "audioFile"
        box = drop.bounding_box()
        assert box is not None and box["height"] >= 90
        native = opened.locator("#audioFile")
        assert float(native.evaluate("el => getComputedStyle(el).opacity")) == 0
    finally:
        context.close()
        browser.close()
        playwright.stop()


def test_playwright_offline_and_unconfigured_codex_state(studio_url: str) -> None:
    playwright, browser = _playwright_chromium()
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.route("**/api/beat-saber/status", lambda route: route.abort())
    page.route(
        "**/api/settings/providers/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"configured":false,"runnerInstalled":false,"label":"OpenAI Codex","model":"gpt-5.6-codex","maximumCostUsd":5}',
        ),
    )
    try:
        page.goto(studio_url, wait_until="networkidle")
        page.wait_for_function("() => document.getElementById('gameStatus').textContent.includes('unavailable')")
        assert page.locator("#gameStatus").inner_text() == "Beat Saber check unavailable"
        page.evaluate("() => { document.getElementById('timingAi').hidden = false; }")
        page.locator('.timing-ai-btn[data-provider="codex"]').click()
        assert page.locator("#providerDialog").is_visible()
        assert "OpenAI Codex setup" in page.locator("#providerName").inner_text()
        assert "never returns it to this page" in page.locator("#providerDialog").inner_text()
        page.locator('#providerForm button[value="cancel"]').click()
        page.locator("#generate").click()
        assert page.locator("#statusTitle").inner_text() == "Select a mastered audio file first."
    finally:
        context.close()
        browser.close()
        playwright.stop()


def test_playwright_webmcp_mock_registers_and_runs_collaboration_loop(studio_url: str) -> None:
    """Exercise the page-side WebMCP contract without requiring experimental Chrome."""
    playwright, browser = _playwright_chromium()
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.add_init_script(
        """
        window.__registeredWebMcpTools = [];
        document.modelContext = {
          registerTool: async tool => { window.__registeredWebMcpTools.push(tool); }
        };
        """
    )
    opened = context.new_page()
    try:
        opened.goto(studio_url, wait_until="networkidle")
        opened.wait_for_function("() => document.getElementById('webmcpStatus').textContent === 'WebMCP ready'")
        result = opened.evaluate(
            """
            async () => {
              const tools = window.__registeredWebMcpTools;
              const call = name => tools.find(tool => tool.name === name).execute;
              await call('load_collaboration_demo')({});
              await call('set_mapping_plan')({
                title: 'Agent Shaped Rain',
                creativeBrief: 'Readable Expert chorus with a bright syncopated lift.',
                difficulties: ['Normal', 'Hard', 'Expert']
              });
              const generated = JSON.parse(await call('generate_beatmap')({ reason: 'Turn the shared brief into a preview.' }));
              const evidence = JSON.parse(await call('record_human_playtest')({
                difficulty: 'Expert', speed: 'full', passed: true, tester: 'Human tester', notes: 'Real person supplied this evidence.'
              }));
              const context = JSON.parse(await call('get_studio_context')({ includeActivity: true }));
              return {
                names: tools.map(tool => tool.name).sort(),
                title: document.getElementById('title').value,
                brief: document.getElementById('creativeBrief').value,
                generated,
                evidence,
                context,
                activity: document.getElementById('agentActivity').innerText
              };
            }
            """
        )
        assert result["names"] == sorted(
            [
                "get_studio_context",
                "set_mapping_plan",
                "load_collaboration_demo",
                "generate_beatmap",
                "review_current_beatmap",
                "record_human_playtest",
            ]
        )
        assert result["title"] == "Agent Shaped Rain"
        assert "Readable Expert chorus" in result["brief"]
        assert result["generated"]["mode"] == "rights-safe-demo"
        assert result["evidence"]["releaseGate"]["evidenceCount"] == 1
        assert result["context"]["job"]["demo"] is True
        assert "Agent called generate_beatmap" in result["activity"]
        assert "Human tester" in result["activity"]
    finally:
        context.close()
        browser.close()
        playwright.stop()
