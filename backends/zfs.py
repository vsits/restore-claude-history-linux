"""ZFS snapshot backend.

ZFS snapshots are auto-mounted on access under ``<dataset-mountpoint>/.zfs/
snapshot/<snapname>/`` (the ``snapdir`` property controls visibility, but the
path is reachable even when ``snapdir=hidden``). Because access triggers the
mount, ``needs_mount`` is False and no explicit mount/unmount is required.

Common on NAS / Pi-hole / homelab boxes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from backends.base import DiscoveredSnapshot, SnapshotBackend

# Mountpoint values that are not real on-disk paths.
_NON_PATH_MOUNTPOINTS = {"none", "legacy", "-"}


def _unescape_mountinfo(field: str) -> str:
    """Decode the octal escapes util-linux writes into /proc/self/mountinfo.

    Spaces, tabs, newlines, and backslashes in a path are encoded as \\040,
    \\011, \\012, \\134. Backslash is decoded last so the others aren't
    re-interpreted.
    """
    return (field.replace("\\040", " ")
                 .replace("\\011", "\t")
                 .replace("\\012", "\n")
                 .replace("\\134", "\\"))


def _zfs(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["zfs", *args], capture_output=True, text=True, check=False
    )


class ZfsBackend(SnapshotBackend):
    name = "zfs"

    def is_available(self) -> bool:
        """True when the zfs CLI is installed and the kernel module responds.

        ``zfs list`` exits 0 even with no pools, but fails when the module is
        not loaded — which distinguishes a usable backend from an installed
        but inert one. We do not check whether snapshots exist here.
        """
        if shutil.which("zfs") is None:
            return False
        return _zfs(["list", "-H"]).returncode == 0

    def _property_mountpoints(self) -> dict[str, str]:
        """Map dataset name -> its ZFS `mountpoint` property value."""
        out: dict[str, str] = {}
        r = _zfs(["list", "-H", "-p", "-o", "name,mountpoint", "-t", "filesystem"])
        if r.returncode != 0:
            return out
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                out[parts[0]] = parts[1]
        return out

    def _live_mountpoints(self) -> dict[str, str]:
        """Map dataset name -> its CURRENT mount target from /proc/self/mountinfo.

        This is the source of truth for ``mountpoint=legacy`` datasets (mounted
        via fstab/manually, so the ZFS property is "legacy" but the snapshot is
        reachable under the real mount). For property-mounted datasets it agrees
        with the property.
        """
        out: dict[str, str] = {}
        try:
            text = Path("/proc/self/mountinfo").read_text()
        except OSError:
            return out
        for line in text.splitlines():
            # mountinfo: <id> <parent> <maj:min> <root> <mountpoint> ... - <fstype> <source> <opts>
            sep = line.find(" - ")
            if sep == -1:
                continue
            pre = line[:sep].split()
            post = line[sep + 3:].split()
            if len(pre) < 5 or len(post) < 2:
                continue
            fstype, source = post[0], post[1]
            if fstype != "zfs":
                continue
            out[_unescape_mountinfo(source)] = _unescape_mountinfo(pre[4])
        return out

    def discover(self) -> list[DiscoveredSnapshot]:
        prop_mounts = self._property_mountpoints()
        live_mounts = self._live_mountpoints()

        def resolve_mountpoint(dataset: str) -> str | None:
            # Prefer the live mount table (handles legacy + agrees otherwise).
            mp = live_mounts.get(dataset)
            if mp:
                return mp
            mp = prop_mounts.get(dataset)
            if mp is None or mp in _NON_PATH_MOUNTPOINTS:
                return None
            return mp

        snaps: list[DiscoveredSnapshot] = []
        r = _zfs(["list", "-H", "-o", "name", "-t", "snapshot"])
        if r.returncode != 0:
            return snaps
        for line in r.stdout.splitlines():
            line = line.strip()
            if "@" not in line:
                continue
            dataset, snapname = line.split("@", 1)
            mp = resolve_mountpoint(dataset)
            if mp is None:
                # Truly unmounted dataset: no .zfs/snapshot path we can walk
                # without mounting; skip rather than guess.
                continue
            data_root = Path(mp) / ".zfs" / "snapshot" / snapname
            snaps.append(
                DiscoveredSnapshot(
                    name=line,
                    data_root=data_root,
                    needs_mount=False,
                    backend_state={"dataset": dataset, "snapshot": snapname},
                )
            )
        return snaps
