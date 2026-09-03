#!/usr/bin/env python3
"""Property checks that occupancy is one contract across SAT, realize, and QA."""

from __future__ import annotations

from choreography import CONFIGS, _default_tail, _hold_occupied_until, _hold_tail_beat
from safety_contract import (
    MIN_CHAIN_DURATION_BEATS,
    POST_HOLD_SAME_HAND_BEATS,
    RECOVERY_BEATS,
    hold_span_beats,
    occupied_until,
    occupied_until_from_intent,
    same_hand_after_hold_too_soon,
)
from validate_map import RECOVERY_BEATS as VALIDATE_RECOVERY


def test_recovery_tables_are_shared() -> None:
    assert RECOVERY_BEATS == VALIDATE_RECOVERY
    for name, config in CONFIGS.items():
        assert config.recovery_beats == RECOVERY_BEATS[name]


def test_short_chain_span_is_independent_of_song_beat() -> None:
    for head in (0.0, 8.0, 64.0, 400.0, 598.0):
        intent = {"beat": head, "duration": 0.125, "kind": "chain"}
        assert _hold_tail_beat(intent) == head + MIN_CHAIN_DURATION_BEATS
        tail = _default_tail({"b": head, "x": 2, "y": 1, "c": 1, "d": 1}, 1, "chain", 0.125, 0)
        assert abs(float(tail["b"]) - (head + MIN_CHAIN_DURATION_BEATS)) < 1e-9
        for recovery in RECOVERY_BEATS.values():
            until = occupied_until_from_intent(intent, recovery)
            assert until == occupied_until(head + MIN_CHAIN_DURATION_BEATS, recovery)
            assert _hold_occupied_until(intent, recovery) == until
    assert same_hand_after_hold_too_soon(POST_HOLD_SAME_HAND_BEATS)
    assert same_hand_after_hold_too_soon(0.25)
    assert not same_hand_after_hold_too_soon(POST_HOLD_SAME_HAND_BEATS + 0.001)


def test_hold_span_uses_musical_duration_when_longer_than_chain_floor() -> None:
    assert hold_span_beats("chain", 1.0) == 1.0
    assert hold_span_beats("arc", 0.125) == 0.125
    assert hold_span_beats("note", 0.5) == 0.5
