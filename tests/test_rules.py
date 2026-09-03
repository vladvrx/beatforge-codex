"""Hard-rule placement, shared skeleton, critic, and installer tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import beatforge.install_elevated as install_elevated
from beatforge.chart import (
    AnalysisResult,
    Chart,
    DifficultyChart,
    Note,
    Section,
    SustainRegion,
)
from beatforge.critic import fingerprint_diff, run_critic
from beatforge.install import install_map
from beatforge.place import place_difficulties


def make_analysis(bpm: float = 120.0, beats_n: int = 64) -> AnalysisResult:
    beat_dur = 60.0 / bpm
    beats = [i * beat_dur for i in range(beats_n)]
    raw: list[tuple[float, float]] = []
    for i in range(8, beats_n):
        s = 0.9 if i % 8 in (0, 4) else 0.55 + 0.001 * i
        raw.append((i * beat_dur, min(s, 0.95)))
    for i in range(32, beats_n):
        raw.append(((i + 0.5) * beat_dur, 0.6 + 0.001 * i))
    raw.sort(key=lambda p: p[0])
    onset_times = [t for t, _ in raw]
    strengths = [s for _, s in raw]
    beat_energy = [0.4] * 8 + [0.9] * (beats_n - 8)
    sections = [
        Section(t=0.0, label="intro", energy=0.4),
        Section(t=8 * beat_dur, label="verse", energy=0.6),
        Section(t=24 * beat_dur, label="drop", energy=0.9),
        Section(t=48 * beat_dur, label="outro", energy=0.45),
    ]
    return AnalysisResult(
        bpm=bpm,
        duration=beats_n * beat_dur,
        beats=beats,
        onset_times=onset_times,
        onset_strengths=strengths,
        beat_energy=beat_energy,
        sections=sections,
    )


def test_no_face_plants_or_reset_chains_flow() -> None:
    analysis = make_analysis()
    diffs = place_difficulties(analysis, ["ExpertPlus"], style="flow", seed=3)
    fp = fingerprint_diff(diffs["ExpertPlus"], analysis.bpm, analysis.duration)
    assert fp["face_plants"] == 0
    assert fp["max_reset_chain"] <= 2
    assert fp["first_note_t"] == pytest.approx(analysis.beats[0], abs=0.02)
    assert fp["stacked_cells"] == 0


def test_full_spread_shares_skeleton() -> None:
    analysis = make_analysis()
    diffs = place_difficulties(
        analysis, ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"], style="flow", seed=7
    )
    easy_beats = {round(n.beat, 3) for n in diffs["Easy"].notes}
    exp_beats = {round(n.beat, 3) for n in diffs["ExpertPlus"].notes}
    assert easy_beats, "Easy chart empty"
    missing = easy_beats - exp_beats
    assert not missing, f"Easy notes not nested in ExpertPlus: {sorted(missing)[:5]}"
    # Density ladder must hold: Easy strictly thinner than Expert+
    assert len(easy_beats) < len(exp_beats)


def test_full_spread_stays_nested_when_arcs_replace_hits() -> None:
    analysis = make_analysis()
    analysis.sustains = [SustainRegion(t=14.0, end_t=16.0, strength=0.9)]
    diffs = place_difficulties(
        analysis,
        ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"],
        style="flow",
        seed=7,
    )

    ordered = ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
    for lower, higher in zip(ordered, ordered[1:]):
        lower_beats = {round(n.beat, 3) for n in diffs[lower].notes}
        higher_beats = {round(n.beat, 3) for n in diffs[higher].notes}
        assert lower_beats <= higher_beats
    assert any(diffs[name].arcs for name in ordered[1:])


def test_bombs_are_drop_punctuation_with_clearance() -> None:
    analysis = make_analysis()
    diffs = place_difficulties(analysis, ["ExpertPlus"], style="flow", seed=11)
    diff = diffs["ExpertPlus"]
    assert diff.bombs, "expected drop punctuation bombs"
    drop_secs = [s for s in analysis.sections if s.label == "drop"]
    beat_dur = 60.0 / analysis.bpm
    for b in diff.bombs:
        assert b.t >= 3.0
        in_drop = any(
            s.t <= b.t <= s.t + (24 * beat_dur) and s.label == "drop" for s in drop_secs
        )
        assert in_drop, f"bomb at t={b.t} outside drop sections"
        for n in diff.notes:
            db = abs(n.beat - b.beat)
            assert not (
                db < 1.25 and (n.lane, n.row) == (b.lane, b.row)
            ), "bomb shares a cell with a note swing path"


def test_walls_avoid_notes() -> None:
    analysis = make_analysis()
    diffs = place_difficulties(analysis, ["ExpertPlus"], style="flow", seed=13)
    diff = diffs["ExpertPlus"]
    for w in diff.obstacles:
        lanes = set(range(w.lane, w.lane + w.width))
        w0, w1 = w.beat - 0.5, w.beat + w.duration_beats + 0.5
        for n in diff.notes:
            if w0 <= n.beat <= w1 and n.lane in lanes and (w.height >= 2 or n.row == 0):
                raise AssertionError(f"wall clips note at beat {n.beat}")


def test_critic_flags_bad_chart() -> None:
    bad = DifficultyChart(
        notes=[
            Note(t=10.0, beat=20.0, lane=1, row=0, hand="left", cut="right"),
            Note(t=10.0, beat=20.0, lane=2, row=0, hand="right", cut="left"),
            Note(t=12.0, beat=24.0, lane=1, row=0, hand="left", cut="right"),
            Note(t=14.0, beat=28.0, lane=1, row=0, hand="left", cut="right"),
            Note(t=16.0, beat=32.0, lane=1, row=0, hand="left", cut="right"),
            Note(t=18.0, beat=36.0, lane=2, row=0, hand="right", cut="down"),
        ],
    )
    chart = Chart(
        title="bad",
        artist="x",
        bpm=120.0,
        duration=40.0,
        beats=[],
        sections=[Section(t=0, label="body", energy=0.5)],
        difficulties={"Expert": bad},
        style="flow",
    )
    report = run_critic(chart)
    failed = {c["name"] for c in report["difficulties"]["Expert"]["checks"] if not c["passed"]}
    assert "no_face_plants" in failed
    assert "chain_ceiling" in failed
    assert "opening_coverage" in failed
    assert report["verdict"] == "needs-work"


def test_critic_passes_generated_chart() -> None:
    analysis = make_analysis()
    diffs = place_difficulties(
        analysis, ["Expert"], style="flow", seed=21
    )
    chart = Chart(
        title="gen",
        artist="x",
        bpm=analysis.bpm,
        duration=analysis.duration,
        beats=analysis.beats,
        sections=analysis.sections,
        difficulties={"Expert": diffs["Expert"]},
        style="flow",
    )
    report = run_critic(chart)
    d = report["difficulties"]["Expert"]
    failed = [c for c in d["checks"] if not c["passed"]]
    assert d["score"] >= 0.85, f"failed: {[c['name'] for c in failed]}"
    assert report["verdict"] in ("clean", "acceptable")
    assert d["metrics"]["hand_alt_ratio"] >= 0.62
    assert d["metrics"]["max_color_streak"] <= 3
    assert d["metrics"]["first_note_t"] == pytest.approx(
        analysis.beats[0], abs=0.02
    )
    assert d["metrics"]["row_hist"][0] <= 0.78
    assert d["metrics"]["row_hist"][2] >= 0.02


def test_install_map_extracts_into_custom_levels(tmp_path: Path) -> None:
    levels = tmp_path / "CustomLevels"
    levels.mkdir()
    zpath = tmp_path / "map.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("Info.dat", json.dumps({"_version": "2.0.0"}))
        zf.writestr("Expert.dat", "{}")
        zf.writestr("song.ogg", b"\x00\x01")
    dest = install_map(zpath, levels, title="My Song!")
    assert dest.is_dir()
    assert dest.name == "My_Song"
    assert (dest / "Info.dat").is_file()
    assert (dest / "song.ogg").read_bytes() == b"\x00\x01"

    # A second install of the same title must not overwrite the first.
    dest2 = install_map(zpath, levels, title="My Song!")
    assert dest2 != dest
    assert (dest / "Info.dat").is_file()
    existing = levels / "Pacific_Coast_Highway"
    existing.mkdir()
    (existing / "Info.dat").write_text("{}", encoding="utf-8")
    dest3 = install_map(zpath, levels, title="Pacific Coast Highway")
    assert dest3.name != "Pacific_Coast_Highway"
    assert dest3.name != "Pacific_Coast_Highway_8"
    assert (existing / "Info.dat").read_text(encoding="utf-8") == "{}"

    empty_protected = levels / "Pacific_Coast_Highway_8"
    empty_protected.mkdir()
    dest4 = install_map(zpath, levels, title="Pacific Coast Highway 8")
    assert dest4.name != "Pacific_Coast_Highway_8"
    assert dest4.parent == levels


def test_restricted_elevated_helper_installs_generated_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "a1b2c3d4e5f6"
    jobs = tmp_path / "jobs"
    job = jobs / job_id
    job.mkdir(parents=True)
    (job / "status.json").write_text(
        json.dumps({"title": "Protected Song"}), encoding="utf-8"
    )
    with zipfile.ZipFile(job / "map.zip", "w") as zf:
        zf.writestr("Info.dat", "{}")
        zf.writestr("Expert.dat", "{}")

    levels = tmp_path / "CustomLevels"
    levels.mkdir()
    monkeypatch.setenv("BEATSABER_CUSTOM_LEVELS", str(levels))
    monkeypatch.setattr(install_elevated, "JOBS_DIR", jobs)

    assert install_elevated.main(["--job-id", job_id]) == 0
    result = json.loads((job / "install-result.json").read_text(encoding="utf-8"))
    assert result["installed"] is True
    assert (Path(result["path"]) / "Info.dat").is_file()
