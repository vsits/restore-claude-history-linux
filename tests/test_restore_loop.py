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


def test_restore_no_jsonl_anywhere_errors(tmp_path):
    empty = tmp_path / "s1" / ".claude" / "projects"
    empty.mkdir(parents=True)
    registry = [LocalDirBackend("local", roots=[tmp_path / "s1"])]
    with pytest.raises(SystemExit):
        run_restore(registry, Options(backend="auto", dest=tmp_path / "dest"))
