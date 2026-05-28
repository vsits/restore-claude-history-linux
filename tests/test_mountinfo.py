"""Layer 1: shared /proc/self/mountinfo parser."""

from __future__ import annotations

from pathlib import Path

from backends._mountinfo import Mount, mounts_of_fstype, unescape

SAMPLE = (
    "24 30 0:22 / /proc rw,nosuid - proc proc rw\n"
    "55 30 0:50 /@ /home rw - btrfs /dev/sda2 rw,subvol=/@\n"
    "56 30 0:51 / /tank/home rw - zfs tank/home rw\n"
    "57 30 0:52 / /media/Storage\\040Pool rw - zfs tank/media rw\n"
    "58 30 0:53 / /srv rw - zfs tank/My\\040Dataset rw\n"
    "60 30 8:1 / /boot rw - ext4 /dev/sda1 rw\n"
)


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "mountinfo"
    p.write_text(SAMPLE)
    return p


def test_unescape_octal():
    assert unescape("/media/Storage\\040Pool/home") == "/media/Storage Pool/home"
    assert unescape("a\\011b\\012c\\134d") == "a\tb\nc\\d"


def test_filters_by_fstype(tmp_path):
    zfs = mounts_of_fstype("zfs", path=_write(tmp_path))
    assert {m.source for m in zfs} == {"tank/home", "tank/media", "tank/My Dataset"}
    btrfs = mounts_of_fstype("btrfs", path=_write(tmp_path))
    assert [m.source for m in btrfs] == ["/dev/sda2"]


def test_captures_root_and_mountpoint(tmp_path):
    btrfs = mounts_of_fstype("btrfs", path=_write(tmp_path))
    m = btrfs[0]
    assert m == Mount(fstype="btrfs", source="/dev/sda2",
                      mountpoint="/home", root="/@")


def test_unescapes_mountpoint(tmp_path):
    zfs = mounts_of_fstype("zfs", path=_write(tmp_path))
    media = next(m for m in zfs if m.mountpoint == "/media/Storage Pool")
    assert media.source == "tank/media"


def test_unescapes_source(tmp_path):
    # Round-1 MEDIUM fix: mount source (e.g. a dataset name with a space) must
    # be unescaped so it matches `zfs list -o name`.
    zfs = mounts_of_fstype("zfs", path=_write(tmp_path))
    assert any(m.source == "tank/My Dataset" for m in zfs)


def test_missing_file_returns_empty(tmp_path):
    assert mounts_of_fstype("zfs", path=tmp_path / "nope") == []
