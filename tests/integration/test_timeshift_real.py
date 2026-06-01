"""Layer 3: real Timeshift integration test (opt-in).

Skipped unless RCB_TIMESHIFT_TEST_BASE names a directory laid out like a
Timeshift snapshots store (``<base>/<timestamp>/localhost/`` for RSYNC mode, or
``<base>/<timestamp>/@home`` etc. for BTRFS mode) AND a Timeshift config exists.
See tests/integration/README.md. This is read-only; it does not invoke
Timeshift or mount anything.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backends.timeshift import TimeshiftBackend

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
