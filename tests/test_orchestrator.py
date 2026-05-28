"""Layer 1: orchestrator selection + overlap-resolution tests.

Uses LocalDirBackend fakes that yield pre-constructed DiscoveredSnapshots, so
no filesystem state or subprocess is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backends._local_dir import LocalDirBackend
from backends.base import DiscoveredSnapshot
from restore_claude_history import (
    Options,
    choose_snapshots,
    resolve_overlaps,
    select_auto,
)


def snap(name: str, path: str) -> DiscoveredSnapshot:
    return DiscoveredSnapshot(name=name, data_root=Path(path),
                              needs_mount=False, backend_state={})


# -------- select_auto rules --------


def test_auto_zero_match_errors():
    with pytest.raises(SystemExit):
        select_auto({"zfs": [], "btrfs": []})


def test_auto_single_match_selected():
    name, snaps = select_auto({"zfs": [snap("z", "/a")], "btrfs": []})
    assert name == "zfs"
    assert len(snaps) == 1


def test_auto_multi_match_is_ambiguous():
    with pytest.raises(SystemExit):
        select_auto({"zfs": [snap("z", "/a")], "btrfs": [snap("b", "/b")]})


# -------- overlap resolution --------


def test_overlap_owner_prunes_peer_duplicate():
    discovered = {
        "timeshift": [snap("ts", "/mnt/snap/@home")],
        "btrfs": [snap("bt", "/mnt/snap/@home"), snap("bt2", "/mnt/snap/@other")],
    }
    out = resolve_overlaps(discovered, [("timeshift", "btrfs")])
    btrfs_paths = {str(s.data_root) for s in out["btrfs"]}
    assert btrfs_paths == {"/mnt/snap/@other"}  # duplicate pruned, unique kept
    assert len(out["timeshift"]) == 1


def test_overlap_empty_owner_prunes_nothing():
    # Round 3 false-negative guard: owner returned nothing -> peer untouched.
    discovered = {
        "timeshift": [],
        "btrfs": [snap("bt", "/mnt/snap/@home")],
    }
    out = resolve_overlaps(discovered, [("timeshift", "btrfs")])
    assert len(out["btrfs"]) == 1


def test_overlap_partial_owner_prunes_only_matched():
    # Round 4 false-negative guard: owner found A and B but missed C under the
    # same namespace -> only A and B pruned from peer; C survives.
    discovered = {
        "timeshift": [snap("a", "/snaps/A"), snap("b", "/snaps/B")],
        "btrfs": [snap("a", "/snaps/A"), snap("b", "/snaps/B"), snap("c", "/snaps/C")],
    }
    out = resolve_overlaps(discovered, [("timeshift", "btrfs")])
    assert {str(s.data_root) for s in out["btrfs"]} == {"/snaps/C"}


def test_overlap_realpath_canonicalization(tmp_path):
    # A symlinked entry point to the same snapshot path is deduplicated.
    real = tmp_path / "real_snap"
    real.mkdir()
    link = tmp_path / "link_snap"
    link.symlink_to(real)
    discovered = {
        "timeshift": [snap("ts", str(real))],
        "btrfs": [snap("bt", str(link))],
    }
    out = resolve_overlaps(discovered, [("timeshift", "btrfs")])
    assert out["btrfs"] == []


# -------- choose_snapshots: auto vs explicit --------


def test_choose_auto_runs_overlap_and_selects():
    registry = [
        LocalDirBackend("timeshift", snapshots=[snap("ts", "/snaps/A")]),
        LocalDirBackend("btrfs", snapshots=[snap("bt", "/snaps/A")]),
    ]
    backend, snaps = choose_snapshots(registry, Options(backend="auto"))
    # btrfs duplicate pruned -> only timeshift has candidates -> auto picks it.
    assert backend.name == "timeshift"
    assert len(snaps) == 1


def test_choose_explicit_bypasses_overlap():
    # With --backend btrfs, overlap resolution is skipped: btrfs sees its own
    # full inventory even though timeshift would have claimed the path in auto.
    registry = [
        LocalDirBackend("timeshift", snapshots=[snap("ts", "/snaps/A")]),
        LocalDirBackend("btrfs", snapshots=[snap("bt", "/snaps/A")]),
    ]
    backend, snaps = choose_snapshots(registry, Options(backend="btrfs"))
    assert backend.name == "btrfs"
    assert len(snaps) == 1


def test_choose_explicit_unavailable_errors():
    registry = [LocalDirBackend("btrfs", snapshots=[], available=False)]
    with pytest.raises(SystemExit):
        choose_snapshots(registry, Options(backend="btrfs"))


def test_choose_explicit_unknown_errors():
    registry = [LocalDirBackend("zfs", snapshots=[snap("z", "/a")])]
    with pytest.raises(SystemExit):
        choose_snapshots(registry, Options(backend="btrfs"))


def test_choose_explicit_no_snapshots_errors():
    registry = [LocalDirBackend("zfs", snapshots=[])]
    with pytest.raises(SystemExit):
        choose_snapshots(registry, Options(backend="zfs"))
