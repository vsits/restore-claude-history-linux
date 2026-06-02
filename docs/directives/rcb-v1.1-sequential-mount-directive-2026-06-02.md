# RCB v1.1 directive — sequential per-snapshot restore loop with backend-supplied creation timestamps

Status: directive-stage
Author: vsits-restore-claude-builder[bot]
Tracking issue: [#22](https://github.com/vsits/restore-claude-history-linux/issues/22)
Upstream reference: [`0dd756b`](https://github.com/garrettmoss/restore-claude-history/commit/0dd756b) (`refactor: sequential mount/index/restore/unmount per snapshot (v1.0.1)`)

## Goal

Replace RCB's current `index_projects` → `pick_largest` → `run_restore` shape with a newest-first per-snapshot loop driven by a `seen` set, mirroring upstream v1.0.1's structure while preserving RCB's correctness guarantees. Add a backend-supplied `created_at` field to `DiscoveredSnapshot` so cross-backend ordering is well-defined.

## Background

Upstream's v1.0.1 commit restructured the orchestrator around per-snapshot iteration: walk newest-first, restore on first sighting of each `(project, filename)` pair (JSONLs are append-only so the newest copy is the largest), unmount when done. The motivating problem was macOS Spotlight worker pile-up from holding 50 snapshots open at once.

Our Linux backends don't have that forcing function — ZFS exposes `.zfs/snapshot/...` as a static tree, Btrfs snapshots are always-present read-only subvols, Timeshift snapshots are pre-mounted. But the refactor is still attractive:

- **Lower peak memory.** No full `(project, filename) → entry` index; one snapshot's projects in flight at a time.
- **Cleaner loop shape.** One snapshot per iteration; restore as you go.
- **First-writer-wins matches the dogfood mental model.** "The newest snapshot's copy of this transcript wins" is more intuitive than "the largest copy across the universe of snapshots wins."
- **Upstream-alignment.** Future cherry-picks against the same loop shape become mechanical instead of structural.

## Non-Functional Requirements

- **Size/complexity budget:** ~200 LOC net delta in `restore_claude_history.py`, plus ~10 lines per backend for `created_at` discovery (3 backends). Total expected: ~230 LOC delta. Review flags an implementation materially larger (≈2×) than this.
- **Threat model:** No new external inputs. The backend timestamp discovery calls are read-only inspections of pre-trusted snapshot metadata (`zfs get creation`, `btrfs subvolume show -m`, Timeshift snapshot dir name / `info.json`). No new subprocess targets, no parsing of user-supplied data.
- **Maintainability constraints:** `created_at` is a single new field on the existing `DiscoveredSnapshot` dataclass — no new abstractions. The orchestrator gains one new helper (newest-first iteration with `seen`-set dedupe), replacing two existing helpers (`pick_largest`, the post-loop restore in `run_restore`). Net abstraction count decreases by one.
- **Performance/reliability:** Restore-loop peak memory drops from O(snapshots × projects × files) to O(snapshots) for the snapshot list plus O(seen-pairs) for the dedupe set. Wall-clock should be no worse than current (we do the same I/O work in a different order). No reliability regression — first-writer-wins on mtime-ordered snapshots gives the same observable result as pick-largest, because JSONLs are append-only.
- **Load-bearing?** **Yes.** This adds a required field (`created_at`) to the `DiscoveredSnapshot` dataclass, which is a wire-contract surface for any future backend implementation. Also touches the hot path that the v1.0.0 dogfood validated end-to-end. Adds Chris as a required approver before `ready-for-merge`.

## Scope

### 1. `DiscoveredSnapshot.created_at: datetime`

Add a required field to the dataclass:

```python
@dataclass
class DiscoveredSnapshot:
    name: str
    data_root: Path
    needs_mount: bool
    backend_state: dict
    created_at: datetime  # NEW — UTC-aware datetime of snapshot creation
```

**UTC-aware** to avoid timezone confusion across backends (`zfs get creation` returns the host's local time by default; we convert at discovery). All three v1 backends populate from existing per-backend mechanisms:

- **ZFS:** `zfs get -Hp -o value creation <snapshot>` returns a Unix timestamp. Parse to `datetime.fromtimestamp(ts, tz=timezone.utc)`.
- **Btrfs:** `btrfs subvolume show -m <path>` includes a `Creation time: YYYY-MM-DD HH:MM:SS +ZZZZ` line. Parse via `datetime.strptime` + `.astimezone(timezone.utc)`.
- **Timeshift:** the snapshot directory name is itself a timestamp (`YYYY-MM-DD_HH-MM-SS`) representing creation in UTC. Parse directly with `datetime.strptime(...).replace(tzinfo=timezone.utc)`. The `info.json` file's `date_created` is a fallback if the dir name parse fails (older Timeshift versions used different formats).

Backend discovery failure modes:

- If `created_at` cannot be determined for a snapshot, the backend SHOULD log a warning and skip that snapshot. The orchestrator MUST NOT accept a `DiscoveredSnapshot` with a missing or sentinel `created_at` — the dataclass field is required, so the type system enforces this.

### 2. Orchestrator: sequential restore loop

Replace the current `run_restore` body:

```python
# current shape (pseudocode):
located = [(snap, locate_projects_dir(...)) for snap in snapshots]
entries = []
for snap, projects in located:
    entries.extend(index_projects(projects, opts))
best = pick_largest(entries)
for entry in best.values():
    restore_file(entry, claude_dir, opts)
restore_subdirs(snapshots, ...)
```

with a single newest-first loop:

```python
# new shape (pseudocode):
snapshots_sorted = sorted(snapshots, key=lambda s: s.created_at, reverse=True)
seen: set[tuple[str, str]] = set()
for snap in snapshots_sorted:
    projects = locate_projects_dir(snap.data_root, home)
    if projects is None:
        continue
    for entry in index_projects(projects, opts):
        key = (entry.project, entry.filename)
        if key in seen:
            continue
        seen.add(key)
        restore_file(entry, claude_dir, opts)
    restore_subdirs_from_snapshot(snap, ..., seen_subdirs)
```

`restore_subdirs` becomes `restore_subdirs_from_snapshot` — called per-iteration with its own `seen_subdirs` set carried across iterations.

### 3. `--backend auto` interaction

Cross-backend overlap resolution (`resolve_overlaps`) currently runs on the full snapshot list before any restore work. That stays unchanged — it dedupes the snapshot list itself, and the sequential loop consumes the deduped output. Ordering by `created_at` happens after dedup.

### 4. e2e validation

The refactor touches the hot path. Acceptance requires:

- All 87 existing unit + tempdir-integration tests pass.
- QEMU e2e harness passes on all three backends (ZFS, Btrfs, Timeshift) — same byte-equality + mtime + ACL + Resume-visibility checks as v1.0.0.
- A re-dogfood pass on Btrfs: planted session → snapshot → delete → restore → `/resume` shows and resumes. Same procedure as the v1.0.0 dogfood (issue #15).

## Alternatives considered

The tracking issue named three options. Rationale for picking (a):

- **(a) `created_at` on `DiscoveredSnapshot` — chosen.** Cross-backend ordering is well-defined by an explicit timestamp. Backends are the right place to source it (each has a native creation-time API). The dataclass is already the abstraction layer for snapshot metadata.
- **(b) Documented "newest-first" backend contract — rejected.** Cross-backend overlap resolution merges snapshots across backends. Within-backend ordering doesn't solve the cross-backend ordering problem; it only hides it. A `created_at` field is the smallest abstraction that actually solves it.
- **(c) Size-based dedupe per-loop — rejected.** Preserves correctness, gets the memory + loop-shape wins, but loses (1) the first-writer-wins mental-model simplicity and (2) the upstream-alignment that motivates the port. Half a port at best; the implementation cost is similar to (a) but the long-term cost (ongoing structural drift from upstream) is much higher.

## Out of scope

- No backend ABC changes beyond the new field on `DiscoveredSnapshot`. The `discover()` signature is unchanged.
- No CLI surface changes. `--list-backends` output may show the new timestamp in verbose mode but is not required.
- No subdir-restore semantic changes beyond the per-snapshot call shape.
- The `restore_subdirs_from_snapshot` rename is not back-compat-shimmed — internal name change, no external callers.
- Backend-creation timestamps are not normalized across backends. Each backend reports what its tooling reports, converted to UTC. We do not attempt to second-guess upstream timestamps (e.g. if Timeshift's dir-name timestamp drifts from `info.json`, the dir name wins because that's what existed historically).

## Acceptance criteria

- [ ] `DiscoveredSnapshot.created_at` lands as a required UTC `datetime` field; all three v1 backends populate it.
- [ ] Orchestrator's `run_restore` runs a sequential newest-first loop with `seen`-set dedupe. `pick_largest` is removed (or kept as a thin shim used by no production caller, deleted in a follow-up — anti-bloat lens applies here, deletion preferred).
- [ ] `restore_subdirs` is renamed to `restore_subdirs_from_snapshot` and called per-iteration.
- [ ] All 87 existing tests pass.
- [ ] QEMU e2e harness passes on ZFS, Btrfs, Timeshift.
- [ ] Btrfs dogfood passes the same shape as v1.0.0.
- [ ] README updated if a user-visible behavior shift surfaces during implementation (none expected).
- [ ] Tag cut as `linux/v1.1.0` after merge.

## Implementation plan (separate PR)

This directive PR establishes scope, NFRs, and the design choice. The implementation lands in a separate PR titled along the lines of `feat: sequential per-snapshot restore loop with created_at (closes #22)`. Implementation order:

1. Add `created_at` to `DiscoveredSnapshot`. Update each backend's `discover()` to populate it. Tests: per-backend unit tests that assert UTC tzinfo + reasonable bounds (no future timestamps, no negatives).
2. Rewrite `run_restore` to the sequential shape. Delete `pick_largest`. Rename `restore_subdirs`.
3. Update the existing tempdir-integration tests to assert first-writer-wins ordering.
4. Run the full QEMU e2e harness on all three backends.
5. Re-dogfood pass on Btrfs.

## Pointers

- Upstream commit: [garrettmoss/restore-claude-history@0dd756b](https://github.com/garrettmoss/restore-claude-history/commit/0dd756b)
- Tracking issue: [#22](https://github.com/vsits/restore-claude-history-linux/issues/22)
- Spike memory: [`project_linux-cc-cleanup-mechanism`](../../../../.claude/projects/-home-manager-git-repos-restore-claude-history-linux/memory/project_linux-cc-cleanup-mechanism.md) — mtime semantics; relevant context for the first-writer-wins correctness argument.
- v1.0.0 dogfood (procedure to mirror): [#15](https://github.com/vsits/restore-claude-history-linux/issues/15).
