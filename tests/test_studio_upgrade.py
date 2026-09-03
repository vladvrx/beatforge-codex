from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "beat-saber-mapping" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artwork import delta_e_2000, rgb_to_lab
from generate_map import info_dat
from kinematics import cross_hand_finding, transition_finding
from validate_map import validate_package


KNOWN_PCH_HANDCLAPS = [
    ((0, 1, 5), (2, 0, 4)),
    ((0, 1, 7), (2, 1, 4)),
    ((0, 2, 7), (2, 0, 6)),
    ((0, 1, 5), (2, 1, 4)),
    ((0, 1, 7), (2, 1, 6)),
    ((0, 1, 7), (2, 0, 6)),
    ((0, 1, 5), (2, 1, 6)),
    ((0, 1, 7), (2, 2, 6)),
    ((0, 1, 7), (2, 2, 4)),
    ((1, 0, 7), (3, 1, 6)),
    ((1, 1, 5), (3, 1, 6)),
    ((1, 0, 5), (3, 1, 6)),
    ((1, 1, 7), (3, 0, 4)),
    ((1, 0, 5), (3, 2, 4)),
    ((1, 2, 7), (3, 0, 4)),
    ((2, 0, 7), (3, 1, 4)),
]


@pytest.mark.parametrize(("red_shape", "blue_shape"), KNOWN_PCH_HANDCLAPS)
def test_known_pch_inward_doubles_are_release_blocking(
    red_shape: tuple[int, int, int], blue_shape: tuple[int, int, int]
) -> None:
    red = {"b": 1, "x": red_shape[0], "y": red_shape[1], "c": 0, "d": red_shape[2]}
    blue = {"b": 1, "x": blue_shape[0], "y": blue_shape[1], "c": 1, "d": blue_shape[2]}
    finding = cross_hand_finding(red, blue)
    assert finding is not None
    assert finding["constraint"] == "inward_facing_handclap"


def test_outward_double_is_not_a_handclap() -> None:
    red = {"b": 1, "x": 0, "y": 1, "c": 0, "d": 6}
    blue = {"b": 1, "x": 2, "y": 1, "c": 1, "d": 7}
    assert cross_hand_finding(red, blue) is None


KNOWN_PCH_PATH_CROSSINGS = [
    ((1, 1, 6), (2, 1, 4)),
    ((1, 1, 5), (2, 1, 7)),
]


@pytest.mark.parametrize(("red_shape", "blue_shape"), KNOWN_PCH_PATH_CROSSINGS)
def test_known_pch_saber_path_crossings_are_release_blocking(
    red_shape: tuple[int, int, int], blue_shape: tuple[int, int, int]
) -> None:
    red = {"b": 1, "x": red_shape[0], "y": red_shape[1], "c": 0, "d": red_shape[2]}
    blue = {"b": 1, "x": blue_shape[0], "y": blue_shape[1], "c": 1, "d": blue_shape[2]}
    finding = cross_hand_finding(red, blue)
    assert finding is not None
    assert finding["constraint"] == "saber_path_intersection"


def test_collinear_same_direction_double_is_not_a_path_crossing() -> None:
    red = {"b": 1, "x": 1, "y": 1, "c": 0, "d": 3}
    blue = {"b": 1, "x": 3, "y": 1, "c": 1, "d": 3}
    assert cross_hand_finding(red, blue) is None


def test_same_color_return_swing_is_allowed_but_repeat_is_blocked() -> None:
    previous = {"b": 1, "x": 0, "y": 0, "c": 0, "d": 1}
    natural_return = {"b": 1.5, "x": 0, "y": 1, "c": 0, "d": 0}
    repeated = {"b": 1.25, "x": 0, "y": 1, "c": 0, "d": 1}
    assert transition_finding(previous, natural_return, 120, recovery_beats=0.3) is None
    finding = transition_finding(previous, repeated, 120, recovery_beats=0.3)
    assert finding is not None
    assert finding["constraint"] == "repeated_cut_without_reset"


