# RCB v1 Directive — Linux Port of restore-claude-history

**Date:** 2026-05-28
**Author:** AI Team Lead
**Implementer:** vsits-restore-claude-builder (RCB)
**Reviewer:** Codex Review Agent
**Approval gate:** Chris (AI Team Lead drafts → Chris approves directive transitions)

## Goal

Port [`garrettmoss/restore-claude-history`](https://github.com/garrettmoss/restore-claude-history) (macOS Time Machine recovery for Claude Code `~/.claude/projects/*.jsonl` transcripts) to Linux. Keep the upstream's recovery logic intact; replace the macOS-specific snapshot-discovery layer with a pluggable backend abstraction supporting Linux snapshot/backup tools.

## Background

Claude Code prunes `~/.claude/projects/<encoded-cwd>/*.jsonl` after `cleanupPeriodDays` (default: 30, undocumented). Multiple user reports describe chats disappearing even with the setting raised, often around app updates. garrettmoss built a macOS recovery tool that walks Time Machine APFS snapshots and restores the largest `.jsonl` for each `(project, filename)` pair to disk. That tool's logic is sound; only the snapshot-discovery layer is macOS-specific.

Origin discussion: [anthropics/claude-code#62272](https://github.com/anthropics/claude-code/issues/62272) — garrettmoss explicitly invited a Linux fork and committed to bidirectional "See also" cross-reference.

## v1 scope

**Three backends, auto-discovery, no user-supplied config paths:**

1. **ZFS** — `zfs list -t snapshot`; snapshots auto-mounted at `<dataset>/.zfs/snapshot/<name>/`. Common on NAS / Pi-hole / homelab.
2. **Btrfs** — `btrfs subvolume list -s <mountpoint>`; snapshots are read-only subvolumes. Default on openSUSE, optional on Ubuntu/Debian.
3. **Timeshift** — `/etc/timeshift/timeshift.json` config + dir scan; snapshots at `/timeshift/snapshots/<ts>/`. Ubuntu's default backup tool.

**Stubbed in v1, implemented in v1.1 (each as a plug-in module):**
- LVM-thin
- Snapper (openSUSE / Ubuntu)
- borg (requires `borg mount` FUSE)
- restic (requires `restic mount` FUSE)

**CLI compatibility goal:** all upstream flags work identically. Add one new flag: `--backend <name>` to force a specific backend; default behavior is "try auto-discovery, prefer first that finds snapshots."

| Upstream flag | This fork |
|---|---|
| `--dry-run` | unchanged |
| `--project NAME` | unchanged |
| `--include-memory` | unchanged |
| `--verbose` | unchanged |
| `--dest DIR` | unchanged |
| — | `--backend {zfs,btrfs,timeshift,auto}` (new; default `auto`) |
| — | `--list-backends` (new; prints available backends + status) |

## File layout

```
.
├── AGENTS.md
├── LICENSE                          # MIT (unchanged from upstream)
├── README.md                        # rewritten for Linux + cross-reference upstream
├── restore_claude_history.py        # orchestrator (kept; backends imported)
├── backends/
│   ├── __init__.py
│   ├── base.py                      # SnapshotBackend ABC
│   ├── zfs.py                       # ZFS adapter
│   ├── btrfs.py                     # Btrfs adapter
│   ├── timeshift.py                 # Timeshift adapter
│   ├── lvm_thin.py                  # v1.1 stub (NotImplementedError)
│   ├── snapper.py                   # v1.1 stub
│   ├── borg.py                      # v1.1 stub
│   └── restic.py                    # v1.1 stub
├── tests/
│   ├── verify_restore.py            # port of upstream test (fixtures use fake backend)
│   ├── test_pick_largest.py         # pure-logic unit tests (porting-validation)
│   ├── test_backend_zfs.py          # backend-specific tests (Docker fixture)
│   ├── test_backend_btrfs.py
│   └── test_backend_timeshift.py
├── docs/
│   ├── directives/
│   │   └── rcb-v1-directive-2026-05-28.md   # this file
│   └── backends.md                  # how to add a new backend
└── NOTES.md                         # historical context (port-relevant parts kept; macOS-only sections dropped)
```

## SnapshotBackend interface (`backends/base.py`)

```python
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

        For snapshot mechanisms where snapshots are auto-mounted (ZFS),
        return them with needs_mount=False. For mechanisms requiring
        explicit mount (FUSE-based borg/restic), return with
        needs_mount=True and leave data_root as the intended mountpoint;
        ensure_mounted() will be called before indexing.
        """
        ...

    def ensure_mounted(self, snap: DiscoveredSnapshot) -> Path:
        """Mount the snapshot if needed; return the usable data_root path.

        Default impl (for auto-mounted backends) is a no-op returning
        snap.data_root. Backends needing FUSE mount override this.
        """
        return snap.data_root

    def cleanup(self, snap: DiscoveredSnapshot) -> None:
        """Unmount or release the snapshot if we mounted it.

        Default impl is a no-op. Backends doing FUSE mount override.
        """
        pass
```

## What stays from upstream

These functions port unchanged (modulo `Path` arg typing for snapshot paths):

- `JsonlEntry` dataclass
- `index_snapshot()` — given a mounted snapshot's data root + user, walks `~/.claude/projects/*/*.jsonl` and yields entries
- `pick_largest()` — pure-logic dedup by `(project, filename)`, takes largest size
- The restore loop (size compare, dry-run logging, `shutil.copy2` with mtime preservation, write-bit restoration)
- CLI flag definitions (extended with `--backend` and `--list-backends`)

## What gets replaced

| Upstream function | Removed | Replaced by |
|---|---|---|
| `find_tm_device()` | yes | each backend's `discover()` |
| `list_snapshots()` | yes | each backend's `discover()` |
| `existing_mounts()` | yes | (n/a — Linux snapshot mounting model is per-backend) |
| `mount_snapshot()` | yes | each backend's `ensure_mounted()` |
| `unmount_if_ours()` | yes | each backend's `cleanup()` |
| `find_data_root()` | yes | each `DiscoveredSnapshot.data_root` |
| `strip_acl_and_make_writable()` | adapted | `setfacl -b` on ext4/xfs with ACL; no-op on filesystems without |

## Test approach

**Docker-based fake-backend fixtures.** Each backend test:

1. Spins up a small Docker container with the backend's tooling installed (or simulated).
2. Creates a fake `/Users/testuser/.claude/projects/<encoded>/foo.jsonl` tree at multiple "snapshot" timestamps with increasing file sizes.
3. Runs `restore_claude_history.py --backend <name> --dest /tmp/restored`.
4. Asserts: correct file count, largest version chosen, mtime preserved.

**Why Docker, not in-process mocks:** the backends shell out to real tools (`zfs`, `btrfs`, `timeshift`, etc.). Mocking subprocess calls in-process is fragile and doesn't validate command syntax against the actual tool. Containerized fixtures are slower but catch the real failure modes.

**Pure-logic tests** (`test_pick_largest.py`, etc.) stay in-process — they exercise the upstream-preserved logic and don't need filesystem fixtures.

## Sequencing

1. **Phase 1 — Backend abstraction.** Land `backends/base.py` + ported orchestrator. v1 backends stubbed (raise NotImplementedError); upstream's logic preserved and tested via `test_pick_largest.py`. Existing upstream tests adapted to call through a `LocalDirBackend` (test-only) that just walks a static directory. CLI accepts `--backend` and `--list-backends`. No real backends wired yet. PR target: `main`.

2. **Phase 2 — ZFS adapter.** First real backend. PR target: `feature/rcb-v1` (parent branch from `main`).

3. **Phase 3 — Btrfs adapter.** PR target: `feature/rcb-v1`.

4. **Phase 4 — Timeshift adapter.** PR target: `feature/rcb-v1`.

5. **Phase 5 — README rewrite + cross-reference PR upstream.** README pivots to Linux. AI Team Lead opens cross-reference PR against `garrettmoss/restore-claude-history`'s README. PR target: `main`.

Each phase = separate PR with directive linked, Codex review, label progression.

## Quality gates

- All phases require Codex review (per AGENTS.md "Codex review triggers").
- All Python code passes `ruff check` (no `ruff format` enforcement yet).
- All new code has type hints (matches upstream's style).
- All new code has at least one test exercising the happy path.
- README and AGENTS.md stay accurate; PRs touching code update docs in the same PR.

## Out of scope for v1

- Snapshot creation (use user's existing backup tool — `zfs snapshot`, `btrfs subvolume snapshot`, `timeshift --create`, etc.).
- macOS support (upstream covers this; we explicitly do not detect-OS-and-branch).
- Windows support (different filesystem semantics; future fork if there's demand).
- Web UI / GUI.
- Anything beyond `.jsonl` recovery — no settings file, no `.claude/memory/` symlink targets, no IDE state.

## Risks

| Risk | Mitigation |
|---|---|
| ZFS auto-mount detection varies by distro | Test on Ubuntu 24.04, openSUSE Tumbleweed, FreeBSD; document quirks per distro |
| Btrfs read-only snapshot semantics | Test on snapper-managed Btrfs and bare Btrfs; document `nosuid,nodev,ro` mount requirements |
| Timeshift snapshot config format changes | Pin to v22+ behavior; document detection logic in `backends/timeshift.py` docstring |
| ACL handling on non-ACL filesystems | `setfacl -b` is a no-op on tmpfs / non-ACL filesystems; doesn't error — verified during test |
| Upstream tool sees a bug fix we should pull in | Manual cherry-pick (this is a port, not a sync-fork); document the upstream-divergence point in README |
