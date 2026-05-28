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

    def _dataset_mountpoints(self) -> dict[str, str]:
        """Map filesystem dataset name -> mountpoint."""
        out: dict[str, str] = {}
        r = _zfs(["list", "-H", "-p", "-o", "name,mountpoint", "-t", "filesystem"])
        if r.returncode != 0:
            return out
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                out[parts[0]] = parts[1]
        return out

    def discover(self) -> list[DiscoveredSnapshot]:
        mountpoints = self._dataset_mountpoints()
        snaps: list[DiscoveredSnapshot] = []
        r = _zfs(["list", "-H", "-o", "name", "-t", "snapshot"])
        if r.returncode != 0:
            return snaps
        for line in r.stdout.splitlines():
            line = line.strip()
            if "@" not in line:
                continue
            dataset, snapname = line.split("@", 1)
            mp = mountpoints.get(dataset)
            if mp is None or mp in _NON_PATH_MOUNTPOINTS:
                # Unmounted/legacy datasets have no .zfs/snapshot path we can
                # walk without mounting; skip rather than guess.
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
