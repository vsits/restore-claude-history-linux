# RCB v1 Directive — Linux Port of restore-claude-history

**Date:** 2026-05-28
**Version:** v6 (revised after Codex Rounds 1–5)
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
- **v3 (2026-05-28):** Codex Round 2 addressed — the Round 1 fix to Finding 1 introduced an internal contradiction (Phase 2 supposedly ships Btrfs with Timeshift-skip logic; Phase 3 supposedly wires up the skip — incompatible). Moved overlap resolution from per-backend `discover()` into the orchestrator, where the overlap-resolution table is a single source of truth and skip rules only fire when the owning backend is actually registered. ABC docstring, overlap section, and Phase 2/3 descriptions now consistent.
- **v4 (2026-05-28):** Codex Round 3 addressed — v3's "prune when owner is available" rule reintroduced a false-negative path (if owner `is_available()=True` but `discover()` returned zero due to config drift or parser failure, valid peer snapshots would still be stripped). Tightened the rule: pruning happens only when the owner backend positively returns at least one snapshot matching the claimed-path-pattern in this run. Also specified that explicit `--backend <name>` mode bypasses overlap resolution entirely.
- **v5 (2026-05-28):** Codex Round 4 addressed — v4's "namespace-wide pruning on positive hit" still allowed false negatives on partial owner discovery (owner finds A and B under `/timeshift/snapshots/` but misses C; orchestrator prunes all three from peers). Tightened to per-snapshot exact-path-match: only the specific snapshot paths the owner actually returned in this run get pruned from peers. The "claimed-path-pattern" in the overlap table is now an informational heuristic for `--list-backends`, not a pruning rule. Also fixed Version header and design-choices recap to reflect v5 as the rule's landing point.
- **v6 (2026-05-28):** Codex Round 5 addressed — v5 left stale wording in the overlap table's "Why" column ("backend skips paths under X") that contradicted the orchestrator-only pruning model and risked sending the implementer back to the rejected v1 design. Rewrote the table entries to describe disambiguation rationale (which backend wins on duplicate paths in `auto` mode), explicitly noting that no backend skips paths in `discover()`. Also narrowed the `realpath` canonicalization claim: handles symlinks + lexical normalization but NOT bind-mount aliasing; bind-mount duplicates surface as duplicates (correctly triggering ambiguity error or duplicate output, user disambiguates with `--backend`); `(st_dev, st_ino)` identity is future work if needed.

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
| Timeshift-on-Btrfs (Timeshift configured with Btrfs subvolume snapshots) | `timeshift` | If both `timeshift.discover()` and `btrfs.discover()` return the same snapshot path in an `auto` run, the orchestrator keeps the Timeshift entry and discards the Btrfs duplicate. Rationale: Timeshift's config carries snapshot intent that bare Btrfs introspection cannot recover (retention policy, snapshot purpose). Neither backend skips paths in its own `discover()` — pruning happens only at the orchestrator. |
| Snapper-on-Btrfs (when Snapper v1.1 lands) | `snapper` | Same pattern: orchestrator keeps the Snapper entry and discards Btrfs duplicates of the same path. Snapper's config carries snapshot intent (pre/post, single, etc.). |
| Bare Btrfs subvolumes (no Timeshift/Snapper management) | `btrfs` | No higher-layer backend can claim these; Btrfs entries stand. |
| Bare ZFS snapshots | `zfs` | No path-level overlap with other v1 backends. |

**The orchestrator applies the overlap-resolution table after discovery completes, gated on per-snapshot positive claim.** Each backend's `discover()` reports what its tooling reports — no backend needs to know about its peers. After all backends have returned their `DiscoveredSnapshot` lists, the orchestrator deduplicates as follows:

> For each `DiscoveredSnapshot` returned by an owner backend (per the table above) at path `data_root=P`, the orchestrator removes from peer backends' lists any `DiscoveredSnapshot` whose `data_root` matches `P` after canonicalization. Mere `is_available()=True` on the owner is insufficient; namespace-wide claim is insufficient; only the specific snapshot paths the owner actually returned in this run get pruned from peers.
>
> **Canonicalization in v1:** `os.path.realpath()` (resolves symlinks + lexical normalization). This catches the common case of one backend reporting `/timeshift/snapshots/2026-05-28_00-00-01/backup/@home` and another reporting the same path via a symlinked entry point. **Known limitation:** `realpath` does NOT canonicalize bind-mount aliases — the same underlying snapshot data reachable via two bind-mount entry points would remain two distinct canonical paths and survive deduplication as duplicates. v1 accepts this (duplicates appear in `auto` output, ambiguity error fires correctly, user can disambiguate with `--backend`). If bind-mount aliasing surfaces as a real problem in v1.1+, the fix is `(st_dev, st_ino)` identity on the snapshot root rather than path equality; this is tracked as future work, not v1 scope.

