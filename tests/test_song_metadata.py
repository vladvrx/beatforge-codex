from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from beatforge import api
from beatforge import song_metadata


class FakeResponse:
    def __init__(self, payload: object, *, content: bytes = b"", status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise song_metadata.httpx.HTTPStatusError("request failed", request=None, response=None)

    def json(self) -> object:
        return self._payload


class FakeClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        assert url == song_metadata.ITUNES_SEARCH_URL
        return FakeResponse(
            {
                "results": [
                    {
                        "kind": "song",
                        "trackId": 123,
                        "trackName": "The Less I Know the Better",
                        "artistName": "Tame Impala",
                        "collectionName": "Currents",
                        "collectionArtistName": "Tame Impala",
                        "trackTimeMillis": 216000,
                        "previewUrl": "http://example.invalid/preview.m4a",
                    },
                    {"kind": "song", "trackName": "The Less I Know", "artistName": "Other Artist"},
                ]
            }
        )


def test_search_song_metadata_returns_metadata_and_preview_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(song_metadata.httpx, "Client", FakeClient)
    result = song_metadata.search_song_metadata("The Less I Know the Better", "Tame Impala", cache_dir=tmp_path)
    assert result["track"]["album"] == "Currents"
    assert result["track"]["previewUrl"].startswith("https://")
    assert result["audio"]["kind"] == "provider-preview"
    assert result["audio"]["downloadable"] is False
    assert result["fullRecordingDownload"] is None


def test_search_song_metadata_rejects_a_weak_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class EmptyClient(FakeClient):
        def get(self, url: str, **kwargs: object) -> FakeResponse:
            return FakeResponse({"results": [{"kind": "song", "trackName": "Completely Different", "artistName": "Nobody"}]})

    monkeypatch.setattr(song_metadata.httpx, "Client", EmptyClient)
    with pytest.raises(song_metadata.SongMetadataNotFound):
        song_metadata.search_song_metadata("The Less I Know the Better", "Tame Impala", cache_dir=tmp_path)


def test_metadata_endpoint_publishes_cover_proxy_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    metadata_id = "0123456789abcdef0123"
    artwork = tmp_path / f"{metadata_id}.jpg"
    artwork.write_bytes(b"cached-cover")
    monkeypatch.setattr(api, "METADATA_DIR", tmp_path)
    monkeypatch.setattr(
        api,
        "search_song_metadata",
        lambda *_args, **_kwargs: {
            "status": "found",
            "metadataId": metadata_id,
            "track": {"title": "Track", "artist": "Artist"},
            "artwork": {"url": "https://is1-ssl.mzstatic.com/image.jpg", "cached": True},
            "palette": None,
            "audio": None,
            "_cachePath": str(artwork),
        },
    )
    response = TestClient(api.app).get("/api/song-metadata", params={"title": "Track", "artist": "Artist"})
    assert response.status_code == 200
    assert response.json()["artwork"]["previewUrl"] == f"/api/song-metadata/{metadata_id}/artwork"
    cover = TestClient(api.app).get(f"/api/song-metadata/{metadata_id}/artwork")
    assert cover.status_code == 200
    assert cover.content == b"cached-cover"
