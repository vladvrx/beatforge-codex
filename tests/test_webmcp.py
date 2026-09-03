"""WebMCP challenge contracts for the browser collaboration surface."""

from pathlib import Path

from fastapi.testclient import TestClient

from beatforge.api import app


ROOT = Path(__file__).resolve().parents[1]
WEB = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
WEBMCP = (ROOT / "web" / "webmcp.js").read_text(encoding="utf-8")


def test_webmcp_uses_standard_document_registration_and_named_tools() -> None:
    assert "document.modelContext.registerTool({" in WEBMCP
    assert 'name: "get_studio_context"' in WEBMCP
    for name in (
        "set_mapping_plan",
        "load_collaboration_demo",
        "generate_beatmap",
        "review_current_beatmap",
        "record_human_playtest",
    ):
        assert f'name: "{name}"' in WEBMCP
    assert "inputSchema" in WEBMCP
    assert "window.BeatForgeWebMcp" in WEBMCP


def test_webmcp_panel_and_script_are_in_the_page() -> None:
    assert 'id="webmcpStatus"' in WEB
    assert 'id="creativeBrief"' in WEB
    assert 'id="loadDemo"' in WEB
    assert 'id="agentTools"' in WEB
    assert 'id="agentActivity"' in WEB
    assert '<script src="/web/webmcp.js"></script>' in WEB


def test_webmcp_script_is_served_by_the_studio() -> None:
    client = TestClient(app)
    response = client.get("/web/webmcp.js")
    assert response.status_code == 200
    assert "document.modelContext.registerTool" in response.text


def test_demo_is_explicitly_rights_safe_and_not_a_real_download() -> None:
    assert "Synthetic 30-second groove" in WEB
    assert "Browser preview · no ZIP" in WEB
    assert "Human headset evidence is still required" in WEB