This closes three failure modes:
1. **Owner zero-discovery** (Round 3): Owner is available but returned nothing — no pruning, peers' results stand. ✓
2. **Owner partial discovery** (Round 4): Owner returned snapshots A and B but missed C under the same `/timeshift/snapshots/` namespace — only A and B are pruned from peers; C is preserved. ✓
3. **Owner full discovery**: Owner returned A, B, C — all three pruned from peers; clean deduplication. ✓

**What "claimed-path-pattern" in the table above actually means:** the pattern (`/timeshift/snapshots/`, `/.snapshots/`, etc.) describes WHERE the owner backend is expected to find its snapshots, used as a sanity-check heuristic for `--list-backends` output. It does NOT define what the orchestrator prunes. Pruning is always per-snapshot, by exact (canonicalized) `data_root` match.

In Phase 2 (Btrfs only, Timeshift backend not yet registered), no pruning happens — Btrfs's results stand as-is, possibly with what Phase 3 would later attribute to Timeshift. The user gets candidates; correctness is preserved.

### Overlap resolution in explicit `--backend <name>` mode

When the user passes `--backend zfs|btrfs|timeshift` (not `auto`), the orchestrator runs ONLY that backend's `discover()` and skips cross-backend overlap resolution entirely. Rationale: the user is being explicit about which inventory they want; if they pass `--backend btrfs` on a Timeshift-on-Btrfs host, they should see all subvolumes Btrfs can enumerate including ones Timeshift would have claimed in `auto` mode. The overlap-resolution rules exist to disambiguate `auto`-mode ownership, not to filter explicit requests.

### Design choices (deliberate, per Codex Rounds 2 + 3)

- **Backends stay independent** — no `btrfs.py` import of Timeshift internals, no "skip this path because some-future-backend might claim it" logic.
- **Pruning gated on positive claim.** "Owner is available" is necessary but not sufficient; the owner must have actually returned matching snapshots. Closes the false-negative path where an installed-but-broken owner backend would silently steal results from a working peer.
- **Explicit-backend mode bypasses overlap pass.** Predictable, user-controlled, and matches the principle that explicit requests are honored verbatim.
- **The overlap table is the single source of truth** — changes happen in one place (orchestrator) when a new backend is added, not scattered across every existing backend's `discover()`.

The earlier v1 draft of this section assigned skip-responsibility to each backend's `discover()` (required `btrfs.discover()` to skip Timeshift paths "even when the higher-layer backend isn't implemented yet"). That was internally inconsistent with the phase plan. The v2 draft moved skip-responsibility to the orchestrator but used "owner is available" as the gate, which had a false-negative path when the owner returned zero. v3 added a "positive claim" gate but defined claim at namespace granularity, which had a partial-discovery false-negative path. v5 lands per-snapshot exact-path-match pruning, which closes all three failure modes.

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

        Implementations report what their tooling reports — no
        cross-backend overlap handling here. The orchestrator deduplicates
        across backends after discovery using the directive's
        overlap-resolution table (see "Backend-overlap resolution rules").

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

2. **Phase 2 — Btrfs adapter.** Adds `backends/btrfs.py` reporting raw `btrfs subvolume list -s` output. No overlap handling in this PR — orchestrator-side deduplication doesn't fire yet because Timeshift backend isn't registered. Tests: Layer 1 (Btrfs `discover()` mock subprocess), Layer 3 opt-in. PR target: `feature/rcb-v1` (parent branch).

3. **Phase 3 — Timeshift adapter + orchestrator deduplication.** Adds `backends/timeshift.py` AND wires the orchestrator's overlap-resolution pass. This is the PR where Timeshift-on-Btrfs hosts get correct results (Timeshift owns the snapshots; Btrfs results filtered post-discovery). Tests: Layer 1 (Timeshift `discover()` mock + orchestrator overlap-resolution unit tests covering Timeshift-on-Btrfs scenario), Layer 3 opt-in. PR target: `feature/rcb-v1`.

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
