"""Btrfs snapshot backend.

Btrfs snapshots are read-only subvolumes. They are listed with
``btrfs subvolume list -s <mountpoint>``, which reports every snapshot in the
whole filesystem with a path **relative to the filesystem root subvolume**
(subvolid 5), e.g. ``@/.snapshots/1/snapshot``.

To turn that into a usable on-disk path we map each snapshot's fs-root-relative
path against the live mounts of the same filesystem: a snapshot is reachable
when one of the filesystem's mounts exposes an ancestor subvolume of it. No
extra mounting is needed for the reachable case, so ``needs_mount`` is False.

Per the v1 directive, this backend reports raw `subvolume list` output and does
NOT do any cross-backend overlap handling — the orchestrator deduplicates after
discovery (and in Phase 2 nothing is pruned, since Timeshift isn't registered).

Default on openSUSE; optional on Ubuntu/Debian. Note: ``btrfs subvolume list``
typically requires root, so unprivileged ``discover()`` may return nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from backends._mountinfo import Mount, mounts_of_fstype, read_all_mounts
from backends.base import DiscoveredSnapshot, SnapshotBackend


def _btrfs(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["btrfs", *args], capture_output=True, text=True, check=False
    )


def _parse_subvol_line(line: str) -> dict[str, str] | None:
    """Parse one `btrfs subvolume list -s` line.

    Format: ``ID <id> gen <g> cgen <c> top level <t> otime <date> <time> path <p>``
    We key off the ``ID``, ``otime`` and ``path`` tokens rather than fixed
    offsets, so field additions or a missing otime (``otime -``) don't break it.
    """
    toks = line.split()
    if len(toks) < 2 or toks[0] != "ID" or "path" not in toks:
        return None
    pi = toks.index("path")
    path = " ".join(toks[pi + 1:])
    if not path:
        return None
    sid = toks[1]
    otime = ""
    if "otime" in toks:
        oi = toks.index("otime")
        otime = " ".join(toks[oi + 1:pi])
    return {"id": sid, "path": path, "otime": otime}


class BtrfsBackend(SnapshotBackend):
    name = "btrfs"

    def _btrfs_mounts(self) -> list[Mount]:
        return mounts_of_fstype("btrfs")

    def _fs_uuid(self, mountpoint: str) -> str | None:
        """Return the Btrfs filesystem UUID for the fs at `mountpoint`, or None.

        `btrfs filesystem show -m <mp>` reports the fs containing the mount.
        Normally needs root; None on failure (callers fall back to a weaker id).
        """
        r = _btrfs(["filesystem", "show", "-m", mountpoint])
        if r.returncode != 0:
            return None
        m = re.search(r"uuid:\s*(\S+)", r.stdout)
        return m.group(1) if m else None

    def _fs_identity(self, mount: Mount) -> str:
        """Stable per-filesystem key so each Btrfs fs is queried exactly once.

        Prefer the Btrfs UUID (unifies multi-device fs and source aliases like
        /dev/sda2 vs /dev/disk/by-uuid/...). Fall back to the canonicalized
        source device when the UUID isn't available (e.g. unprivileged).
        """
        uuid = self._fs_uuid(mount.mountpoint)
        if uuid:
            return f"uuid:{uuid}"
        return f"dev:{os.path.realpath(mount.source)}"

    def is_available(self) -> bool:
        """True when the btrfs CLI is installed and a Btrfs filesystem is mounted.

        We require a mounted Btrfs filesystem (not just the binary) because the
        backend can only do anything useful against one. We do NOT check whether
        snapshots exist.
        """
        if shutil.which("btrfs") is None:
            return False
        return bool(self._btrfs_mounts())

    @staticmethod
    def _resolve_through(p: str, m: Mount) -> Path | None:
        """On-disk path where mount `m` exposes fs-root-relative subvol path `p`
        (already stripped of leading "/"), or None if `m`'s subvolume isn't an
        ancestor of `p`.

          - root "/" (whole fs root mounted) -> <mountpoint>/<p>
          - root "/@" exposing subvol "@", p "@/.snapshots/1/snapshot"
            -> <mountpoint>/.snapshots/1/snapshot
        """
        r = m.root.strip("/")
        if r == "":
            return Path(m.mountpoint) / p
        if p == r:
            return Path(m.mountpoint)
        if p.startswith(r + "/"):
            return Path(m.mountpoint) / p[len(r) + 1:]
        return None

    @classmethod
    def _candidate_paths(cls, subvol_path: str, mounts: list[Mount]) -> list[Path]:
        """Every on-disk path a same-fs mount could expose `subvol_path` at,
        MOST SPECIFIC first (longest matching root), so the caller prefers the
        deepest same-fs mount but can fall back to a shallower one when a more
        specific candidate turns out to be shadowed."""
        p = subvol_path.strip("/")
        out: list[Path] = []
        for m in sorted(mounts, key=lambda m: len(m.root.strip("/")), reverse=True):
            dr = cls._resolve_through(p, m)
            if dr is not None:
                out.append(dr)
        return out

    @staticmethod
    def _topmost_covering(path: str, all_mounts: list[Mount]) -> Mount | None:
        """The mount that is effectively visible at `path`.

        The covering mount is the one with the longest mountpoint that is an
        ancestor-or-equal of `path`; among mounts stacked at the SAME
        mountpoint, the one listed last in mountinfo wins (it is on top).
        Returns None when nothing covers `path` (only possible with an empty
        mount table, i.e. in tests).
        """
        best: Mount | None = None
        best_len = -1
        for m in all_mounts:
            mp = m.mountpoint
            if path == mp or path.startswith(mp.rstrip("/") + "/"):
                # >= so a later same-mountpoint (stacked) mount overrides.
                if len(mp) >= best_len:
                    best, best_len = m, len(mp)
        return best

    @classmethod
    def _is_visible(cls, data_root: Path, subvol_path: str,
                    fs_mounts: list[Mount], all_mounts: list[Mount]) -> bool:
        """True if `data_root` actually shows the snapshot's bytes.

        Requires the topmost mount covering `data_root` to (a) belong to this
        filesystem and (b) expose the snapshot's subvolume such that resolving
        `subvol_path` through it reproduces `data_root`. (b) rules out a same-fs
        mount of a DIFFERENT subvolume stacked on the path, which would point
        `data_root` at the wrong subvolume's bytes.
        """
        top = cls._topmost_covering(str(data_root), all_mounts)
        if top is None:
            return True
        if top not in fs_mounts:
            return False
        return cls._resolve_through(subvol_path.strip("/"), top) == data_root

    def discover(self) -> list[DiscoveredSnapshot]:
        mounts = self._btrfs_mounts()
        if not mounts:
            return []
        all_mounts = read_all_mounts()

        # Group mounts by filesystem identity so we query each fs once (a
        # `subvolume list` reports the whole fs regardless of which mount we
        # query) and never emit the same snapshot via two mount aliases.
        by_fs: dict[str, list[Mount]] = {}
        for m in mounts:
            by_fs.setdefault(self._fs_identity(m), []).append(m)

        snaps: list[DiscoveredSnapshot] = []
        seen: set[str] = set()
        for fs_mounts in by_fs.values():
            r = _btrfs(["subvolume", "list", "-s", fs_mounts[0].mountpoint])
            if r.returncode != 0:
                continue
            for line in r.stdout.splitlines():
                parsed = _parse_subvol_line(line)
                if parsed is None:
                    continue
                # First candidate (deepest mount first) that is actually
                # visible — i.e. not masked by a foreign overmount.
                data_root = next(
                    (c for c in self._candidate_paths(parsed["path"], fs_mounts)
                     if self._is_visible(c, parsed["path"], fs_mounts, all_mounts)),
                    None,
                )
                if data_root is None:
                    continue
                key = str(data_root)
                if key in seen:
                    continue
                seen.add(key)
                snaps.append(DiscoveredSnapshot(
                    name=parsed["path"],
                    data_root=data_root,
                    needs_mount=False,
                    backend_state={
                        "id": parsed["id"],
                        "otime": parsed["otime"],
                        "subvol_path": parsed["path"],
                    },
                ))
        return snaps