def test_five_thousand_same_color_transitions_have_deterministic_constraints() -> None:
    rng = random.Random(20260821)
    known = {
        "simultaneous_same_color",
        "repeated_cut_without_reset",
        "parity_family_repeat",
        "recovery_speed",
        "impossible_rotational_reversal",
        "body_lean_reversal",
        "rotational_momentum",
    }
    recoveries = (0.125, 0.25, 0.5, 1.0)
    bpms = (60.0, 90.0, 120.0, 160.0, 200.0, 240.0, 260.0)
    for _ in range(5_000):
        color = rng.randrange(2)
        previous = {
            "b": 8.0,
            "x": rng.randrange(4),
            "y": rng.randrange(3),
            "c": color,
            "d": rng.randrange(9),
        }
        current = {
            "b": 8.0 + rng.choice((0.0, 0.125, 0.25, 0.5, 1.0)),
            "x": rng.randrange(4),
            "y": rng.randrange(3),
            "c": color,
            "d": rng.randrange(9),
        }
        bpm = rng.choice(bpms)
        recovery = rng.choice(recoveries)
        first = transition_finding(previous, current, bpm, recovery_beats=recovery)
        second = transition_finding(previous, current, bpm, recovery_beats=recovery)
        assert first == second
        if first is not None:
            assert first["constraint"] in known
            assert first["failedConstraint"] == first["constraint"]
            assert first["previousBeat"] == previous["b"]
            assert first["currentBeat"] == current["b"]
            assert first["color"] == color
            assert first["previousPosition"] == {"x": previous["x"], "y": previous["y"]}
            assert first["currentPosition"] == {"x": current["x"], "y": current["y"]}
            assert first["previousDirection"] == previous["d"]
            assert first["currentDirection"] == current["d"]
            assert "availableRecoveryBeats" in first
            assert first["requiredRecoveryBeats"] == recovery


def test_dot_notes_are_parity_resets() -> None:
    previous = {"b": 1.0, "x": 0, "y": 0, "c": 0, "d": 1}
    dotted = {"b": 1.25, "x": 3, "y": 2, "c": 0, "d": 8}
    assert transition_finding(previous, dotted, 180.0, recovery_beats=0.25) is None


def test_arc_and_chain_exits_are_validated_as_poses() -> None:
    head = {"b": 4.0, "x": 0, "y": 1, "c": 0, "d": 1}
    legal_exit = {"b": 6.0, "x": 1, "y": 2, "c": 0, "d": 0}
    illegal_repeat = {"b": 4.125, "x": 0, "y": 1, "c": 0, "d": 1}
    assert transition_finding(head, legal_exit, 120.0, recovery_beats=0.22) is None
    finding = transition_finding(head, illegal_repeat, 120.0, recovery_beats=0.3)
    assert finding is not None
    assert finding["failedConstraint"] == "repeated_cut_without_reset"


def test_doubles_bombs_and_wall_cells_remain_distinct() -> None:
    red = {"b": 2.0, "x": 0, "y": 1, "c": 0, "d": 6}
    blue = {"b": 2.0, "x": 3, "y": 1, "c": 1, "d": 7}
    assert cross_hand_finding(red, blue) is None
    clap = {"b": 2.0, "x": 2, "y": 1, "c": 1, "d": 4}
    assert cross_hand_finding({"b": 2.0, "x": 0, "y": 1, "c": 0, "d": 5}, clap) is not None


@pytest.mark.parametrize("bpm", (60.0, 120.0, 260.0))
@pytest.mark.parametrize("recovery", (0.125, 0.25, 0.5, 1.0))
def test_recovery_windows_cover_eighth_through_full_at_lane_row_extremes(bpm: float, recovery: float) -> None:
    previous = {"b": 1.0, "x": 0, "y": 0, "c": 0, "d": 1}
    repeated = {"b": 1.0 + recovery, "x": 3, "y": 2, "c": 0, "d": 1}
    finding = transition_finding(previous, repeated, bpm, recovery_beats=recovery)
    assert finding is not None
    assert finding["constraint"] == "repeated_cut_without_reset"
    dotted = {"b": 1.0 + recovery, "x": 3, "y": 2, "c": 0, "d": 8}
    assert transition_finding(previous, dotted, bpm, recovery_beats=recovery) is None


