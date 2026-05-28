#!/usr/bin/env python3
"""
restore_claude_history.py

Recover deleted Claude Code chat transcripts (~/.claude/projects/<project>/*.jsonl)
from Linux filesystem snapshots (ZFS / Btrfs / Timeshift / ...).

For each (project, filename) seen across all snapshots, picks the LARGEST
version (JSONLs are append-only, so bigger == more complete) and copies it
back, preserving mtime and stripping any inherited ACL.

Linux port of garrettmoss/restore-claude-history (macOS Time Machine). The
recovery logic is unchanged; only the snapshot-discovery layer is replaced
with a pluggable backend abstraction. See docs/ and AGENTS.md for background.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from backends import default_registry
from backends.base import DiscoveredSnapshot, SnapshotBackend

# Overlap ownership for --backend auto deduplication. Each (owner, peer) pair
# means: when both backends return a snapshot at the same canonical path, keep
# the owner's entry and prune the peer's duplicate. Entries for backends not
# yet registered are harmless no-ops. See the v1 directive's
# "Backend-overlap resolution rules".
DEFAULT_OWNERSHIP: list[tuple[str, str]] = [
    ("timeshift", "btrfs"),   # Timeshift config carries snapshot intent (Phase 3)
    ("snapper", "btrfs"),     # future: Snapper config carries snapshot intent
]


# -------- types --------


@dataclass
class JsonlEntry:
    """One JSONL found inside a snapshot."""

    project: str             # encoded project dir name
    filename: str            # <uuid>.jsonl
    size: int
    src: Path                # absolute path inside the snapshot


@dataclass
class Options:
    backend: str = "auto"
    list_backends: bool = False
    dry_run: bool = False
    project: str | None = None
    include_memory: bool = False
    verbose: bool = False
    dest: Path | None = None


# -------- shell helpers --------


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, capture stdout/stderr as text."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def die(msg: str) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# -------- backend orchestration --------


def canonical(path: Path) -> str:
    """Canonicalize a snapshot path for cross-backend equality.

    os.path.realpath resolves symlinks + lexical normalization. Known v1
    limitation: it does NOT canonicalize bind-mount aliases — those survive as
    distinct paths (and correctly surface as duplicates / ambiguity). See the
    directive's canonicalization note.
    """
    return os.path.realpath(str(path))


def resolve_overlaps(
    discovered: dict[str, list[DiscoveredSnapshot]],
    ownership: list[tuple[str, str]],
) -> dict[str, list[DiscoveredSnapshot]]:
    """Deduplicate snapshots seen by multiple backends (auto mode only).

    Per-snapshot, exact-path-match pruning gated on positive claim: for each
    (owner, peer) pair, remove from the peer's list any snapshot whose
    canonical data_root matches a path the OWNER actually returned this run.
    An owner that returned nothing prunes nothing (closes the false-negative
    paths from Codex Rounds 3/4).
    """
    out = {name: list(snaps) for name, snaps in discovered.items()}
    for owner, peer in ownership:
        if owner not in out or peer not in out:
            continue
        owner_paths = {canonical(s.data_root) for s in out[owner]}
        if not owner_paths:
            continue
        out[peer] = [s for s in out[peer] if canonical(s.data_root) not in owner_paths]
    return out


def select_auto(
    discovered: dict[str, list[DiscoveredSnapshot]],
) -> tuple[str, list[DiscoveredSnapshot]]:
    """Apply --backend auto selection rules; die() on zero or ambiguous."""
    nonempty = {name: snaps for name, snaps in discovered.items() if snaps}
    if not nonempty:
        die(
            "no snapshots found on any backend. Check `--list-backends` and "
            "verify your snapshot tool (zfs/btrfs/timeshift) is installed and "
            "has snapshots."
        )
    if len(nonempty) > 1:
        lines = ["multiple backends found candidate snapshots; re-run with "
                 "--backend <name> to disambiguate:"]
        for name, snaps in sorted(nonempty.items()):
            example = snaps[0].data_root if snaps else ""
            lines.append(f"  --backend {name}: {len(snaps)} snapshot(s), e.g. {example}")
        die("\n".join(lines))
    name = next(iter(nonempty))
    return name, nonempty[name]


def list_backends(registry: list[SnapshotBackend]) -> int:
    """Print each backend's availability + discovered snapshot count."""
    for b in registry:
        available = b.is_available()
        count: int | str = 0
        if available:
            try:
                count = len(b.discover())
            except Exception:  # noqa: BLE001 - listing must never crash
                count = "?"
        print(f"{b.name:10}  available={str(available).lower():5}  snapshots={count}")
    return 0


# -------- data layout probing --------


