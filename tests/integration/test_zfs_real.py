"""Layer 3: real ZFS integration test (opt-in).

Skipped unless RCB_ZFS_TEST_DATASET names a writable dataset you control AND
the zfs CLI works. See tests/integration/README.md for setup. This creates and
destroys snapshots on that dataset — point it only at a throwaway test pool.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from backends.zfs import ZfsBackend
from restore_claude_history import Options, run_restore

DATASET = os.environ.get("RCB_ZFS_TEST_DATASET")

pytestmark = pytest.mark.skipif(
    not DATASET or ZfsBackend().is_available() is False,
    reason="set RCB_ZFS_TEST_DATASET to a writable ZFS dataset to run",
)

PROJECT = "-rcb-integration-demo"


def _zfs(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["zfs", *args], capture_output=True, text=True, check=True)


def _mountpoint(dataset: str) -> Path:
    out = subprocess.run(
        ["zfs", "list", "-H", "-o", "mountpoint", dataset],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(out)


def test_zfs_snapshot_discover_and_restore():
    mp = _mountpoint(DATASET)
    snapname = f"rcbtest-{int(time.time())}"
    proj = mp / ".claude" / "projects" / PROJECT
    live = proj / "session.jsonl"
    try:
        proj.mkdir(parents=True, exist_ok=True)
        live.write_bytes(b"complete transcript body" * 10)

        _zfs("snapshot", f"{DATASET}@{snapname}")

        # Simulate the deletion we recover from.
        live.unlink()

        snaps = ZfsBackend().discover()
        assert any(s.name == f"{DATASET}@{snapname}" for s in snaps), \
            "discover() did not find the snapshot we just created"

        rc = run_restore([ZfsBackend()], Options(backend="zfs", dest=mp / ".claude" / "projects"))
        assert rc == 0
        assert live.exists(), "restore did not recover the deleted transcript"
        assert live.read_bytes() == b"complete transcript body" * 10
    finally:
        subprocess.run(["zfs", "destroy", f"{DATASET}@{snapname}"],
                       capture_output=True, text=True)
        if live.exists():
            live.unlink()
