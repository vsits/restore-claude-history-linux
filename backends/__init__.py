"""Pluggable Linux snapshot backends for restore-claude-history-linux."""

from __future__ import annotations

from backends.base import DiscoveredSnapshot, SnapshotBackend
from backends.btrfs import BtrfsBackend
from backends.zfs import ZfsBackend

__all__ = [
    "DiscoveredSnapshot",
    "SnapshotBackend",
    "ZfsBackend",
    "BtrfsBackend",
    "default_registry",
]


def default_registry() -> list[SnapshotBackend]:
    """Backends wired into production runs.

    Phases 1-2 ship ZFS + Btrfs. Timeshift (Phase 3) appends here as it lands.
    The test-only LocalDirBackend is never registered.
    """
    return [ZfsBackend(), BtrfsBackend()]
