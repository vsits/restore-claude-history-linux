"""LocalDirBackend — test-only fake backend.

Treats ordinary directories as if they were snapshot roots. Used by the
Layer 1 orchestrator tests (ambiguity / auto-selection / overlap resolution)
and the Layer 2 tempdir-based restore tests. Never registered in production.

Each "snapshot" data_root is expected to contain a ``.claude/projects/``
tree, exactly as a real snapshot of a user's home directory would.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backends.base import DiscoveredSnapshot, SnapshotBackend


class LocalDirBackend(SnapshotBackend):
    def __init__(
        self,
        name: str = "local",
        roots: Iterable[Path | str] | None = None,
        *,
        available: bool = True,
        snapshots: list[DiscoveredSnapshot] | None = None,
    ) -> None:
        self.name = name
        self._available = available
        if snapshots is not None:
            self._snapshots = snapshots
        else:
            # Synthesize created_at so the orchestrator's newest-first
            # sort has a stable order: index N → 2026-01-01 + N hours UTC.
            # Tests that need a specific order construct snapshots
            # directly with snapshots=...
            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self._snapshots = [
                DiscoveredSnapshot(
                    name=f"{name}-{i}",
                    data_root=Path(r),
                    needs_mount=False,
                    backend_state={},
                    created_at=base + timedelta(hours=i),
                )
                for i, r in enumerate(roots or [])
            ]

    def is_available(self) -> bool:
        return self._available

    def discover(self) -> list[DiscoveredSnapshot]:
        return list(self._snapshots)
