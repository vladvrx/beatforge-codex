from __future__ import annotations

import json

from corpus import fetch_beatsaver as fetch


def _entry(map_id: str, score: float) -> dict:
    return {
        "map": {
            "id": map_id,
            "name": f"map {map_id}",
            "automapper": False,
            "metadata": {"bpm": 120, "duration": 180},
            "stats": {
                "score": score,
                "upvotes": 90,
                "downvotes": 10,
            },
            "versions": [
                {
                    "state": "Published",
                    "hash": "abc",
                    "downloadURL": "https://example.invalid/map.zip",
                    "diffs": [],
                }
            ],
        }
    }


def test_slim_map_enforces_85_percent_rating() -> None:
    playlist = {"id": 1, "name": "curated", "curator": "c", "owner": "o"}

    assert fetch.slim_map(_entry("low", 0.8499), playlist) is None
    accepted = fetch.slim_map(_entry("good", 0.85), playlist)

    assert accepted is not None
    assert accepted["rating"] == 0.85
    assert accepted["upvotes"] == 90
    assert fetch.slim_map(_entry("percent", 85), playlist) is not None


def test_prune_removed_maps_only_deletes_exact_corpus_ids(tmp_path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    monkeypatch.setattr(fetch, "CORPUS", corpus)
    low_map = corpus / "maps" / "low"
    keep_map = corpus / "maps" / "keep"
    low_map.mkdir(parents=True)
    keep_map.mkdir(parents=True)
    (low_map / "Info.dat").write_text("{}", encoding="utf-8")
    (keep_map / "Info.dat").write_text("{}", encoding="utf-8")
    fp_dir = corpus / "fingerprints"
    fp_dir.mkdir()
    (fp_dir / "low.json").write_text("[]", encoding="utf-8")
    (fp_dir / "keep.json").write_text("[]", encoding="utf-8")
    (corpus / "download_state.json").write_text(
        json.dumps({"done": ["low", "keep"], "failed": [], "remaining": ["low"]}),
        encoding="utf-8",
    )
    (corpus / "nn_index.json").write_text("{}", encoding="utf-8")

    counts = fetch.prune_removed_maps({"low", "../outside"})

    assert counts == {"catalog_entries": 2, "map_directories": 1, "fingerprints": 1}
    assert not low_map.exists() and keep_map.exists()
    assert not (fp_dir / "low.json").exists() and (fp_dir / "keep.json").exists()
    state = json.loads((corpus / "download_state.json").read_text(encoding="utf-8"))
    assert state == {"done": ["keep"], "failed": [], "remaining": []}
    assert not (corpus / "nn_index.json").exists()
