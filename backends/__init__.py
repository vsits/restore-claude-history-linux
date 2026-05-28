"""Pluggable Linux snapshot backends for restore-claude-history-linux."""

from __future__ import annotations

from backends.base import DiscoveredSnapshot, SnapshotBackend
from backends.zfs import ZfsBackend

__all__ = [
    "DiscoveredSnapshot",
    "SnapshotBackend",
    "ZfsBackend",
    "default_registry",
]


def default_registry() -> list[SnapshotBackend]:
    """Backends wired into production runs.

    Phase 1 ships ZFS only. Btrfs (Phase 2) and Timeshift (Phase 3) append
    here as they land. The test-only LocalDirBackend is never registered.
    """
    return [ZfsBackend()]