def test_arc_and_chain_exits_feed_the_next_same_hand_cut() -> None:
    arc_head = {"b": 4.0, "x": 0, "y": 0, "c": 0, "d": 1}
    arc_tail = {"b": 6.0, "x": 1, "y": 2, "c": 0, "d": 0}
    chain_tail = {"b": 8.0, "x": 3, "y": 0, "c": 0, "d": 1, "_virtualTail": "chain"}
    next_cut = {"b": 9.0, "x": 3, "y": 1, "c": 0, "d": 0}
    assert transition_finding(arc_head, arc_tail, 120.0, recovery_beats=0.25) is None
    assert transition_finding(arc_tail, chain_tail, 180.0, recovery_beats=0.25) is None
    assert transition_finding(chain_tail, next_cut, 120.0, recovery_beats=0.25) is None
    illegal = {"b": 6.125, "x": 1, "y": 2, "c": 0, "d": 0}
    finding = transition_finding(arc_tail, illegal, 240.0, recovery_beats=0.5)
    assert finding is not None


def test_bomb_avoidance_and_wall_escape_cells() -> None:
    from choreography import CONFIGS, _hazard_cell_blocked, _solve_hazards, _wall_blocked, _wall_forces_body_damage

    notes = [
        {"b": 4.0, "x": 1, "y": 1, "c": 0, "d": 1},
        {"b": 4.0, "x": 2, "y": 1, "c": 1, "d": 1},
    ]
    assert _hazard_cell_blocked(notes, 4.0, 1, 1)
    assert _wall_blocked(notes, 3.5, 1.0, 1, 2)
    assert _wall_forces_body_damage({"b": 8.0, "d": 1.0, "x": 0, "y": 0, "w": 4, "h": 5})
    bombs, walls = _solve_hazards(notes, [{"beat": 0.0, "section": "intro"}, {"beat": 8.0, "section": "body"}], "ExpertPlus", CONFIGS["ExpertPlus"])
    for bomb in bombs:
        assert not _hazard_cell_blocked(notes, float(bomb["b"]), int(bomb["x"]), int(bomb["y"]))
    for wall in walls:
        assert not _wall_forces_body_damage(wall)
        remaining = {0, 1, 2, 3} - set(range(int(wall["x"]), int(wall["x"]) + int(wall["w"])))
        assert remaining, "wall escape lane is required"


def test_ciede2000_reference_pair() -> None:
    # Sharma et al. CIEDE2000 supplementary test pair 1.
    assert delta_e_2000((50, 2.6772, -79.7751), (50, 0, -82.7485)) == pytest.approx(2.0425, abs=0.0001)
    assert delta_e_2000(rgb_to_lab((1, 0, 0)), rgb_to_lab((0, 0, 1))) > 30


def test_info_dat_serializes_all_color_fields() -> None:
    red = {"r": 0.9, "g": 0.1, "b": 0.2, "a": 1.0}
    blue = {"r": 0.1, "g": 0.4, "b": 1.0, "a": 1.0}
    scheme = {
        "useOverride": True,
        "colorScheme": {
            "colorSchemeId": "Album",
            "saberAColor": red,
            "saberBColor": blue,
            "environmentColor0": red,
            "environmentColor1": blue,
            "obstaclesColor": red,
            "environmentColor0Boost": red,
            "environmentColor1Boost": blue,
        },
    }
    info = info_dat("Song", "Artist", "Mapper", 120, color_scheme=scheme)
    assert info["_colorSchemes"] == [scheme]
    assert all(item["_beatmapColorSchemeIdx"] == 0 for item in info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"])


def test_validator_reports_exact_flow_context(tmp_path: Path) -> None:
    difficulty = {
        "version": "3.3.0",
        "colorNotes": [
            {"b": 1, "x": 0, "y": 0, "c": 0, "d": 1},
            {"b": 1.25, "x": 0, "y": 1, "c": 0, "d": 1},
        ],
        "bombNotes": [],
        "obstacles": [],
        "sliders": [],
        "burstSliders": [],
    }
    info = info_dat("Song", "Artist", "Mapper", 120)
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"] = [info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"][3]]
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "ExpertStandard.dat").write_text(json.dumps(difficulty), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"audio")
    (tmp_path / "cover.png").write_bytes(b"cover")
    report = validate_package(tmp_path)
    issue = next(item for item in report.errors if item.code == "SAME_HAND_FLOW_CONFLICT")
    assert issue.details["difficulty"] == "Expert"
    assert issue.details["previousBeat"] == 1
    assert issue.details["currentBeat"] == 1.25
    assert issue.details["color"] == 0
    assert issue.details["recoverySeconds"] == pytest.approx(0.125)
    assert issue.details["failedConstraint"]


