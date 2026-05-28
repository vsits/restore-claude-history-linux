"""LocalDirBackend — test-only fake backend.

Treats ordinary directories as if they were snapshot roots. Used by the
Layer 1 orchestrator tests (ambiguity / auto-selection / overlap resolution)
and the Layer 2 tempdir-based restore tests. Never registered in production.

Each "snapshot" data_root is expected to contain a ``.claude/projects/``
tree, exactly as a real snapshot of a user's home directory would.
"""

from __future__ import annotations

from collections.abc import Iterable
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
            self._snapshots = [
                DiscoveredSnapshot(
                    name=f"{name}-{i}",
                    data_root=Path(r),
                    needs_mount=False,
                    backend_state={},
                )
                for i, r in enumerate(roots or [])
            ]

    def is_available(self) -> bool:
        return self._available

    def discover(self) -> list[DiscoveredSnapshot]:
        return list(self._snapshots)
