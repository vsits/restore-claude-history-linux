"""Pluggable Linux snapshot backends for restore-claude-history-linux."""

from __future__ import annotations

from backends.base import DiscoveredSnapshot, SnapshotBackend
from backends.btrfs import BtrfsBackend
from backends.timeshift import TimeshiftBackend
from backends.zfs import ZfsBackend

__all__ = [
    "DiscoveredSnapshot",
    "SnapshotBackend",
    "ZfsBackend",
    "BtrfsBackend",
    "TimeshiftBackend",
    "default_registry",
]


def default_registry() -> list[SnapshotBackend]:
    """Backends wired into production runs (v1: ZFS, Btrfs, Timeshift).

    Registering Timeshift activates the orchestrator's overlap-resolution pass
    for Timeshift-on-Btrfs (the `timeshift > btrfs` ownership entry). The
    test-only LocalDirBackend is never registered.
    """
    return [ZfsBackend(), BtrfsBackend(), TimeshiftBackend()]
