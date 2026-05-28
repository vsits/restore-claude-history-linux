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

import shutil
import subprocess
from pathlib import Path

from backends._mountinfo import Mount, mounts_of_fstype
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
    def _reachable_path(subvol_path: str, mounts: list[Mount]) -> Path | None:
        """Resolve a snapshot's fs-root-relative path to a usable on-disk path.

        `mounts` are all current mounts of the snapshot's filesystem. For each,
        `mount.root` is the subvolume exposed at `mount.mountpoint`:
          - root "/" (whole fs root mounted) -> <mountpoint>/<subvol_path>
          - root "/@" exposing subvol "@", snapshot "@/.snapshots/1/snapshot"
            -> <mountpoint>/.snapshots/1/snapshot
        Returns None when no mount of this filesystem exposes the snapshot.
        """
        p = subvol_path.strip("/")
        for m in mounts:
            r = m.root.strip("/")
            if r == "":
                return Path(m.mountpoint) / p
            if p == r:
                return Path(m.mountpoint)
            if p.startswith(r + "/"):
                return Path(m.mountpoint) / p[len(r) + 1:]
        return None

    def discover(self) -> list[DiscoveredSnapshot]:
        mounts = self._btrfs_mounts()
        if not mounts:
            return []

        # Group mounts by filesystem so we query each fs once (a `subvolume
        # list` reports the whole fs regardless of which mount we query).
        by_fs: dict[str, list[Mount]] = {}
        for m in mounts:
            by_fs.setdefault(m.source, []).append(m)

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
                data_root = self._reachable_path(parsed["path"], fs_mounts)
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
