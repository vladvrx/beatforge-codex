"""CLI default path is the premium skill pipeline, not beatforge.place."""

from __future__ import annotations

from pathlib import Path

from beatforge.cli import main


def test_cli_source_does_not_import_place_at_module_level() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "beatforge" / "cli.py").read_text(encoding="utf-8")
    assert "from beatforge.place import generate_chart" in source
    before_legacy, _, after_legacy = source.partition("def _legacy_main")
    assert "from beatforge.place import generate_chart" not in before_legacy
    assert "from beatforge.place import generate_chart" in after_legacy


def test_cli_default_invokes_premium_pipeline(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"RIFF")
    captured: dict = {}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        (kwargs["output"] / "Info.dat").write_text("{}", encoding="utf-8")
        return {"status": "needs_anchors", "returnCode": 3, "payload": {"status": "needs_anchors"}, "stdout": "", "stderr": ""}

    monkeypatch.setattr("beatforge.cli.run_premium_pipeline", fake_pipeline)
    monkeypatch.setattr("beatforge.cli.package_map", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("zip only after playtest_candidate")))
    code = main([str(audio), "--out", str(tmp_path / "out"), "--title", "Song", "--artist", "Artist"])
    assert code == 3
    assert captured["audio"] == audio.resolve()
    assert captured["title"] == "Song"
    assert captured["artist"] == "Artist"
