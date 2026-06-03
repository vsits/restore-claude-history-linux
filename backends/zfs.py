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
from datetime import datetime, timezone
from pathlib import Path

from backends._mountinfo import mounts_of_fstype
from backends.base import DiscoveredSnapshot, SnapshotBackend

# Mountpoint values that are not real on-disk paths.
_NON_PATH_MOUNTPOINTS = {"none", "legacy", "-"}


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
        with the property. For ZFS, mountinfo's mount source is the dataset name.
        """
        return {m.source: m.mountpoint for m in mounts_of_fstype("zfs")}

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
        # `-p` makes `creation` a Unix epoch (parseable), not human-formatted text.
        r = _zfs(["list", "-H", "-p", "-o", "name,creation", "-t", "snapshot"])
        if r.returncode != 0:
            return snaps
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            full_name, creation_str = parts[0], parts[1]
            if "@" not in full_name:
                continue
            dataset, snapname = full_name.split("@", 1)
            mp = resolve_mountpoint(dataset)
            if mp is None:
                # Truly unmounted dataset: no .zfs/snapshot path we can walk
                # without mounting; skip rather than guess.
                continue
            try:
                created_at = datetime.fromtimestamp(int(creation_str), tz=timezone.utc)
            except (ValueError, OSError):
                # Unparseable creation time — skip rather than report with a
                # sentinel; the directive's contract is "no None case."
                continue
            data_root = Path(mp) / ".zfs" / "snapshot" / snapname
            snaps.append(
                DiscoveredSnapshot(
                    name=full_name,
                    data_root=data_root,
                    needs_mount=False,
                    backend_state={"dataset": dataset, "snapshot": snapname},
                    created_at=created_at,
                )
            )
        return snaps
