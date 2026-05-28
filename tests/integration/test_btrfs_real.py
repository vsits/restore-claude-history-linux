"""Layer 3: real Btrfs integration test (opt-in).

Skipped unless RCB_BTRFS_TEST_MOUNT names a writable Btrfs mountpoint you
control AND the btrfs CLI works (snapshot creation + `subvolume list` normally
require root). See tests/integration/README.md for setup. This creates and
deletes a subvolume + snapshot under that mount — point it only at a throwaway
test filesystem.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from backends.btrfs import BtrfsBackend

MOUNT = os.environ.get("RCB_BTRFS_TEST_MOUNT")

pytestmark = pytest.mark.skipif(
    not MOUNT or BtrfsBackend().is_available() is False,
    reason="set RCB_BTRFS_TEST_MOUNT to a writable Btrfs mountpoint to run",
)


def _btrfs(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["btrfs", *args], capture_output=True, text=True, check=True)


def test_btrfs_snapshot_is_discovered():
    base = Path(MOUNT)
    tag = f"rcbtest-{int(time.time())}"
    subvol = base / f"sv-{tag}"
    snap = base / f"snap-{tag}"
    try:
        _btrfs("subvolume", "create", str(subvol))
        (subvol / "marker").write_text("hi")
        _btrfs("subvolume", "snapshot", "-r", str(subvol), str(snap))

        snaps = BtrfsBackend().discover()
        # The discovered snapshot's data_root should be readable and contain
        # the marker we wrote before snapshotting.
        matches = [s for s in snaps if (s.data_root / "marker").is_file()]
        assert matches, "discover() did not surface the snapshot we just created"
    finally:
        subprocess.run(["btrfs", "subvolume", "delete", str(snap)],
                       capture_output=True, text=True)
        subprocess.run(["btrfs", "subvolume", "delete", str(subvol)],
                       capture_output=True, text=True)
