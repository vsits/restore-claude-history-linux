"""Layer 2: end-to-end restore loop over tempdir 'snapshots' via LocalDirBackend.

No real snapshot tooling; runs unprivileged. Each snapshot is a tempdir whose
``.claude/projects/<proj>/*.jsonl`` tree stands in for a snapshot of $HOME.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backends._local_dir import LocalDirBackend
from restore_claude_history import (
    Options,
    index_projects,
    locate_projects_dir,
    run_restore,
)

PROJECT = "-home-user-projects-foo"


def make_snapshot(root: Path, files: dict[str, tuple[bytes, float]]) -> Path:
    """Build a fake snapshot under `root`. files maps name -> (content, mtime)."""
    proj = root / ".claude" / "projects" / PROJECT
    proj.mkdir(parents=True)
    for name, (content, mtime) in files.items():
        p = proj / name
        p.write_bytes(content)
        os.utime(p, (mtime, mtime))
    return root


# -------- locate_projects_dir layout handling --------


def test_locate_projects_dir_at_home_root(tmp_path):
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    assert locate_projects_dir(tmp_path, tmp_path) == tmp_path / ".claude" / "projects"


def test_locate_projects_dir_snapshot_of_root(tmp_path):
    # data_root is a snapshot of '/', projects live under home subpath.
    home = Path("/home/user")
    target = tmp_path / "home" / "user" / ".claude" / "projects"
    target.mkdir(parents=True)
    assert locate_projects_dir(tmp_path, home) == target


def test_locate_projects_dir_missing(tmp_path):
    assert locate_projects_dir(tmp_path, Path("/home/user")) is None


def test_locate_projects_dir_follows_symlinked_home(tmp_path):
    # Round-1 MEDIUM fix: home is a symlink; snapshot exposes the resolved path.
    real_home = tmp_path / "mnt" / "data" / "alice"
    link_home = tmp_path / "home" / "alice"
    link_home.parent.mkdir(parents=True)
    real_home.mkdir(parents=True)
    link_home.symlink_to(real_home)
    # data_root is a snapshot of /mnt/data -> contains alice/.claude/projects
    data_root = tmp_path / "snap_of_mnt_data"
    target = data_root / "alice" / ".claude" / "projects"
    target.mkdir(parents=True)
    assert locate_projects_dir(data_root, link_home) == target


# -------- index --------


def test_index_projects_filters_by_project(tmp_path):
    proj_a = tmp_path / ".claude" / "projects" / "-p-a"
    proj_b = tmp_path / ".claude" / "projects" / "-p-b"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)
    (proj_a / "x.jsonl").write_text("a")
    (proj_b / "y.jsonl").write_text("b")
    projects = tmp_path / ".claude" / "projects"
    only_a = index_projects(projects, "-p-a")
    assert {e.project for e in only_a} == {"-p-a"}


# -------- restore loop --------


def test_restore_picks_largest_and_preserves_mtime(tmp_path):
    old_mtime = 1_600_000_000.0
    new_mtime = 1_600_500_000.0
    s1 = make_snapshot(tmp_path / "s1", {"a.jsonl": (b"short", old_mtime)})
    s2 = make_snapshot(tmp_path / "s2", {"a.jsonl": (b"a much longer body", new_mtime)})

    dest = tmp_path / "dest"
    registry = [LocalDirBackend("local", roots=[s1, s2])]
    rc = run_restore(registry, Options(backend="auto", dest=dest))
    assert rc == 0

    restored = dest / PROJECT / "a.jsonl"
    assert restored.read_bytes() == b"a much longer body"  # largest won
    assert abs(restored.stat().st_mtime - new_mtime) < 1.0  # mtime preserved


def test_restore_dry_run_writes_nothing(tmp_path):
    s1 = make_snapshot(tmp_path / "s1", {"a.jsonl": (b"hello", 1_600_000_000.0)})
    dest = tmp_path / "dest"
    registry = [LocalDirBackend("local", roots=[s1])]
    rc = run_restore(registry, Options(backend="auto", dry_run=True, dest=dest))
    assert rc == 0
    assert not (dest / PROJECT / "a.jsonl").exists()


def test_restore_skips_when_ondisk_is_larger(tmp_path):
    s1 = make_snapshot(tmp_path / "s1", {"a.jsonl": (b"snap", 1_600_000_000.0)})
    dest = tmp_path / "dest"
    live = dest / PROJECT
    live.mkdir(parents=True)
    (live / "a.jsonl").write_text("on-disk is already bigger")
    registry = [LocalDirBackend("local", roots=[s1])]
    rc = run_restore(registry, Options(backend="auto", dest=dest))
    assert rc == 0
    assert (live / "a.jsonl").read_text() == "on-disk is already bigger"


def _add_subdir(root: Path, sub_name: str, files: dict[str, bytes]) -> None:
    sub = root / ".claude" / "projects" / PROJECT / sub_name
    sub.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (sub / name).write_bytes(content)


def test_restore_subdirs_picks_largest_subtree_not_lexical(tmp_path):
    # Round-1 MEDIUM fix: the larger (more complete) subdir wins regardless of
    # snapshot-name ordering. Snapshot A (processed first) has the SMALL copy.
    s1 = make_snapshot(tmp_path / "s1", {"a.jsonl": (b"x", 1_600_000_000.0)})
    s2 = make_snapshot(tmp_path / "s2", {"a.jsonl": (b"x", 1_600_000_000.0)})
    _add_subdir(s1, "subagents", {"agent.json": b"small"})
    _add_subdir(s2, "subagents", {"agent.json": b"a much larger agent body here"})

    dest = tmp_path / "dest"
    registry = [LocalDirBackend("local", roots=[s1, s2])]
    rc = run_restore(registry, Options(backend="auto", dest=dest))
    assert rc == 0
    restored = dest / PROJECT / "subagents" / "agent.json"
    assert restored.read_bytes() == b"a much larger agent body here"


def test_restore_subdirs_largest_wins_when_newer_subtree_is_smaller(tmp_path):
    """v1.1 regression: jsonl path moved to first-writer-wins on created_at,
    but subdir path MUST keep largest-subtree selection. Construct a case
    where the newer snapshot's subdir is smaller than the older one's; if
    subdir restore had been accidentally switched to first-writer-wins it
    would pick the newer (smaller) one.

    LocalDirBackend assigns created_at by enumeration order: s1 → 2026-01-01
    00:00 UTC, s2 → 2026-01-01 01:00 UTC. Newest-first iteration visits s2
    first. We put the SMALL subdir on s2 (newest) and the LARGE one on s1
    (oldest). If the largest-subtree rule still holds, the large one wins.
    """
    s1 = make_snapshot(tmp_path / "s1", {"a.jsonl": (b"x", 1_600_000_000.0)})
    s2 = make_snapshot(tmp_path / "s2", {"a.jsonl": (b"x", 1_600_000_000.0)})
    _add_subdir(s1, "subagents", {"agent.json": b"the full historical agent body - larger"})
    _add_subdir(s2, "subagents", {"agent.json": b"truncated"})

    dest = tmp_path / "dest"
    registry = [LocalDirBackend("local", roots=[s1, s2])]
    rc = run_restore(registry, Options(backend="auto", dest=dest))
    assert rc == 0
    restored = dest / PROJECT / "subagents" / "agent.json"
    assert restored.read_bytes() == b"the full historical agent body - larger"


def test_restore_subdirs_memory_gated_by_flag(tmp_path):
    s1 = make_snapshot(tmp_path / "s1", {"a.jsonl": (b"x", 1_600_000_000.0)})
    _add_subdir(s1, "memory", {"note.md": b"remembered"})
    dest = tmp_path / "dest"

    # Without --include-memory, memory/ is skipped.
    rc = run_restore([LocalDirBackend("local", roots=[s1])],
                     Options(backend="auto", dest=dest))
    assert rc == 0
    assert not (dest / PROJECT / "memory").exists()

    # With it, memory/ is restored.
    dest2 = tmp_path / "dest2"
    rc = run_restore([LocalDirBackend("local", roots=[s1])],
                     Options(backend="auto", include_memory=True, dest=dest2))
    assert rc == 0
    assert (dest2 / PROJECT / "memory" / "note.md").read_bytes() == b"remembered"


def test_restore_no_jsonl_anywhere_errors(tmp_path):
    empty = tmp_path / "s1" / ".claude" / "projects"
    empty.mkdir(parents=True)
    registry = [LocalDirBackend("local", roots=[tmp_path / "s1"])]
    with pytest.raises(SystemExit):
        run_restore(registry, Options(backend="auto", dest=tmp_path / "dest"))