def locate_projects_dir(data_root: Path, home: Path) -> Path | None:
    """Find ``.claude/projects`` inside a snapshot root.

    A snapshot's data_root may be the root of any filesystem that contains the
    user's home. Depending on what was snapshotted, projects live at one of:
      <data_root>/home/<user>/.claude/projects   (snapshot of /)
      <data_root>/<user>/.claude/projects         (snapshot of /home)
      <data_root>/.claude/projects                (snapshot of /home/<user>)
    We try progressively shorter suffixes of the home path and return the
    first that exists.
    """
    parts = home.parts
    rel = list(parts[1:]) if parts and parts[0] == os.sep else list(parts)
    for i in range(len(rel) + 1):
        candidate = data_root.joinpath(*rel[i:], ".claude", "projects")
        if candidate.is_dir():
            return candidate
    return None


# -------- indexing --------


def index_projects(
    projects_dir: Path,
    only_project: str | None,
) -> list[JsonlEntry]:
    """Walk one snapshot's projects dir and return every JSONL it contains."""
    entries: list[JsonlEntry] = []
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        if only_project and proj_dir.name != only_project:
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            try:
                size = jsonl.stat().st_size
            except OSError:
                continue
            entries.append(JsonlEntry(
                project=proj_dir.name,
                filename=jsonl.name,
                size=size,
                src=jsonl,
            ))
    return entries


def pick_largest(entries: list[JsonlEntry]) -> dict[tuple[str, str], JsonlEntry]:
    """For each (project, filename), keep the entry with the largest size."""
    best: dict[tuple[str, str], JsonlEntry] = {}
    for e in entries:
        key = (e.project, e.filename)
        cur = best.get(key)
        if cur is None or e.size > cur.size:
            best[key] = e
    return best


# -------- restore --------


def strip_acl_and_make_writable(path: Path) -> None:
    """Remove any inherited ACL and ensure the user can overwrite.

    ``setfacl -b`` clears ACLs on ext4/xfs/btrfs mounted with ACL support and
    is a harmless no-op where ACLs are absent. Missing setfacl (or a
    filesystem that rejects it) is non-fatal — we still fix the write bit.
    """
    try:
        run(["setfacl", "-b", str(path)], check=False)
    except FileNotFoundError:
        pass
    try:
        st = path.stat()
        path.chmod(st.st_mode | stat.S_IWUSR)
    except OSError:
        pass


def restore_file(
    entry: JsonlEntry, claude_dir: Path, dry_run: bool, verbose: bool
) -> tuple[bool, int]:
    """
    Restore one JSONL if the on-disk version is missing or smaller.
    Returns (restored, bytes).
    """
    dest_dir = claude_dir / entry.project
    dest = dest_dir / entry.filename

    if dest.exists():
        if dest.stat().st_size >= entry.size:
            if verbose:
                print(f"skip   {entry.project}/{entry.filename} "
                      f"(on-disk {dest.stat().st_size} >= snapshot {entry.size})")
            return (False, 0)

    if dry_run:
        print(f"would  {entry.project}/{entry.filename} ({entry.size} bytes)")
        return (True, entry.size)

    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        strip_acl_and_make_writable(dest)

    # shutil.copy2 preserves mtime via copystat; we still os.utime below in
    # case any ACL/permission shenanigans break copystat.
    shutil.copy2(entry.src, dest)
    try:
        src_stat = entry.src.stat()
        os.utime(dest, (src_stat.st_atime, src_stat.st_mtime))
    except OSError:
        pass
    strip_acl_and_make_writable(dest)

    if verbose:
        print(f"restore {entry.project}/{entry.filename} ({entry.size} bytes)")
    return (True, entry.size)


def restore_subdirs(
    located: list[tuple[str, Path]],
    claude_dir: Path,
    only_project: str | None,
    include_memory: bool,
    dry_run: bool,
    verbose: bool,
) -> int:
    """
    Restore per-session subdirs (subagents/, etc.) and optionally memory/.
    Walks snapshots newest-first (lexical sort on snapshot name); first writer
    wins. Skips dirs that already exist on disk.
    """
    located_sorted = sorted(located, key=lambda t: t[0], reverse=True)
    copied = 0
    for _snap_name, projects in located_sorted:
        for proj_dir in projects.iterdir():
            if not proj_dir.is_dir():
                continue
            if only_project and proj_dir.name != only_project:
                continue
            dest_proj = claude_dir / proj_dir.name
            for sub in proj_dir.iterdir():
                if not sub.is_dir():
                    continue
                if sub.name == "memory" and not include_memory:
                    continue
                dest = dest_proj / sub.name
                if dest.exists():
                    continue
                if dry_run:
                    print(f"would  subdir {dest} (from {sub})")
                    copied += 1
                    continue
                dest_proj.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copytree(sub, dest)
                except OSError as e:
                    print(f"  fail: copytree {sub} -> {dest}: {e}", file=sys.stderr)
                    continue
                for root, _dirs, files in os.walk(dest):
                    for name in files:
                        strip_acl_and_make_writable(Path(root) / name)
                if verbose:
                    print(f"restore subdir {dest}")
                copied += 1
    return copied


# -------- orchestration --------


