#!/usr/bin/env python3
"""
verify_restore.py — Layer 2 end-to-end check for restore_claude_code.py

Ported from upstream's macOS Time Machine verifier to the Linux backend model.
Builds synthetic "snapshots" as tempdirs via LocalDirBackend (no real ZFS /
Btrfs / Timeshift needed), simulates a deletion on the live tree, runs the
restore loop, and asserts each file came back with the LARGEST size, correct
historical mtime, and no leftover ACL.

Usage:
    python3 tests/verify_restore.py
    python3 tests/verify_restore.py --keep   # leave sandbox for inspection
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backends._local_dir import LocalDirBackend  # noqa: E402
from restore_claude_code import Options, run_restore  # noqa: E402

PROJECT = "-home-user-projects-demo"
# name -> (small_size, large_size, mtime). The large version lives in the
# newer snapshot and must be the one restored.
FIXTURES = {
    "session-a.jsonl": (200, 900, 1_600_000_000.0),
    "session-b.jsonl": (50, 4096, 1_600_100_000.0),
    "session-c.jsonl": (1024, 8192, 1_600_200_000.0),
}


@dataclass
class Expect:
    name: str
    size: int
    mtime: float


def _write(path: Path, size: int, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    import os
    os.utime(path, (mtime, mtime))


def has_acl(path: Path) -> bool:
    """getfacl shows more than the three base entries when an ACL is set."""
    try:
        r = subprocess.run(["getfacl", "-c", str(path)],
                           capture_output=True, text=True)
    except FileNotFoundError:
        return False  # getfacl not installed
    if r.returncode != 0:
        return False  # fs without ACL support
    entries = [ln for ln in r.stdout.splitlines()
               if ln and not ln.startswith("#")]
    extra = [ln for ln in entries
             if not re.match(r"^(user|group|other)::", ln)]
    return bool(extra)


def die(msg: str) -> NoReturn:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true",
                        help="leave the sandbox in /tmp after the run")
    args = parser.parse_args()

    sandbox = Path(tempfile.mkdtemp(prefix="claude-restore-verify-"))
    print(f"[setup]  sandbox: {sandbox}")

    # Two snapshots: an older one with small files, a newer one with the large
    # (most complete) versions carrying the historical mtimes we expect back.
    old_snap = sandbox / "snap-old"
    new_snap = sandbox / "snap-new"
    expects: list[Expect] = []
    for name, (small, large, mtime) in FIXTURES.items():
        _write(old_snap / ".claude" / "projects" / PROJECT / name, small, mtime - 10)
        _write(new_snap / ".claude" / "projects" / PROJECT / name, large, mtime)
        expects.append(Expect(name=name, size=large, mtime=mtime))
    print(f"[setup]  built {len(expects)} files across 2 snapshots")

    # Live tree: files are missing (the deletion we're recovering from).
    dest = sandbox / "live"
    (dest / PROJECT).mkdir(parents=True)

    registry = [LocalDirBackend("local", roots=[old_snap, new_snap])]
    print("[run]    run_restore(--backend auto)")
    rc = run_restore(registry, Options(backend="auto", dest=dest))
    if rc != 0:
        die(f"restore returned {rc}")

    failures: list[str] = []
    for exp in expects:
        restored = dest / PROJECT / exp.name
        if not restored.exists():
            failures.append(f"missing: {restored}")
            continue
        st = restored.stat()
        if st.st_size != exp.size:
            failures.append(f"size mismatch on {exp.name}: got {st.st_size}, "
                            f"want {exp.size}")
        if abs(st.st_mtime - exp.mtime) > 1.0:
            failures.append(f"mtime drift on {exp.name}: got {st.st_mtime}, "
                            f"want {exp.mtime}")
        if has_acl(restored):
            failures.append(f"ACL still present on {exp.name}")

    if args.keep:
        print(f"[keep]   sandbox left at {sandbox}")
    else:
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)
        print(f"[clean]  removed {sandbox}")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"\nPASS: {len(expects)} files restored with largest size, correct "
          f"mtime, no ACL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
