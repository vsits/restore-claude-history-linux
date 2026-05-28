"""Layer 1: pure-logic tests for pick_largest() and JsonlEntry."""

from __future__ import annotations

from pathlib import Path

from restore_claude_history import JsonlEntry, pick_largest


def _e(project: str, filename: str, size: int) -> JsonlEntry:
    return JsonlEntry(project=project, filename=filename, size=size,
                      src=Path(f"/snap/{project}/{filename}"))


def test_empty():
    assert pick_largest([]) == {}


def test_keeps_largest_per_key():
    entries = [
        _e("proj", "a.jsonl", 100),
        _e("proj", "a.jsonl", 500),
        _e("proj", "a.jsonl", 300),
    ]
    best = pick_largest(entries)
    assert set(best) == {("proj", "a.jsonl")}
    assert best[("proj", "a.jsonl")].size == 500


def test_distinct_keys_are_independent():
    entries = [
        _e("p1", "a.jsonl", 10),
        _e("p1", "b.jsonl", 20),
        _e("p2", "a.jsonl", 30),
    ]
    best = pick_largest(entries)
    assert {k: v.size for k, v in best.items()} == {
        ("p1", "a.jsonl"): 10,
        ("p1", "b.jsonl"): 20,
        ("p2", "a.jsonl"): 30,
    }


def test_first_wins_on_tie():
    # On equal size the incumbent stays (strict > comparison).
    first = _e("proj", "a.jsonl", 100)
    second = _e("proj", "a.jsonl", 100)
    best = pick_largest([first, second])
    assert best[("proj", "a.jsonl")].src == first.src
