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
            return _cp("tank/home@daily-1\t1779926401\ntank/home@daily-2\t1779926401\ntank/data@daily-1\t1779926401\n")
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
            return _cp("tank/none@s\t1779926401\ntank/home@s\t1779926401\n")
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
            return _cp("tank/home@s\t1779926401\n")
        return _cp()

    monkeypatch.setattr(zfs_mod, "_zfs", fake_zfs)
    snaps = ZfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == ["/home/.zfs/snapshot/s"]


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
            return _cp("tank/home@s\t1779926401\ntank/ghost@s\t1779926401\n")
        return _cp()

    monkeypatch.setattr(zfs_mod, "_zfs", fake_zfs)
    snaps = ZfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == [str(Path("/home/.zfs/snapshot/s"))]


# -------- created_at population --------


def test_discover_populates_created_at_from_creation_epoch(monkeypatch):
    """ZFS backend reads `creation` as a Unix epoch and stores as UTC."""
    from datetime import datetime, timezone

    _no_live_mounts(monkeypatch)

    def fake_zfs(args):
        if "filesystem" in args:
            return _cp("tank/home\t/home\n")
        if "snapshot" in args:
            # 1779926401 = 2026-05-28 00:00:01 UTC
            return _cp("tank/home@s\t1779926401\n")
        return _cp()

    monkeypatch.setattr(zfs_mod, "_zfs", fake_zfs)
    snaps = ZfsBackend().discover()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.created_at == datetime(2026, 5, 28, 0, 0, 1, tzinfo=timezone.utc)
    assert s.created_at.tzinfo is timezone.utc


def test_discover_skips_snapshot_with_unparseable_creation(monkeypatch):
    """Snapshot whose creation epoch is garbage is skipped, not sentinel'd."""
    _no_live_mounts(monkeypatch)

    def fake_zfs(args):
        if "filesystem" in args:
            return _cp("tank/home\t/home\n")
        if "snapshot" in args:
            return _cp("tank/home@s\tnot-a-number\n")
        return _cp()

    monkeypatch.setattr(zfs_mod, "_zfs", fake_zfs)
    assert ZfsBackend().discover() == []
