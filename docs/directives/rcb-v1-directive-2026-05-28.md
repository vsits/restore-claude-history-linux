# RCB v1 Directive — Linux Port of restore-claude-history

**Date:** 2026-05-28
**Version:** v2 (revised after Codex Round 1)
**Author:** AI Team Lead
**Implementer:** vsits-restore-claude-builder (RCB)
**Reviewer:** Codex Review Agent
**Approval gate:** Chris (AI Team Lead drafts → Chris approves directive transitions)

## Revision log

- **v1 (2026-05-28):** initial directive
- **v2 (2026-05-28):** Codex Round 1 addressed —
  - Finding 1 (HIGH): `--backend auto` fails on ambiguity, requires explicit `--backend` when multiple backends find candidates. Timeshift-on-Btrfs assigned to `timeshift` backend; `btrfs` skips Timeshift-managed paths.
  - Finding 2 (MED): three-layer test strategy replaces single Docker-fixture approach.
  - Finding 3 (MED): Phase 1 includes ZFS as the first real backend wired end-to-end.
  - Finding 4 (LOW): v1.1 backend stubs removed from file layout; future backends documented in directive + backend-authoring docs only.
  - Finding 5 (LOW): AGENTS.md gets distinct `--approve` / `--request-changes` / `--comment` examples; bot name reference corrected.

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

**Future backends (documented in directive + `docs/backends.md` only, no code stubs):**
- LVM-thin
- Snapper (openSUSE / Ubuntu)
- borg (requires `borg mount` FUSE)
- restic (requires `restic mount` FUSE)

These are tracked in the directive so the abstraction stays mindful of their constraints; they get a code module only when actually implemented.

## CLI surface

All upstream flags work identically. Two new flags:

| Upstream flag | This fork |
|---|---|
| `--dry-run` | unchanged |
| `--project NAME` | unchanged |
| `--include-memory` | unchanged |
| `--verbose` | unchanged |
| `--dest DIR` | unchanged |
| — | `--backend {zfs,btrfs,timeshift,auto}` (new; default `auto`) |
| — | `--list-backends` (new; prints available backends + discovered-snapshot counts) |

### `--backend auto` semantics (revised after Codex Round 1)

`auto` is the default. The orchestrator runs `discover()` on every implemented backend whose `is_available()` returns True, then chooses according to:

1. **Zero backends find any candidates** → exit with "no snapshots found on any backend" error, suggesting the user check `--list-backends` and verify their snapshot tool is installed.
2. **Exactly one backend finds candidates** → use that backend; log which one was selected at INFO level.
3. **Multiple backends find candidates** → exit with ambiguity error listing each matching backend, its discovered snapshot count, and example snapshot root paths. The error message tells the user to re-run with `--backend <name>` to disambiguate.

The "first match wins" behavior is explicitly rejected because backend scopes overlap on real systems (notably Timeshift on Btrfs; future Snapper-on-Btrfs).

### Backend-overlap resolution rules

When the same physical snapshots are visible to multiple backends, the directive assigns ownership to avoid double-counting in `discover()`:

| Snapshot source | Owner backend | Why |
|---|---|---|
| Timeshift-on-Btrfs (Timeshift configured with Btrfs subvolume snapshots) | `timeshift` | Timeshift's config is the source of truth; `btrfs` backend skips paths under `/timeshift/snapshots/` |
| Snapper-on-Btrfs (when Snapper v1.1 lands) | `snapper` | Snapper's config is source of truth; `btrfs` backend skips paths under `/.snapshots/` |
| Bare Btrfs subvolumes (no Timeshift/Snapper management) | `btrfs` | No higher-layer config exists |
| Bare ZFS snapshots | `zfs` | No overlap with other v1 backends |

