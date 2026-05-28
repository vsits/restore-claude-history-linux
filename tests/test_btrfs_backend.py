"""Layer 1: Btrfs backend is_available() / discover() / parsing, mocked."""

from __future__ import annotations

import subprocess

import backends.btrfs as btrfs_mod
from backends._mountinfo import Mount
from backends.btrfs import BtrfsBackend, _parse_subvol_line


def _cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["btrfs"], returncode=returncode,
                                       stdout=stdout, stderr="")


def _line(sid: str, path: str, otime: str = "2026-05-28 00:00:01") -> str:
    return f"ID {sid} gen 30 cgen 25 top level 5 otime {otime} path {path}"


# -------- _parse_subvol_line --------


def test_parse_normal_line():
    out = _parse_subvol_line(_line("256", "@/.snapshots/1/snapshot"))
    assert out == {"id": "256", "path": "@/.snapshots/1/snapshot",
                   "otime": "2026-05-28 00:00:01"}


def test_parse_missing_otime():
    out = _parse_subvol_line("ID 256 gen 30 cgen 25 top level 5 path @/snap")
    assert out["path"] == "@/snap"
    assert out["otime"] == ""


def test_parse_rejects_non_id_and_pathless():
    assert _parse_subvol_line("") is None
    assert _parse_subvol_line("garbage line here") is None
    assert _parse_subvol_line("ID 256 gen 30 top level 5") is None


# -------- _reachable_path --------


def test_reachable_whole_fs_root():
    mounts = [Mount(source="/dev/sda2", mountpoint="/mnt/top", root="/")]
    p = BtrfsBackend._reachable_path("@/.snapshots/1/snapshot", mounts)
    assert str(p) == "/mnt/top/@/.snapshots/1/snapshot"


def test_reachable_under_subvol_mount():
    mounts = [Mount(source="/dev/sda2", mountpoint="/", root="/@")]
    p = BtrfsBackend._reachable_path("@/.snapshots/1/snapshot", mounts)
    assert str(p) == "/.snapshots/1/snapshot"


def test_reachable_exact_subvol():
    mounts = [Mount(source="/dev/sda2", mountpoint="/srv", root="/@data")]
    p = BtrfsBackend._reachable_path("@data", mounts)
    assert str(p) == "/srv"


def test_unreachable_when_not_under_any_mount():
    mounts = [Mount(source="/dev/sda2", mountpoint="/", root="/@")]
    assert BtrfsBackend._reachable_path("@home/.snapshots/2/snapshot", mounts) is None


# -------- is_available --------


def test_is_available_false_without_binary(monkeypatch):
    monkeypatch.setattr(btrfs_mod.shutil, "which", lambda _: None)
    assert BtrfsBackend().is_available() is False


def test_is_available_false_without_btrfs_mount(monkeypatch):
    monkeypatch.setattr(btrfs_mod.shutil, "which", lambda _: "/usr/bin/btrfs")
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [])
    assert BtrfsBackend().is_available() is False


def test_is_available_true(monkeypatch):
    monkeypatch.setattr(btrfs_mod.shutil, "which", lambda _: "/usr/bin/btrfs")
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts",
                        lambda self: [Mount("/dev/sda2", "/", "/@")])
    assert BtrfsBackend().is_available() is True


# -------- discover --------


def test_discover_resolves_paths_under_subvol_mount(monkeypatch):
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts",
                        lambda self: [Mount("/dev/sda2", "/", "/@")])
    out = (_line("256", "@/.snapshots/1/snapshot") + "\n"
           + _line("257", "@/.snapshots/2/snapshot") + "\n")
    monkeypatch.setattr(btrfs_mod, "_btrfs", lambda args: _cp(out))
    snaps = BtrfsBackend().discover()
    assert {str(s.data_root) for s in snaps} == {
        "/.snapshots/1/snapshot", "/.snapshots/2/snapshot"}
    assert all(s.needs_mount is False for s in snaps)
    one = next(s for s in snaps if s.name == "@/.snapshots/1/snapshot")
    assert one.backend_state["id"] == "256"


def test_discover_skips_unreachable_snapshots(monkeypatch):
    # Mounted subvol is @, but a snapshot lives under @home (different subvol).
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts",
                        lambda self: [Mount("/dev/sda2", "/", "/@")])
    out = (_line("256", "@/.snapshots/1/snapshot") + "\n"
           + _line("260", "@home/.snapshots/9/snapshot") + "\n")
    monkeypatch.setattr(btrfs_mod, "_btrfs", lambda args: _cp(out))
    snaps = BtrfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == ["/.snapshots/1/snapshot"]


def test_discover_queries_each_fs_once_and_dedups(monkeypatch):
    # Two mounts of the SAME filesystem must not double-count snapshots.
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [
        Mount("/dev/sda2", "/", "/@"),
        Mount("/dev/sda2", "/home", "/@home"),
    ])
    calls = {"n": 0}

    def fake_btrfs(args):
        calls["n"] += 1
        return _cp(_line("256", "@/.snapshots/1/snapshot") + "\n")

    monkeypatch.setattr(btrfs_mod, "_btrfs", fake_btrfs)
    snaps = BtrfsBackend().discover()
    assert calls["n"] == 1  # queried once per filesystem
    assert [str(s.data_root) for s in snaps] == ["/.snapshots/1/snapshot"]


def test_discover_empty_when_btrfs_fails(monkeypatch):
    # Non-root / error: subvolume list returns non-zero -> no snapshots.
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts",
                        lambda self: [Mount("/dev/sda2", "/", "/@")])
    monkeypatch.setattr(btrfs_mod, "_btrfs", lambda args: _cp(returncode=1))
    assert BtrfsBackend().discover() == []


def test_discover_empty_without_mounts(monkeypatch):
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [])
    assert BtrfsBackend().discover() == []
