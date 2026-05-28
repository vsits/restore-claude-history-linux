"""Shared /proc/self/mountinfo parsing for filesystem-snapshot backends.

Both the ZFS and Btrfs backends need to map the live mount table — including
the octal-escape decoding that util-linux applies to paths — so the parsing
lives here once rather than being duplicated (and drifting) per backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_MOUNTINFO = Path("/proc/self/mountinfo")


def unescape(field: str) -> str:
    """Decode the octal escapes util-linux writes into mountinfo fields.

    Spaces, tabs, newlines, and backslashes are encoded as \\040, \\011,
    \\012, \\134. Backslash is decoded last so the others aren't
    re-interpreted.
    """
    return (field.replace("\\040", " ")
                 .replace("\\011", "\t")
                 .replace("\\012", "\n")
                 .replace("\\134", "\\"))


@dataclass(frozen=True)
class Mount:
    """One mountinfo entry, restricted to the fields backends need."""

    source: str       # mount source: device (/dev/sda2) or dataset (tank/home)
    mountpoint: str   # absolute path the fs is mounted at (octal-unescaped)
    root: str         # subvolume/root path within the fs (mountinfo field 4)


def mounts_of_fstype(fstype: str, *, path: Path | None = None) -> list[Mount]:
    """Return every current mount of `fstype` from /proc/self/mountinfo.

    `path` overrides the mountinfo source (tests only). Returns [] if the file
    cannot be read.
    """
    src = path or _MOUNTINFO
    try:
        text = src.read_text()
    except OSError:
        return []
    result: list[Mount] = []
    for line in text.splitlines():
        # mountinfo: <id> <parent> <maj:min> <root> <mountpoint> <opts...>
        #            - <fstype> <source> <super-opts>
        sep = line.find(" - ")
        if sep == -1:
            continue
        pre = line[:sep].split()
        post = line[sep + 3:].split()
        if len(pre) < 5 or len(post) < 2:
            continue
        if post[0] != fstype:
            continue
        result.append(Mount(
            source=post[1],
            mountpoint=unescape(pre[4]),
            root=unescape(pre[3]),
        ))
    return result