Each backend's `discover()` implementation is responsible for skipping paths claimed by a higher-layer backend, even if the higher-layer backend isn't implemented yet (so v1's `btrfs.discover()` knows to skip Timeshift paths). This keeps the ambiguity-error case ("multiple backends find candidates") rare in practice while preserving fail-loud semantics when it does happen.

## File layout

```
.
├── AGENTS.md
├── LICENSE                          # MIT (unchanged from upstream)
├── README.md                        # rewritten for Linux + cross-reference upstream
├── restore_claude_history.py        # orchestrator (kept; backends imported)
├── backends/
│   ├── __init__.py
│   ├── base.py                      # SnapshotBackend ABC + DiscoveredSnapshot
│   ├── _local_dir.py                # LocalDirBackend — test-only fake (used by tempdir-based tests)
│   ├── zfs.py                       # ZFS adapter (Phase 1)
│   ├── btrfs.py                     # Btrfs adapter (Phase 2)
│   └── timeshift.py                 # Timeshift adapter (Phase 3)
├── tests/
│   ├── test_pick_largest.py         # Layer 1: in-process logic tests
│   ├── test_orchestrator.py         # Layer 1: ambiguity + auto-selection tests using LocalDirBackend
│   ├── test_restore_loop.py         # Layer 2: tempdir-based restore tests using LocalDirBackend
│   ├── verify_restore.py            # Layer 2 end-to-end (ported from upstream, uses LocalDirBackend)
│   ├── integration/                 # Layer 3: privileged/manual tests (opt-in; not in default test run)
│   │   ├── README.md                # how to run integration tests on a real host
│   │   ├── test_zfs_real.py         # requires real ZFS pool with snapshots
│   │   ├── test_btrfs_real.py       # requires real Btrfs filesystem
│   │   └── test_timeshift_real.py   # requires Timeshift install + snapshot
├── docs/
│   ├── directives/
│   │   └── rcb-v1-directive-2026-05-28.md   # this file
│   └── backends.md                  # how to add a new backend; lists future-work backends
└── NOTES.md                         # historical context (port-relevant parts kept; macOS-only sections dropped)
```

## SnapshotBackend interface (`backends/base.py`)

Unchanged from v1 directive — the ABC shape held up under Codex review:

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

        Implementations MUST skip paths owned by higher-layer backends per
        the directive's overlap-resolution rules, even when the higher-layer
        backend is not yet implemented.

        For snapshot mechanisms where snapshots are auto-mounted (ZFS),
        return them with needs_mount=False. For mechanisms requiring
        explicit mount (FUSE-based borg/restic, when implemented),
        return with needs_mount=True; ensure_mounted() will be called
        before indexing.
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

## Test approach (revised after Codex Round 1)

Three layers, each with a clear job:

### Layer 1 — in-process unit tests

Pure-Python tests of orchestrator and logic. No filesystem state, no subprocess. Run in <1s.

- `test_pick_largest.py` — `pick_largest()` and `JsonlEntry` behavior
- `test_orchestrator.py` — `--backend auto` selection rules: zero-match error, single-match auto-pick, multi-match ambiguity error; uses `LocalDirBackend` (a fake that yields pre-constructed `DiscoveredSnapshot`s)
- Backend `is_available()` tests (mock subprocess calls)

### Layer 2 — tempdir-based fake-backend tests

Tests of the restore loop end-to-end, using `LocalDirBackend` which treats a tempdir as if it were a snapshot. No real snapshot tooling needed; runs unprivileged.

- `test_restore_loop.py` — build N "snapshots" as tempdirs with varying `.jsonl` sizes; assert correct file selected, mtime preserved, dry-run is no-op
- `verify_restore.py` — ported from upstream's end-to-end test, adapted to `LocalDirBackend`

### Layer 3 — privileged/manual integration tests (opt-in)

Real-backend tests requiring real filesystems and (for ZFS) kernel modules. NOT run in default `pytest` invocation; opt-in via `pytest tests/integration/` plus per-test prerequisites.

- `test_zfs_real.py` — requires a writable test ZFS pool, snapshot creation perm; documented setup in `tests/integration/README.md`
- `test_btrfs_real.py` — requires a Btrfs filesystem with snapshot perm
- `test_timeshift_real.py` — requires Timeshift install + at least one snapshot

**Why this split (vs the v1 directive's Docker-fixture approach):** Codex correctly observed that Docker fixtures can't validate kernel-dependent filesystem semantics (ZFS module loading, Btrfs subvolume mount behavior, `.zfs/snapshot` visibility) in unprivileged containers. Docker tests collapse into command-parsing validation, which we already get from Layer 1 with subprocess mocks. The three-layer split gives us: fast iteration on logic (Layer 1), end-to-end confidence with fake state (Layer 2), and real-world confidence on dedicated hosts (Layer 3) — without paying Docker's container overhead for tests that don't actually exercise the kernel paths.

## Sequencing (revised after Codex Round 1)

1. **Phase 1 — Abstraction + ZFS adapter wired end-to-end.** Land `backends/base.py`, `backends/_local_dir.py`, `backends/zfs.py`, orchestrator with `--backend auto` semantics (including ambiguity error), `--list-backends`. Tests: full Layer 1 + Layer 2; Layer 3 ZFS test exists but is opt-in. Codex review. PR target: `main`.

   *Rationale:* Codex's medium-severity phasing concern is correct — abstractions without a real consumer don't get pressure-tested. ZFS is the cleanest first-real-backend (auto-mount, no overlap with other v1 backends, well-defined `zfs list` output).

2. **Phase 2 — Btrfs adapter.** Adds `backends/btrfs.py` with Timeshift-skip logic. Tests: Layer 1 (Btrfs `discover()` mock subprocess), Layer 2 (n/a — covered by Phase 1's LocalDirBackend tests), Layer 3 opt-in. PR target: `feature/rcb-v1` (parent branch).

3. **Phase 3 — Timeshift adapter.** Adds `backends/timeshift.py`; updates `btrfs.discover()` to actually skip Timeshift paths (so far it's a no-op). PR target: `feature/rcb-v1`.

4. **Phase 4 — README rewrite + cross-reference PR upstream.** README pivots to Linux. AI Team Lead opens cross-reference PR against `garrettmoss/restore-claude-history`'s README for the "See also" wire-up. PR target: `main`.

Each phase = separate PR with directive linked, Codex review, label progression.

## Quality gates

- All phases require Codex review (per AGENTS.md "Codex review triggers").
- All Python code passes `ruff check` (no `ruff format` enforcement yet).
- All new code has type hints (matches upstream's style).
- All new code has at least one test in Layer 1 or Layer 2.
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
| ZFS auto-mount detection varies by distro | Test on Ubuntu 24.04, openSUSE Tumbleweed, FreeBSD; document quirks per distro in `tests/integration/README.md` |
| Btrfs read-only snapshot semantics | Test on snapper-managed Btrfs and bare Btrfs; document `nosuid,nodev,ro` mount requirements |
| Timeshift snapshot config format changes | Pin to v22+ behavior; document detection logic in `backends/timeshift.py` docstring |
| ACL handling on non-ACL filesystems | `setfacl -b` is a no-op on tmpfs / non-ACL filesystems; doesn't error — verified during test |
| Upstream tool sees a bug fix we should pull in | Manual cherry-pick (this is a port, not a sync-fork); document the upstream-divergence point in README |
| `--backend auto` ambiguity error is too aggressive | The overlap-resolution rules (Timeshift-on-Btrfs assigned to `timeshift`, etc.) keep multi-match rare; if it surfaces too often in real use, revisit with telemetry from `--list-backends` output |
