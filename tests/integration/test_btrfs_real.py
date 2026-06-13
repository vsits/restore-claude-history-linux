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
from restore_claude_code import Options, run_restore

MOUNT = os.environ.get("RCB_BTRFS_TEST_MOUNT")

pytestmark = pytest.mark.skipif(
    not MOUNT or BtrfsBackend().is_available() is False,
    reason="set RCB_BTRFS_TEST_MOUNT to a writable Btrfs mountpoint to run",
)

PROJECT = "-rcb-integration-demo"


def _btrfs(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["btrfs", *args], capture_output=True, text=True, check=True)


def test_btrfs_snapshot_is_discovered():
    """Lightweight smoke: discover() surfaces a snapshot we just created."""
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


def test_btrfs_snapshot_full_restore(tmp_path):
    """End-to-end: write transcript into a subvol, snapshot, delete, restore.

    Mirrors the ZFS Layer 3 shape. The fixture is planted directly under the
    subvolume root (`<subvol>/.claude/projects/<proj>/`) so the orchestrator's
    `locate_projects_dir` finds it via the empty-suffix fallback — same pattern
    the ZFS test relies on. The payload bytes embed a per-run unique token so a
    stale snapshot from a prior run that didn't clean up cannot accidentally
    satisfy the byte-equality assertion.
    """
    base = Path(MOUNT)
    tag = f"rcbrestore-{int(time.time())}-{os.getpid()}"
    subvol = base / f"sv-{tag}"
    snap = base / f"snap-{tag}"
    # Per-run-unique payload: the tag makes stale-snapshot collisions detectable.
    payload = f"complete transcript body for {tag}\n".encode() * 20

    # Diagnostic anchor: which snapshot data_roots existed BEFORE we created
    # ours. If the post-restore bytes match payload, the only way that's
    # possible is from a snapshot we just created (the tag is unique).
    snaps_before = {str(s.data_root) for s in BtrfsBackend().discover()}

    try:
        _btrfs("subvolume", "create", str(subvol))
        proj = subvol / ".claude" / "projects" / PROJECT
        proj.mkdir(parents=True)
        live = proj / "session.jsonl"
        live.write_bytes(payload)

        _btrfs("subvolume", "snapshot", "-r", str(subvol), str(snap))

        # Confirm a NEW snapshot is in the inventory, so the restore is reading
        # the one we just created (not a leftover from a prior run).
        snaps_after = {str(s.data_root) for s in BtrfsBackend().discover()}
        new = snaps_after - snaps_before
        assert new, "no new Btrfs snapshot appeared after subvolume snapshot"

        # Simulate the deletion we recover from.
        live.unlink()
        assert not live.exists()

        # Restore into the live tree's projects dir (dest override). The
        # backend's discover() returns the snapshot; run_restore walks it,
        # finds .claude/projects under the snapshot root, copies the file.
        dest = subvol / ".claude" / "projects"
        rc = run_restore([BtrfsBackend()], Options(backend="btrfs", dest=dest))
        assert rc == 0

        assert live.exists(), "restore did not recover the deleted transcript"
        assert live.read_bytes() == payload, "restored bytes differ from snapshot"
    finally:
        subprocess.run(["btrfs", "subvolume", "delete", str(snap)],
                       capture_output=True, text=True)
        subprocess.run(["btrfs", "subvolume", "delete", str(subvol)],
                       capture_output=True, text=True)
