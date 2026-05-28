"""Layer 1: ZFS backend is_available() + discover() with mocked subprocess."""

from __future__ import annotations

import subprocess
from pathlib import Path

import backends.zfs as zfs_mod
from backends.zfs import ZfsBackend


def _cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["zfs"], returncode=returncode,
                                       stdout=stdout, stderr="")


def test_is_available_false_when_missing(monkeypatch):
    monkeypatch.setattr(zfs_mod.shutil, "which", lambda _: None)
    assert ZfsBackend().is_available() is False


def test_is_available_false_when_module_dead(monkeypatch):
    monkeypatch.setattr(zfs_mod.shutil, "which", lambda _: "/usr/sbin/zfs")
    monkeypatch.setattr(zfs_mod, "_zfs", lambda args: _cp(returncode=1))
    assert ZfsBackend().is_available() is False


def test_is_available_true(monkeypatch):
    monkeypatch.setattr(zfs_mod.shutil, "which", lambda _: "/usr/sbin/zfs")
    monkeypatch.setattr(zfs_mod, "_zfs", lambda args: _cp(returncode=0))
    assert ZfsBackend().is_available() is True


def _no_live_mounts(monkeypatch):
    """Force the live mount table empty so tests exercise the property path."""
    monkeypatch.setattr(ZfsBackend, "_live_mountpoints", lambda self: {})


def test_discover_builds_snapshot_paths(monkeypatch):
    _no_live_mounts(monkeypatch)

    def fake_zfs(args):
        if "filesystem" in args:
            return _cp("tank/home\t/home\ntank/data\t/data\n")
        if "snapshot" in args:
            return _cp("tank/home@daily-1\ntank/home@daily-2\ntank/data@daily-1\n")
        return _cp()

    monkeypatch.setattr(zfs_mod, "_zfs", fake_zfs)
    snaps = ZfsBackend().discover()
    roots = {str(s.data_root) for s in snaps}
    assert roots == {
        "/home/.zfs/snapshot/daily-1",
        "/home/.zfs/snapshot/daily-2",
        "/data/.zfs/snapshot/daily-1",
    }
    assert all(s.needs_mount is False for s in snaps)
    one = next(s for s in snaps if s.name == "tank/home@daily-1")
    assert one.backend_state == {"dataset": "tank/home", "snapshot": "daily-1"}


def test_discover_skips_unmounted_none_but_keeps_real(monkeypatch):
    _no_live_mounts(monkeypatch)

    def fake_zfs(args):
        if "filesystem" in args:
            return _cp("tank/none\tnone\ntank/home\t/home\n")
        if "snapshot" in args:
            return _cp("tank/none@s\ntank/home@s\n")
        return _cp()

    monkeypatch.setattr(zfs_mod, "_zfs", fake_zfs)
    snaps = ZfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == ["/home/.zfs/snapshot/s"]


def test_discover_resolves_legacy_via_live_mount(monkeypatch):
    # Round-1 HIGH fix: a legacy dataset whose ZFS property is "legacy" but
    # which is actually mounted (per the live mount table) must be discovered.
    monkeypatch.setattr(ZfsBackend, "_live_mountpoints",
                        lambda self: {"tank/home": "/home"})

    def fake_zfs(args):
        if "filesystem" in args:
            return _cp("tank/home\tlegacy\n")
        if "snapshot" in args:
            return _cp("tank/home@s\n")
        return _cp()

    monkeypatch.setattr(zfs_mod, "_zfs", fake_zfs)
    snaps = ZfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == ["/home/.zfs/snapshot/s"]


def test_live_mountpoints_parses_mountinfo(monkeypatch, tmp_path):
    mountinfo = (
        "24 30 0:22 / /proc rw,nosuid - proc proc rw\n"
        "55 30 0:50 / /home rw - zfs tank/home rw\n"
        "56 30 0:51 / /data rw,relatime - zfs tank/data rw\n"
        "60 30 8:1 / /boot rw - ext4 /dev/sda1 rw\n"
    )
    mi = tmp_path / "mountinfo"
    mi.write_text(mountinfo)
    monkeypatch.setattr(zfs_mod.Path, "read_text", lambda self: mountinfo)
    out = ZfsBackend()._live_mountpoints()
    assert out == {"tank/home": "/home", "tank/data": "/data"}


def test_live_mountpoints_unescapes_octal(monkeypatch):
    # mountinfo encodes spaces as \040 etc.; they must be decoded.
    mountinfo = "55 30 0:50 / /media/Storage\\040Pool/home rw - zfs tank/home rw\n"
    monkeypatch.setattr(zfs_mod.Path, "read_text", lambda self: mountinfo)
    out = ZfsBackend()._live_mountpoints()
    assert out == {"tank/home": "/media/Storage Pool/home"}


def test_discover_empty_when_zfs_fails(monkeypatch):
    _no_live_mounts(monkeypatch)
    monkeypatch.setattr(zfs_mod, "_zfs", lambda args: _cp(returncode=1))
    assert ZfsBackend().discover() == []


def test_discover_skips_dataset_without_mountpoint(monkeypatch):
    _no_live_mounts(monkeypatch)

    # Snapshot whose dataset isn't in the filesystem listing is dropped.
    def fake_zfs(args):
        if "filesystem" in args:
            return _cp("tank/home\t/home\n")
        if "snapshot" in args:
            return _cp("tank/home@s\ntank/ghost@s\n")
        return _cp()

    monkeypatch.setattr(zfs_mod, "_zfs", fake_zfs)
    snaps = ZfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == [str(Path("/home/.zfs/snapshot/s"))]