def _silent_audio(path: Path, suffix: str) -> Path:
    import soundfile as sf
    import numpy as np

    target = path.with_suffix(suffix)
    sf.write(target, np.zeros(22050, dtype=np.float32), 22050)
    return target


def _png_bytes() -> bytes:
    from generate_map import write_default_cover
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "cover.png"
        write_default_cover(path, "Art", 16)
        return path.read_bytes()


def test_embedded_artwork_wav_and_flac(tmp_path: Path) -> None:
    from artwork import extract_embedded_artwork
    from mutagen.id3 import APIC
    from mutagen.flac import FLAC, Picture

    png = _png_bytes()
    wav = _silent_audio(tmp_path / "song", ".wav")
    from mutagen.wave import WAVE

    wave_file = WAVE(wav)
    wave_file.add_tags()
    wave_file.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=png))
    wave_file.save()
    found = extract_embedded_artwork(wav, tmp_path / "from-wav")
    assert found["status"] == "found"
    assert found["frontCover"] is True

    flac = _silent_audio(tmp_path / "song", ".flac")
    audio = FLAC(flac)
    picture = Picture()
    picture.type = 3
    picture.mime = "image/png"
    picture.desc = "Cover"
    picture.data = png
    audio.add_picture(picture)
    audio.save()
    found = extract_embedded_artwork(flac, tmp_path / "from-flac")
    assert found["status"] == "found"
    assert Path(found["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def _ffmpeg_encode(source: Path, destination: Path, extra: list[str]) -> None:
    from beatforge_core import find_ffmpeg

    executable = find_ffmpeg()
    if not executable:
        pytest.skip("FFmpeg is required to encode MP3, M4A, and OGG artwork fixtures")
    result = subprocess.run(
        [executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), *extra, str(destination)],
        check=False,
        capture_output=True,
    )
    if result.returncode or not destination.is_file():
        pytest.skip(f"FFmpeg could not encode {destination.suffix}: {result.stderr[-400:]!r}")


def test_embedded_artwork_mp3_m4a_ogg(tmp_path: Path) -> None:
    import base64

    from artwork import extract_embedded_artwork
    from mutagen.flac import Picture
    from mutagen.id3 import APIC, ID3
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.oggvorbis import OggVorbis

    png = _png_bytes()
    wav = _silent_audio(tmp_path / "song", ".wav")

    mp3 = tmp_path / "song.mp3"
    _ffmpeg_encode(wav, mp3, ["-c:a", "libmp3lame", "-q:a", "7"])
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=png))
    tags.save(mp3)
    found = extract_embedded_artwork(mp3, tmp_path / "from-mp3")
    assert found["status"] == "found"
    assert found["container"] == "id3-apic"
    assert found["frontCover"] is True
    assert Path(found["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    m4a = tmp_path / "song.m4a"
    _ffmpeg_encode(wav, m4a, ["-c:a", "aac", "-b:a", "96k"])
    mp4_file = MP4(m4a)
    mp4_file["covr"] = [MP4Cover(png, imageformat=MP4Cover.FORMAT_PNG)]
    mp4_file.save()
    found = extract_embedded_artwork(m4a, tmp_path / "from-m4a")
    assert found["status"] == "found"
    assert found["container"] == "mp4-covr"
    assert Path(found["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    mp4 = tmp_path / "song.mp4"
    _ffmpeg_encode(wav, mp4, ["-c:a", "aac", "-b:a", "96k"])
    mp4_file = MP4(mp4)
    mp4_file["covr"] = [MP4Cover(png, imageformat=MP4Cover.FORMAT_PNG)]
    mp4_file.save()
    found = extract_embedded_artwork(mp4, tmp_path / "from-mp4")
    assert found["status"] == "found"
    assert found["container"] == "mp4-covr"

    ogg = tmp_path / "song.ogg"
    _ffmpeg_encode(wav, ogg, ["-c:a", "libvorbis", "-q:a", "3"])
    picture = Picture()
    picture.type = 3
    picture.mime = "image/png"
    picture.desc = "Cover"
    picture.data = png
    vorbis = OggVorbis(ogg)
    vorbis["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
    vorbis.save()
    found = extract_embedded_artwork(ogg, tmp_path / "from-ogg")
    assert found["status"] == "found"
    assert found["container"] == "ogg-picture"
    assert found["frontCover"] is True
    assert Path(found["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_musicbrainz_cover_gates_require_confidence_duration_front_and_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from artwork import lookup_release_cover, metadata_match_confidence, normalize_credit

    assert "featuring" in normalize_credit("Track (Official Audio) ft. Guest")
    assert metadata_match_confidence("Exact Title", "Exact Artist", "Exact Title", "Exact Artist") >= 0.92
    assert metadata_match_confidence("Exact Title", "Exact Artist", "No Match", "Somebody Else") < 0.92

    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(self, payload: dict, status: int = 200, content: bytes = b"") -> None:
            self._payload = payload
            self.status_code = status
            self.content = content or (b"\x89PNG\r\n\x1a\n" + b"0" * 24)
            self.headers = {"content-type": "image/png"}

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPError("catalog error")

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def get(self, url: str, params: dict | None = None) -> FakeResponse:
            captured["last_url"] = url
            captured["last_params"] = params
            if "recording" in url:
                assert params is not None
                assert "audio" not in str(params).casefold()
                return FakeResponse(
                    {
                        "recordings": [
                            {
                                "title": "Exact Title",
                                "length": 10_000,
                                "artist-credit": [{"name": "Exact Artist"}],
                                "releases": [{"id": "too-short", "status": "Official"}],
                            },
                            {
                                "id": "rec-ok",
                                "title": "Exact Title",
                                "length": 180_000,
                                "artist-credit": [{"name": "Exact Artist"}],
                                "releases": [
                                    {"id": "rel-ok", "status": "Official", "title": "Exact Title"},
                                    {"id": "bootleg", "status": "Bootleg", "title": "Exact Title"},
                                ],
                            },
                        ]
                    }
                )
            if "coverartarchive.org/release/rel-ok" in url:
                return FakeResponse(
                    {"images": [{"front": True, "approved": True, "image": "https://coverartarchive.org/fake.png"}]}
                )
            if url.endswith("fake.png"):
                return FakeResponse({}, content=b"\x89PNG\r\n\x1a\n" + b"1" * 40)
            return FakeResponse({"images": []}, status=404)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    rejected = lookup_release_cover(title="Exact Title", artist="Exact Artist", duration_seconds=15.0, destination_stem=tmp_path / "cover")
    assert rejected["status"] == "missing"
    accepted = lookup_release_cover(title="Exact Title", artist="Exact Artist", duration_seconds=180.0, destination_stem=tmp_path / "cover")
    assert accepted["status"] == "needs_approval"
    assert accepted["front"] is True
    assert accepted["matchConfidence"] >= 0.92
    assert accepted["durationDeltaSeconds"] <= 2.0
    assert "audio" not in str(captured["last_params"]).casefold()


@pytest.mark.network
def test_live_musicbrainz_search_is_metadata_only() -> None:
    """Live catalog probe. Sends title/artist query text only. Never uploads audio."""

    import httpx

    from artwork import USER_AGENT

    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
            response = client.get(
                "https://musicbrainz.org/ws/2/recording/",
                params={"query": 'recording:"Magic" AND artist:"Camellia"', "fmt": "json", "limit": 1},
            )
    except httpx.HTTPError as error:
        pytest.skip(f"MusicBrainz unreachable: {type(error).__name__}")
    if response.status_code != 200:
        pytest.skip(f"MusicBrainz returned HTTP {response.status_code}")
    payload = response.json()
    assert "recordings" in payload


def test_mood_palette_uses_metadata_only_and_stops_at_needs_palette(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from artwork import palette_from_rgb
    from beatforge import providers

    identical = palette_from_rgb((0.5, 0.5, 0.5), (0.51, 0.5, 0.5), source="test", rationale="too close")
    assert identical["status"] == "needs_palette"
    readable = palette_from_rgb((0.92, 0.18, 0.12), (0.12, 0.42, 0.95), source="codex-mood-analysis", rationale="warm left, cool right")
    assert readable["status"] == "needs_approval"
    assert readable["colorScheme"]["colorScheme"]["saberAColor"]["r"] == pytest.approx(0.92)
    assert "environmentColor0Boost" in readable["colorScheme"]["colorScheme"]

    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "id": "run-palette",
                "output_text": '{"left":[0.92,0.18,0.12],"right":[0.12,0.42,0.95],"rationale":"metadata only"}',
            }

    def fake_post(url: str, headers: dict | None = None, json: dict | None = None, timeout: float = 0) -> FakeResponse:
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    job = tmp_path / "job"
    analysis_dir = job / "map" / "_beatforge"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "analysis.json").write_text(
        json.dumps({"bpm": 128.0, "durationSeconds": 210.0, "tempoRegions": [], "events": [{"strength": 0.4}]}),
        encoding="utf-8",
    )
    (job / "status.json").write_text(json.dumps({"title": "Exact Title", "artist": "Exact Artist"}), encoding="utf-8")
    monkeypatch.setattr(providers, "get_secret", lambda _name: "provider-secret")
    monkeypatch.setattr(httpx, "post", fake_post)
    candidate = providers.suggest_mood_palette(
        job_dir=job,
        provider="codex",
        model="studio-model",
        metadata_approved=True,
    )
    assert candidate["status"] == "needs_approval"
    transferred = json.dumps(candidate["transferred"])
    assert candidate["transferred"]["title"] == "Exact Title"
    assert "audio" not in transferred.casefold()
    prompt = str(captured["json"]["input"])
    assert "Exact Title" in prompt
    assert "audio" not in prompt.casefold()
    assert candidate["palette"]["assignment"] == "provider-mood-palette"
    assert candidate["approvalRequired"] is True
    assert (analysis_dir / "ai_palette_cover.png").is_file()
    provenance = json.loads((analysis_dir / "palette_candidate.json").read_text(encoding="utf-8"))
    assert provenance["provider"] == "codex"
    assert provenance["model"] == "studio-model"


def test_validate_package_cannot_claim_release_candidate_without_headset_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEATFORGE_AI_RELEASE_ROUTE", raising=False)
    difficulty = {
        "version": "3.3.0",
        "colorNotes": [
            {"b": 1, "x": 0, "y": 1, "c": 0, "d": 1},
            {"b": 2, "x": 3, "y": 1, "c": 1, "d": 1},
        ],
        "bombNotes": [],
        "obstacles": [],
        "sliders": [],
        "burstSliders": [],
    }
    info = info_dat("Song", "Artist", "Mapper", 120)
    (tmp_path / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    for name in ("Easy", "Normal", "Hard", "Expert", "ExpertPlus"):
        (tmp_path / f"{name}Standard.dat").write_text(json.dumps(difficulty), encoding="utf-8")
    (tmp_path / "song.ogg").write_bytes(b"audio")
    (tmp_path / "cover.png").write_bytes(b"cover")
    reports = tmp_path / "_beatforge"
    reports.mkdir()
    (reports / "analysis.json").write_text(json.dumps({"status": "timing_verified", "clickTrackEvidence": {"checkpoints": [{"label": "start"}, {"label": "middle"}, {"label": "end"}]}}), encoding="utf-8")
    (reports / "provenance.json").write_text(
        json.dumps({"releaseGate": {"fullSpeedVrPlaytest": False, "freshSightRead": False}}),
        encoding="utf-8",
    )
    unsigned = validate_package(tmp_path)
    assert unsigned.status != "release_candidate"
    assert not any(item.code == "TIMING_UNVERIFIED" for item in unsigned.errors)
    needs_timing = tmp_path / "_beatforge" / "analysis.json"
    needs_timing.write_text(json.dumps({"status": "needs_anchors", "clickTrackEvidence": {"checkpoints": [{"label": "start"}, {"label": "middle"}, {"label": "end"}]}}), encoding="utf-8")
    unverified = validate_package(tmp_path)
    assert not unverified.errors
    assert any(item.code == "TIMING_UNVERIFIED" for item in unverified.warnings)
    signed = validate_package(tmp_path, vr_playtest=True, sight_read=True)
    if not signed.errors:
        assert signed.status == "playtest_candidate"
        assert signed.metrics["releaseGate"]["vrPlaytest"] is True
        assert signed.metrics["releaseGate"]["freshSightRead"] is True
        assert signed.metrics["releaseGate"]["aiReleaseRoute"] is False


def test_approved_palette_manifest_stamps_nested_palette_status(tmp_path: Path) -> None:
    from generate_map import _load_approved_palette

    cover = tmp_path / "cover.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 24)
    scheme = {
        "useOverride": True,
        "colorScheme": {
            "colorSchemeId": "BeatForge Cover Palette",
            "saberAColor": {"r": 0.9, "g": 0.1, "b": 0.1, "a": 1.0},
            "saberBColor": {"r": 0.1, "g": 0.2, "b": 0.9, "a": 1.0},
            "environmentColor0": {"r": 0.8, "g": 0.1, "b": 0.1, "a": 1.0},
            "environmentColor1": {"r": 0.1, "g": 0.2, "b": 0.8, "a": 1.0},
            "obstaclesColor": {"r": 0.4, "g": 0.4, "b": 0.4, "a": 1.0},
            "environmentColor0Boost": {"r": 1.0, "g": 0.2, "b": 0.2, "a": 1.0},
            "environmentColor1Boost": {"r": 0.2, "g": 0.3, "b": 1.0, "a": 1.0},
        },
    }
    manifest = tmp_path / "palette.json"
    manifest.write_text(
        json.dumps(
            {
                "status": "approved",
                "artwork": {"path": str(cover)},
                "palette": {"status": "needs_approval", "colorScheme": scheme},
            }
        ),
        encoding="utf-8",
    )
    _cover, loaded_scheme, payload = _load_approved_palette(manifest)
    assert payload["palette"]["status"] == "approved"
    assert loaded_scheme["useOverride"] is True


def test_demucs_missing_install_does_not_change_timing_gates() -> None:
    import importlib.util

    analyzer = (Path(__file__).resolve().parents[1] / "skills" / "beat-saber-mapping" / "scripts" / "analyze_audio.py").read_text(encoding="utf-8")
    assert "def external_agreement_passes" in analyzer
    assert 'float(agreement["medianMs"]) <= 10.0' in analyzer
    assert 'float(agreement["p95Ms"]) <= 20.0' in analyzer
    assert 'float(agreement["maximumCumulativeDriftMs"]) <= 20.0' in analyzer
    spec = importlib.util.find_spec("demucs")
    if spec is None:
        assert "Demucs stem separation was required but is not installed" in analyzer
    else:
        assert spec.name == "demucs"


@pytest.mark.hardware
def test_physical_headset_playtest_is_not_executable_in_this_suite() -> None:
    """Headset clears are a human gate. This marker keeps them out of unit CI."""

    pytest.skip("no headset session is attached; software cannot play or clear a map")