def choose_snapshots(
    registry: list[SnapshotBackend], opts: Options
) -> tuple[SnapshotBackend, list[DiscoveredSnapshot]]:
    """Resolve which backend + snapshots to use, honoring --backend semantics."""
    by_name = {b.name: b for b in registry}

    if opts.backend == "auto":
        available = [b for b in registry if b.is_available()]
        discovered = {b.name: b.discover() for b in available}
        discovered = resolve_overlaps(discovered, DEFAULT_OWNERSHIP)
        name, snaps = select_auto(discovered)
        print(f"Selected backend: {name}")
        return by_name[name], snaps

    # Explicit backend: run ONLY that backend, skip overlap resolution.
    backend = by_name.get(opts.backend)
    if backend is None:
        die(f"unknown backend '{opts.backend}'. Known: {', '.join(sorted(by_name))}.")
    if not backend.is_available():
        die(f"backend '{opts.backend}' is not available on this system.")
    snaps = backend.discover()
    if not snaps:
        die(f"backend '{opts.backend}' found no snapshots.")
    return backend, snaps


def run_restore(registry: list[SnapshotBackend], opts: Options) -> int:
    if opts.list_backends:
        return list_backends(registry)

    home = Path.home()
    claude_dir = opts.dest if opts.dest else home / ".claude" / "projects"
    if opts.dest:
        print(f"Destination override: {claude_dir}")

    backend, snaps = choose_snapshots(registry, opts)
    print(f"Found {len(snaps)} snapshot(s).")

    # Mount (no-op for auto-mounted backends), locate projects, index.
    located: list[tuple[str, Path]] = []
    all_entries: list[JsonlEntry] = []
    try:
        for snap in snaps:
            root = backend.ensure_mounted(snap)
            projects = locate_projects_dir(root, home)
            if projects is None:
                if opts.verbose:
                    print(f"  {snap.name}: no .claude/projects under {root}")
                continue
            located.append((snap.name, projects))
            all_entries.extend(index_projects(projects, opts.project))

        if not all_entries:
            die("No Claude JSONL files found in any snapshot.")

        best = pick_largest(all_entries)
        print(f"Indexed {len(best)} unique (project, jsonl) pairs across snapshots.")

        restored = 0
        total_bytes = 0
        skipped = 0
        for entry in sorted(best.values(), key=lambda e: (e.project, e.filename)):
            ok, n = restore_file(entry, claude_dir, opts.dry_run, opts.verbose)
            if ok:
                restored += 1
                total_bytes += n
            else:
                skipped += 1

        subdirs = restore_subdirs(
            located, claude_dir, opts.project,
            opts.include_memory, opts.dry_run, opts.verbose,
        )

        print()
        prefix = "DRY RUN: would restore" if opts.dry_run else "Restored"
        print(f"{prefix} {restored} file(s), {total_bytes} byte(s). "
              f"Skipped {skipped} already-current. Subdirs: {subdirs}.")
        return 0
    finally:
        for snap in snaps:
            backend.cleanup(snap)


# -------- main --------


def parse_args(argv: list[str] | None = None) -> Options:
    p = argparse.ArgumentParser(
        description="Restore deleted Claude Code chat transcripts from "
                    "Linux filesystem snapshots.",
    )
    p.add_argument("--backend", default="auto",
                   choices=["zfs", "btrfs", "timeshift", "auto"],
                   help="snapshot backend to use (default: auto)")
    p.add_argument("--list-backends", action="store_true",
                   help="list available backends and discovered snapshot counts")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be restored; copy nothing")
    p.add_argument("--project", metavar="NAME",
                   help="limit to one encoded project dir "
                        "(e.g. -home-you-projects-foo)")
    p.add_argument("--include-memory", action="store_true",
                   help="also restore <project>/memory/ subdirs")
    p.add_argument("--verbose", action="store_true",
                   help="log every file decision, not just the summary")
    p.add_argument("--dest", metavar="DIR", type=Path,
                   help="restore into DIR instead of ~/.claude/projects "
                        "(useful for testing against a copy of your real projects)")

    # Encoded project names start with '-', which argparse would otherwise
    # mistake for another flag. Rewrite "--project FOO" -> "--project=FOO".
    raw = sys.argv[1:] if argv is None else argv
    rewritten: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == "--project" and i + 1 < len(raw) and raw[i + 1].startswith("-"):
            rewritten.append(f"--project={raw[i + 1]}")
            i += 2
        else:
            rewritten.append(raw[i])
            i += 1
    ns = p.parse_args(rewritten)
    return Options(
        backend=ns.backend,
        list_backends=ns.list_backends,
        dry_run=ns.dry_run,
        project=ns.project,
        include_memory=ns.include_memory,
        verbose=ns.verbose,
        dest=ns.dest,
    )


def main() -> int:
    if sys.platform == "darwin":
        die("This is the Linux port. On macOS use the upstream tool: "
            "https://github.com/garrettmoss/restore-claude-history")
    opts = parse_args()
    return run_restore(default_registry(), opts)


if __name__ == "__main__":
    sys.exit(main())
