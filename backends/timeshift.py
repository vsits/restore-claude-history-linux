"""Timeshift snapshot backend.

Timeshift is Ubuntu's default backup tool. It runs in one of two modes,
recorded in ``/etc/timeshift/timeshift.json``:

- **RSYNC mode** — snapshots are plain directory trees at
  ``/timeshift/snapshots/<timestamp>/localhost/`` (a full root-filesystem copy).
- **BTRFS mode** — snapshots are Btrfs subvolumes (``@``, ``@home``) under a
  ``timeshift-btrfs/snapshots/<timestamp>/`` directory on the backup device,
  reachable at ``/timeshift-btrfs/snapshots`` or, while Timeshift has the
  device mounted, ``/run/timeshift/<pid>/backup/timeshift-btrfs/snapshots``.

This backend scans those locations and reports each snapshot's data root.
needs_mount=False: it reads snapshots that are already on disk and never mounts
the backup device itself (if BTRFS-mode snapshots aren't currently exposed,
they're simply not found — v1 does not mount on the user's behalf).

Per the v1 directive, Timeshift OWNS Timeshift-on-Btrfs snapshots: the
orchestrator prunes a Btrfs-backend duplicate of the same canonical path in
``auto`` mode (the overlap pass, now active because this backend is
registered). The backend itself does no cross-backend handling.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from backends.base import DiscoveredSnapshot, SnapshotBackend


def _parse_created_at(ts_dir: Path) -> datetime | None:
    """Determine the snapshot's creation time.

    Per v1.1 directive: ``info.json``'s ``created`` epoch is the authoritative
    UTC source. Falls back to the snapshot directory name (``YYYY-MM-DD_HH-MM-SS``,
    Timeshift writes this from ``DateTime.now_local()`` — local-time,
    DST-vulnerable). Returns None if neither parses.
    """
    info_json = ts_dir / "info.json"
    if info_json.is_file():
        try:
            with info_json.open("r") as fh:
                data = json.load(fh)
            created_str = data.get("created")
            if created_str is not None:
                # Timeshift writes either an epoch or an ISO-formatted date.
                # Try both shapes.
                try:
                    return datetime.fromtimestamp(int(created_str), tz=timezone.utc)
                except (ValueError, TypeError):
                    pass
                try:
                    return datetime.strptime(
                        created_str, "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
        except (OSError, json.JSONDecodeError):
            pass

    # Fallback: snapshot dir name. WARNING: this is local-time per Timeshift's
    # `DateTime.now_local()`, so cross-timezone correctness is best-effort.
    try:
        dt_local = datetime.strptime(ts_dir.name, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None
    print(
        f"timeshift: warning: {ts_dir.name} has no usable info.json; "
        f"using dir-name timestamp (treated as local time, DST-vulnerable). "
        f"Ordering across timezone boundaries may be approximate.",
        file=sys.stderr,
    )
    return dt_local.astimezone(timezone.utc)

_CONFIG_PATH = Path("/etc/timeshift/timeshift.json")
# Persistent locations Timeshift exposes snapshots at (RSYNC + BTRFS modes).
_SNAPSHOT_BASES = (
    Path("/timeshift/snapshots"),
    Path("/timeshift-btrfs/snapshots"),
)
# Per-snapshot subdirectories that hold the filesystem root, in probe order.
_DATA_SUBDIRS = ("localhost", "@home", "@")


class TimeshiftBackend(SnapshotBackend):
    name = "timeshift"

    def __init__(
        self,
        config_path: Path = _CONFIG_PATH,
        snapshot_bases: tuple[Path, ...] = _SNAPSHOT_BASES,
        runtime_root: Path = Path("/run/timeshift"),
    ) -> None:
        self.config_path = config_path
        self.snapshot_bases = snapshot_bases
        self.runtime_root = runtime_root

    def _load_config(self) -> dict | None:
        try:
            return json.loads(self.config_path.read_text())
        except (OSError, ValueError):
            return None

    def is_available(self) -> bool:
        """True when Timeshift is configured (its config file parses).

        Config presence is the "Timeshift is set up on this host" signal; we
        do not check whether snapshots exist.
        """
        return self._load_config() is not None

    def _runtime_base_dirs(self) -> list[Path]:
        """Snapshot bases Timeshift exposes while it has the backup device
        mounted. Covers both the non-PID (`/run/timeshift/backup`) and PID
        (`/run/timeshift/<pid>/backup`) layouts, for both RSYNC and BTRFS."""
        if not self.runtime_root.is_dir():
            return []
        backups = [self.runtime_root / "backup"]
        backups.extend(sorted(self.runtime_root.glob("*/backup")))
        out: list[Path] = []
        for b in backups:
            out.append(b / "timeshift" / "snapshots")
            out.append(b / "timeshift-btrfs" / "snapshots")
        return out

    def _snapshot_base_dirs(self) -> list[Path]:
        bases = list(self.snapshot_bases) + self._runtime_base_dirs()
        return [b for b in bases if b.is_dir()]

    @staticmethod
    def _snapshot_data_roots(ts_dir: Path) -> list[Path]:
        """Every filesystem root inside a snapshot timestamp dir.

        RSYNC -> [<ts>/localhost]; BTRFS -> [<ts>/@home, <ts>/@] when both
        exist (each is a separate subvolume Btrfs also reports, so Timeshift
        must claim BOTH for auto-mode dedup to fully prune the Btrfs peer).
        Falls back to the timestamp dir itself when no known subdir exists.
        """
        roots = [ts_dir / sub for sub in _DATA_SUBDIRS if (ts_dir / sub).is_dir()]
        return roots or [ts_dir]

    def discover(self) -> list[DiscoveredSnapshot]:
        if self._load_config() is None:
            return []
        snaps: list[DiscoveredSnapshot] = []
        seen: set[str] = set()
        for base in self._snapshot_base_dirs():
            for ts_dir in sorted(base.iterdir()):
                if not ts_dir.is_dir():
                    continue
                created_at = _parse_created_at(ts_dir)
                if created_at is None:
                    # No usable creation time. Per directive contract, skip
                    # rather than emit a sentinel.
                    continue
                for data_root in self._snapshot_data_roots(ts_dir):
                    key = os.path.realpath(str(data_root))
                    if key in seen:
                        continue
                    seen.add(key)
                    label = (ts_dir.name if data_root == ts_dir
                             else f"{ts_dir.name}/{data_root.name}")
                    snaps.append(DiscoveredSnapshot(
                        name=label,
                        data_root=data_root,
                        needs_mount=False,
                        backend_state={
                            "timestamp": ts_dir.name,
                            "subvol": data_root.name,
                            "base": str(base),
                        },
                        created_at=created_at,
                    ))
        return snaps
