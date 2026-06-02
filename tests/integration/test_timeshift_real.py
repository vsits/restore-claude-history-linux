"""Layer 3: real Timeshift integration test (opt-in).

Skipped unless RCB_TIMESHIFT_TEST_BASE names a directory laid out like a
Timeshift snapshots store (``<base>/<timestamp>/localhost/`` for RSYNC mode, or
``<base>/<timestamp>/@home`` etc. for BTRFS mode) AND a Timeshift config exists.
See tests/integration/README.md.

The discovery test is read-only. The full-restore test exercises the
end-to-end path: a fixture is planted under the live tree before
``timeshift --create`` is invoked (the e2e harness's cloud-init does that),
then the live file is deleted, then the orchestrator restores from the
snapshot.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backends.timeshift import TimeshiftBackend
from restore_claude_history import Options, run_restore

BASE = os.environ.get("RCB_TIMESHIFT_TEST_BASE")
CONFIG = os.environ.get("RCB_TIMESHIFT_TEST_CONFIG", "/etc/timeshift/timeshift.json")

pytestmark = pytest.mark.skipif(
    not BASE or not Path(CONFIG).exists(),
    reason="set RCB_TIMESHIFT_TEST_BASE (+ a Timeshift config) to run",
)


def test_timeshift_discovers_snapshots():
    backend = TimeshiftBackend(config_path=Path(CONFIG),
                               snapshot_bases=(Path(BASE),))
    assert backend.is_available()
    snaps = backend.discover()
    assert snaps, "no Timeshift snapshots discovered under the configured base"
    # Each reported data_root must be a real, readable directory on disk.
    for s in snaps:
        assert s.data_root.is_dir(), f"data_root not a dir: {s.data_root}"
        assert s.needs_mount is False


def test_timeshift_full_restore():
    """End-to-end: fixture planted before ``timeshift --create``, deleted, restored.

    Preconditions (set up by the e2e harness's cloud-init):
      - ``RCB_TIMESHIFT_FIXTURE_PATH`` points at a ``.jsonl`` under the live
        home (e.g. ``/root/.claude/projects/<PROJECT>/session.jsonl``).
      - ``RCB_TIMESHIFT_FIXTURE_BYTES_PATH`` points at a separate file holding
        the expected bytes so we can compare even after the live file is
        unlinked.
      - ``timeshift --create`` has already run after the fixture was written,
        so the snapshot store under ``RCB_TIMESHIFT_TEST_BASE`` contains a copy.

    The test deletes the live file, runs ``run_restore``, and asserts the bytes
    come back from the snapshot.
    """
    fixture_path = os.environ.get("RCB_TIMESHIFT_FIXTURE_PATH")
    fixture_bytes_path = os.environ.get("RCB_TIMESHIFT_FIXTURE_BYTES_PATH")
    if not (fixture_path and fixture_bytes_path):
        pytest.skip("set RCB_TIMESHIFT_FIXTURE_PATH + RCB_TIMESHIFT_FIXTURE_BYTES_PATH "
                    "after the e2e harness pre-stages the fixture")

    live = Path(fixture_path)
    expected = Path(fixture_bytes_path).read_bytes()

    # Sanity: the fixture must still be on disk pre-test, otherwise something
    # raced us (e.g. cleanup) and the test would be meaningless.
    assert live.exists(), f"fixture missing pre-test: {live}"
    assert live.read_bytes() == expected, "live fixture bytes drifted before test"

    # Simulate the deletion we recover from.
    live.unlink()
    assert not live.exists()

    # discover() should find the Timeshift snapshot taken after we planted the
    # fixture. The snapshot mirrors the live tree under <base>/<ts>/localhost/,
    # so locate_projects_dir(<localhost>, $HOME) finds .claude/projects under
    # the home-suffix probe.
    backend = TimeshiftBackend(config_path=Path(CONFIG),
                               snapshot_bases=(Path(BASE),))
    snaps = backend.discover()
    assert snaps, "no Timeshift snapshots discovered post-create"

    # Diagnostic: before invoking run_restore, find a discovered snapshot that
    # actually contains our fixture. discover() can return multiple snapshots
    # (a prior run that didn't clean up, or a retry); pinning on snaps[0] would
    # let a stale snapshot mask a Timeshift-RSYNC-scope problem with the
    # fixture we just planted. Scan all and require at least one match.
    expected_relpath = Path("home/ubuntu/.claude/projects") / "-rcb-integration-demo" \
        / "session.jsonl"
    matching = [s for s in snaps if (s.data_root / expected_relpath).is_file()]
    if not matching:
        # Surface what IS in each snapshot so the failure is debuggable.
        import subprocess as _sp
        listing_per_snap = []
        for s in snaps:
            # `find` boolean precedence: -name A -o -name B -print only
            # prints B-matches. Wrap the OR in parens so -print fires on both.
            # -maxdepth 6 reaches both `.claude` (depth 4) and the
            # `session.jsonl` (depth 6) under <localhost>/home/ubuntu/.claude/
            # projects/<PROJECT>/. Without -maxdepth, find descends into
            # arbitrarily-deep snapshot trees and the listing becomes useless.
            out = _sp.run(
                ["find", str(s.data_root), "-maxdepth", "6",
                 "(", "-name", "*.jsonl", "-o", "-name", ".claude", ")",
                 "-print"],
                capture_output=True, text=True,
            ).stdout
            listing_per_snap.append(f"  data_root={s.data_root}\n{out[:2000]}")
        pytest.fail(
            f"fixture not in any of {len(snaps)} snapshot(s):\n"
            + "\n".join(listing_per_snap)
        )

    # Restore into the live tree's projects dir (dest override). Drives the
    # restore loop end-to-end against the real Timeshift backend (not a fake),
    # exercising locate + pick_largest + restore_file as production does.
    dest = Path.home() / ".claude" / "projects"
    rc = run_restore([backend], Options(backend="timeshift", dest=dest))
    assert rc == 0

    # Give the filesystem a moment to settle. On the harness's loop image this
    # is instant; the small tolerance is cheap insurance for real disks.
    for _ in range(5):
        if live.exists():
            break
        time.sleep(0.2)

    assert live.exists(), "restore did not recover the deleted transcript"
    assert live.read_bytes() == expected, "restored bytes differ from snapshot"
