"""WebMCP challenge contracts for the browser collaboration surface."""

from pathlib import Path
import json
import re

from fastapi.testclient import TestClient

from beatforge.api import app


ROOT = Path(__file__).resolve().parents[1]
WEB = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
WEBMCP = (ROOT / "web" / "webmcp.js").read_text(encoding="utf-8")


def test_webmcp_uses_standard_document_registration_and_named_tools() -> None:
    assert "document.modelContext.registerTool({" in WEBMCP
    assert 'name: "get_studio_context"' in WEBMCP
    for name in (
        "find_song_metadata",
        "import_song_preview",
        "set_mapping_plan",
        "load_collaboration_demo",
        "generate_beatmap",
        "review_current_beatmap",
        "record_human_playtest",
        "get_chart_preview",
        "get_learning_summary",
        "record_mapping_feedback",
    ):
        assert re.search(r'name\s*:\s*"' + re.escape(name) + r'"', WEBMCP)
    assert "inputSchema" in WEBMCP
    assert "window.BeatForgeWebMcp" in WEBMCP


def test_webmcp_panel_and_script_are_in_the_page() -> None:
    assert 'id="webmcpStatus"' in WEB
    assert 'id="creativeBrief"' in WEB
    assert 'id="loadDemo"' in WEB
    assert 'id="agentTools"' in WEB
    assert 'id="agentActivity"' in WEB
    assert 'id="findMetadata"' in WEB
    assert 'id="metadataCover"' in WEB
    assert 'id="metadataPalette"' in WEB
    assert 'id="useMetadataPreview"' in WEB
    assert '<script src="/web/webmcp.js"></script>' in WEB


def test_webmcp_script_is_served_by_the_studio() -> None:
    client = TestClient(app)
    response = client.get("/web/webmcp.js")
    assert response.status_code == 200
    assert "document.modelContext.registerTool" in response.text


def test_demo_download_contains_real_rights_safe_chart_assets() -> None:
    assert "Synthetic 30-second groove" in WEB
    assert "Download sample ZIP" in WEB
    assert "Human headset evidence is still required" in WEB
    demo = json.loads((ROOT / "web" / "assets" / "demo" / "preview.json").read_text())
    assert demo["source"] == "synthetic-authored-score"
    assert len(demo["charts"]) == 5
    assert (ROOT / "web" / "assets" / "demo" / "map.zip").is_file()


def test_metadata_lookup_is_explicitly_preview_only() -> None:
    assert "/api/song-metadata" in WEB
    assert "fullRecordingDownload" in WEBMCP or "fullRecordingDownload" in WEB
    assert "Short provider preview; not the full recording" in WEB
    assert "never infers a full-recording download" in WEBMCP
