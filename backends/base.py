"""SnapshotBackend abstraction shared by every Linux snapshot adapter.

The orchestrator talks to backends only through this interface. Each backend
reports what its own tooling reports; cross-backend overlap handling lives in
the orchestrator, not here (see the v1 directive's overlap-resolution rules).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DiscoveredSnapshot:
    """One snapshot the backend has located on disk."""

    name: str              # human-readable snapshot identifier
    data_root: Path        # absolute path to the snapshot's filesystem root
    needs_mount: bool      # True if the backend must mount/unmount around use
    backend_state: dict    # opaque to the orchestrator; backend uses for cleanup


class SnapshotBackend(ABC):
    """Adapter for one Linux snapshot/backup mechanism."""

    name: str  # short identifier matching --backend flag value

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this backend's tooling is installed and accessible.

        Should NOT check whether snapshots exist; only whether the backend
        itself is usable. Used for --list-backends and auto-discovery.
        """
        ...

    @abstractmethod
    def discover(self) -> list[DiscoveredSnapshot]:
        """Find all snapshots this backend can reach.

        Implementations report what their tooling reports — no cross-backend
        overlap handling here. The orchestrator deduplicates across backends
        after discovery using the directive's overlap-resolution table.

        For snapshot mechanisms where snapshots are auto-mounted (ZFS),
        return them with needs_mount=False. For mechanisms requiring explicit
        mount (FUSE-based borg/restic, when implemented), return with
        needs_mount=True; ensure_mounted() will be called before indexing.
        """
        ...

    def ensure_mounted(self, snap: DiscoveredSnapshot) -> Path:
        """Mount the snapshot if needed; return the usable data_root path.

        Default impl (for auto-mounted backends) is a no-op returning
        snap.data_root. Backends needing FUSE mount override this.
        """
        return snap.data_root

    def cleanup(self, snap: DiscoveredSnapshot) -> None:  # noqa: B027
        """Unmount or release the snapshot if we mounted it.

        Intentionally a non-abstract no-op: auto-mounted backends (ZFS) need
        no teardown and must not be forced to implement this. Backends doing
        FUSE mount override it.
        """
        return None
