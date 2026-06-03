"""Layer 1: Btrfs backend is_available() / discover() / parsing, mocked."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import backends.btrfs as btrfs_mod
from backends._mountinfo import Mount
from backends.btrfs import BtrfsBackend, _parse_subvol_line


_FIXED_CREATED_AT = datetime(2026, 5, 28, 0, 0, 1, tzinfo=timezone.utc)


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


# -------- _candidate_paths --------


def _mnt(source="/dev/sda2", mountpoint="/", root="/@"):
    return Mount(fstype="btrfs", source=source, mountpoint=mountpoint, root=root)


def test_candidates_whole_fs_root():
    mounts = [_mnt(mountpoint="/mnt/top", root="/")]
    cands = BtrfsBackend._candidate_paths("@/.snapshots/1/snapshot", mounts)
    assert [str(p) for p in cands] == ["/mnt/top/@/.snapshots/1/snapshot"]


def test_candidates_under_subvol_mount():
    mounts = [_mnt(mountpoint="/", root="/@")]
    cands = BtrfsBackend._candidate_paths("@/.snapshots/1/snapshot", mounts)
    assert [str(p) for p in cands] == ["/.snapshots/1/snapshot"]


def test_candidates_exact_subvol():
    mounts = [_mnt(mountpoint="/srv", root="/@data")]
    cands = BtrfsBackend._candidate_paths("@data", mounts)
    assert [str(p) for p in cands] == ["/srv"]


def test_candidates_deepest_subvol_first():
    # Both "/" (subvol @) and "/.snapshots" (subvol @/.snapshots) expose it;
    # the more specific mount must be ordered first.
    mounts = [
        _mnt(mountpoint="/", root="/@"),
        _mnt(mountpoint="/.snapshots", root="/@/.snapshots"),
    ]
    cands = BtrfsBackend._candidate_paths("@/.snapshots/1/snapshot", mounts)
    # Both resolve to the same on-disk path here, deepest-mount candidate first.
    assert str(cands[0]) == "/.snapshots/1/snapshot"


def test_candidates_empty_when_not_under_any_mount():
    mounts = [_mnt(mountpoint="/", root="/@")]
    assert BtrfsBackend._candidate_paths("@home/.snapshots/2/snapshot", mounts) == []


# -------- _topmost_covering / _is_visible --------


def test_topmost_covering_prefers_longest_mountpoint():
    mounts = [_mnt(mountpoint="/", root="/@"),
              Mount("ext4", "/dev/sdb1", "/.snapshots", "/")]
    top = BtrfsBackend._topmost_covering("/.snapshots/1/snapshot", mounts)
    assert top.mountpoint == "/.snapshots" and top.fstype == "ext4"


def test_topmost_covering_same_mountpoint_last_wins():
    stacked = [_mnt(mountpoint="/.snapshots", root="/@/.snapshots"),
               Mount("ext4", "/dev/sdb1", "/.snapshots", "/")]
    top = BtrfsBackend._topmost_covering("/.snapshots/1/snapshot", stacked)
    assert top.fstype == "ext4"  # later (stacked on top) wins


def test_not_visible_under_foreign_overmount():
    fs_mounts = [_mnt(mountpoint="/", root="/@")]
    foreign = [Mount("ext4", "/dev/sdb1", "/.snapshots", "/")]
    assert BtrfsBackend._is_visible(Path("/.snapshots/1/snapshot"),
                                    "@/.snapshots/1/snapshot",
                                    fs_mounts, foreign) is False


def test_visible_under_same_fs_overmount_same_subvol():
    # /.snapshots exposes @/.snapshots (the snapshot's own parent subvol).
    fs_mounts = [_mnt(mountpoint="/", root="/@"),
                 _mnt(mountpoint="/.snapshots", root="/@/.snapshots")]
    assert BtrfsBackend._is_visible(Path("/.snapshots/1/snapshot"),
                                    "@/.snapshots/1/snapshot",
                                    fs_mounts, fs_mounts) is True


def test_not_visible_under_same_fs_overmount_different_subvol():
    # /.snapshots exposes a DIFFERENT subvol (@other); the candidate path would
    # point into the wrong subvol -> not visible.
    fs_mounts = [_mnt(mountpoint="/", root="/@"),
                 _mnt(mountpoint="/.snapshots", root="/@other")]
    assert BtrfsBackend._is_visible(Path("/.snapshots/1/snapshot"),
                                    "@/.snapshots/1/snapshot",
                                    fs_mounts, fs_mounts) is False


def test_visible_when_mount_table_empty():
    fs_mounts = [_mnt(mountpoint="/", root="/@")]
    assert BtrfsBackend._is_visible(Path("/.snapshots/1/snapshot"),
                                    "@/.snapshots/1/snapshot",
                                    fs_mounts, []) is True


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
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [_mnt()])
    assert BtrfsBackend().is_available() is True


# -------- discover --------


def _stub_fs(monkeypatch, *, uuid=None, all_mounts=None):
    """Neutralize fs-UUID lookup + the mount-table read in discover tests."""
    monkeypatch.setattr(BtrfsBackend, "_fs_uuid", lambda self, mp: uuid)
    monkeypatch.setattr(btrfs_mod, "read_all_mounts", lambda: all_mounts or [])
    # discover() now also calls _parse_creation_time() per snapshot (v1.1).
    # Tests stub `_btrfs` globally to return subvolume-list output, which would
    # not parse as a Creation-time line; short-circuit to a fixed UTC datetime
    # so the discover path completes.
    monkeypatch.setattr(btrfs_mod, "_parse_creation_time",
                        lambda data_root: _FIXED_CREATED_AT)


def test_discover_resolves_paths_under_subvol_mount(monkeypatch):
    _stub_fs(monkeypatch)
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [_mnt()])
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
    _stub_fs(monkeypatch)
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [_mnt()])
    out = (_line("256", "@/.snapshots/1/snapshot") + "\n"
           + _line("260", "@home/.snapshots/9/snapshot") + "\n")
    monkeypatch.setattr(btrfs_mod, "_btrfs", lambda args: _cp(out))
    snaps = BtrfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == ["/.snapshots/1/snapshot"]


def test_discover_queries_each_fs_once_and_dedups(monkeypatch):
    # Two mounts of the SAME filesystem must not double-count snapshots.
    _stub_fs(monkeypatch)
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [
        _mnt(mountpoint="/", root="/@"),
        _mnt(mountpoint="/home", root="/@home"),
    ])
    calls = {"n": 0}

    def fake_btrfs(args):
        calls["n"] += 1
        return _cp(_line("256", "@/.snapshots/1/snapshot") + "\n")

    monkeypatch.setattr(btrfs_mod, "_btrfs", fake_btrfs)
    snaps = BtrfsBackend().discover()
    assert calls["n"] == 1  # queried once per filesystem
    assert [str(s.data_root) for s in snaps] == ["/.snapshots/1/snapshot"]


def test_discover_dedups_source_aliases_by_uuid(monkeypatch):
    # Round-1 HIGH fix: same fs mounted via two source aliases is ONE fs.
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [
        _mnt(source="/dev/sda2", mountpoint="/", root="/@"),
        _mnt(source="/dev/disk/by-uuid/XYZ", mountpoint="/mnt/top", root="/"),
    ])
    monkeypatch.setattr(BtrfsBackend, "_fs_uuid", lambda self, mp: "UUID-1")
    monkeypatch.setattr(btrfs_mod, "read_all_mounts", lambda: [])
    monkeypatch.setattr(btrfs_mod, "_parse_creation_time",
                        lambda data_root: _FIXED_CREATED_AT)
    calls = {"n": 0}

    def fake_btrfs(args):
        calls["n"] += 1
        return _cp(_line("256", "@/.snapshots/1/snapshot") + "\n")

    monkeypatch.setattr(btrfs_mod, "_btrfs", fake_btrfs)
    snaps = BtrfsBackend().discover()
    assert calls["n"] == 1  # one fs -> one query, despite two source aliases
    assert len(snaps) == 1


def test_discover_skips_shadowed_snapshot(monkeypatch):
    # Round-1 MEDIUM fix: a foreign fs overmounted at /.snapshots masks the
    # snapshot bytes -> skip rather than emit a bogus data_root.
    _stub_fs(monkeypatch, all_mounts=[
        Mount(fstype="ext4", source="/dev/sdb1", mountpoint="/.snapshots", root="/"),
    ])
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts",
                        lambda self: [_mnt(mountpoint="/", root="/@")])
    monkeypatch.setattr(btrfs_mod, "_btrfs",
                        lambda args: _cp(_line("256", "@/.snapshots/1/snapshot") + "\n"))
    assert BtrfsBackend().discover() == []


def test_discover_skips_when_chosen_mount_overmounted_same_path(monkeypatch):
    # Round-2 HIGH: a foreign fs stacked at the SAME path as the chosen same-fs
    # mount must be caught (not just deeper mounts).
    monkeypatch.setattr(BtrfsBackend, "_fs_uuid", lambda self, mp: "U1")
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [
        _mnt(mountpoint="/", root="/@"),
        _mnt(mountpoint="/.snapshots", root="/@/.snapshots"),
    ])
    monkeypatch.setattr(btrfs_mod, "read_all_mounts", lambda: [
        _mnt(mountpoint="/", root="/@"),
        _mnt(mountpoint="/.snapshots", root="/@/.snapshots"),
        Mount("ext4", "/dev/sdb1", "/.snapshots", "/"),  # stacked on top, last
    ])
    monkeypatch.setattr(btrfs_mod, "_btrfs",
                        lambda args: _cp(_line("256", "@/.snapshots/1/snapshot") + "\n"))
    assert BtrfsBackend().discover() == []


def test_discover_falls_back_to_shallower_visible_mount(monkeypatch):
    # Round-2 HIGH: the most specific candidate (/a/x) is shadowed by a foreign
    # overmount at /a; discovery must fall back to the reachable /@/x via "/".
    monkeypatch.setattr(BtrfsBackend, "_fs_uuid", lambda self, mp: "U1")
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [
        _mnt(mountpoint="/a", root="/@"),
        _mnt(mountpoint="/", root="/"),
    ])
    monkeypatch.setattr(btrfs_mod, "read_all_mounts", lambda: [
        _mnt(mountpoint="/a", root="/@"),
        Mount("ext4", "/dev/sdb1", "/a", "/"),  # overmounts /a, listed last
        _mnt(mountpoint="/", root="/"),
    ])
    monkeypatch.setattr(btrfs_mod, "_btrfs",
                        lambda args: _cp(_line("256", "@/x") + "\n"))
    monkeypatch.setattr(btrfs_mod, "_parse_creation_time",
                        lambda data_root: _FIXED_CREATED_AT)
    snaps = BtrfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == ["/@/x"]


def test_discover_falls_back_when_overmount_is_different_subvol(monkeypatch):
    # Round-3 HIGH: /.snapshots exposes a DIFFERENT subvol (@other), so the
    # candidate /.snapshots/1/snapshot points into the wrong subvol. Discovery
    # must reject it and fall back to the whole-fs-root mount /mnt/top.
    monkeypatch.setattr(BtrfsBackend, "_fs_uuid", lambda self, mp: "U1")
    fs_mounts = [
        _mnt(mountpoint="/", root="/@"),
        _mnt(mountpoint="/.snapshots", root="/@other"),
        _mnt(mountpoint="/mnt/top", root="/"),
    ]
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: fs_mounts)
    monkeypatch.setattr(btrfs_mod, "read_all_mounts", lambda: fs_mounts)
    monkeypatch.setattr(btrfs_mod, "_btrfs",
                        lambda args: _cp(_line("256", "@/.snapshots/1/snapshot") + "\n"))
    monkeypatch.setattr(btrfs_mod, "_parse_creation_time",
                        lambda data_root: _FIXED_CREATED_AT)
    snaps = BtrfsBackend().discover()
    assert [str(s.data_root) for s in snaps] == ["/mnt/top/@/.snapshots/1/snapshot"]


def test_fs_uuid_parses_filesystem_show(monkeypatch):
    show = ("Label: 'none'  uuid: 1b3e7c44-aa00-4f00-9abc-deadbeef0001\n"
            "\tTotal devices 1 FS bytes used 1.00GiB\n"
            "\tdevid 1 size 10.00GiB used 2.00GiB path /dev/sda2\n")
    monkeypatch.setattr(btrfs_mod, "_btrfs", lambda args: _cp(show))
    assert BtrfsBackend()._fs_uuid("/") == "1b3e7c44-aa00-4f00-9abc-deadbeef0001"


def test_fs_uuid_none_on_failure(monkeypatch):
    monkeypatch.setattr(btrfs_mod, "_btrfs", lambda args: _cp(returncode=1))
    assert BtrfsBackend()._fs_uuid("/") is None


def test_discover_empty_when_btrfs_fails(monkeypatch):
    # Non-root / error: subvolume list returns non-zero -> no snapshots.
    _stub_fs(monkeypatch)
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [_mnt()])
    monkeypatch.setattr(btrfs_mod, "_btrfs", lambda args: _cp(returncode=1))
    assert BtrfsBackend().discover() == []


def test_discover_empty_without_mounts(monkeypatch):
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [])
    assert BtrfsBackend().discover() == []


# -------- _parse_creation_time --------


def test_parse_creation_time_with_tz_offset(monkeypatch, tmp_path):
    """Parses 'Creation time: 2026-06-02 17:10:26 +0000' to UTC."""
    from datetime import datetime, timezone

    from backends.btrfs import _parse_creation_time

    monkeypatch.setattr(btrfs_mod, "_btrfs",
                        lambda args: _cp("Name: foo\n"
                                          "Creation time:       2026-06-02 17:10:26 +0000\n"))
    out = _parse_creation_time(tmp_path)
    assert out == datetime(2026, 6, 2, 17, 10, 26, tzinfo=timezone.utc)
    assert out.tzinfo is timezone.utc


def test_parse_creation_time_returns_none_when_btrfs_fails(monkeypatch, tmp_path):
    from backends.btrfs import _parse_creation_time
    monkeypatch.setattr(btrfs_mod, "_btrfs",
                        lambda args: _cp(returncode=1))
    assert _parse_creation_time(tmp_path) is None


def test_parse_creation_time_returns_none_when_tz_missing(monkeypatch, tmp_path):
    """Older btrfs-progs that emit no TZ marker get refused, not guessed."""
    from backends.btrfs import _parse_creation_time
    monkeypatch.setattr(btrfs_mod, "_btrfs",
                        lambda args: _cp("Creation time: 2026-06-02 17:10:26\n"))
    assert _parse_creation_time(tmp_path) is None


def test_discover_skips_snapshot_when_creation_time_unknown(monkeypatch):
    """Discover refuses to emit a snapshot with no usable creation time."""
    monkeypatch.setattr(BtrfsBackend, "_fs_uuid", lambda self, mp: "U")
    monkeypatch.setattr(btrfs_mod, "read_all_mounts", lambda: [])
    monkeypatch.setattr(BtrfsBackend, "_btrfs_mounts", lambda self: [_mnt()])
    monkeypatch.setattr(btrfs_mod, "_btrfs",
                        lambda args: _cp(_line("256", "@/s") + "\n"))
    # No stub on _parse_creation_time → returns None (real impl, real failure
    # mode, no stubbed btrfs-show output).
    monkeypatch.setattr(btrfs_mod, "_parse_creation_time",
                        lambda data_root: None)
    assert BtrfsBackend().discover() == []
