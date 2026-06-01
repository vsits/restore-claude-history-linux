# Snapshot backends

Each backend adapts one Linux snapshot/backup mechanism to the
`SnapshotBackend` interface in [`backends/base.py`](../backends/base.py). The
orchestrator (`restore_claude_history.py`) talks to backends only through that
interface and handles cross-backend deduplication itself — backends never need
to know about each other.

## v1 backends

| Backend | Module | Tooling | Mount model |
|---|---|---|---|
| ZFS | `backends/zfs.py` | `zfs list -t snapshot` | auto-mounted at `<mountpoint>/.zfs/snapshot/<name>/` (`needs_mount=False`) |
| Btrfs | `backends/btrfs.py` | `btrfs subvolume list -s` | read-only subvolumes, resolved against live mounts (`needs_mount=False`) |
| Timeshift | `backends/timeshift.py` | `/etc/timeshift/timeshift.json` + dir scan | RSYNC `/timeshift/snapshots/<ts>/localhost/`; BTRFS `<ts>/@home`·`@` (`needs_mount=False`) |

Both ZFS and Btrfs read the live mount table via `backends/_mountinfo.py`
(`mounts_of_fstype`), which handles util-linux octal-escape decoding once for
all backends.

The Btrfs adapter reports paths from `btrfs subvolume list -s` (relative to the
filesystem root subvolume) and resolves each against the live mounts of that
filesystem; a snapshot reachable from no current mount is skipped. It does no
cross-backend overlap handling.

The Timeshift adapter scans `/timeshift/snapshots`, `/timeshift-btrfs/snapshots`,
and `/run/timeshift/*/backup/timeshift-btrfs/snapshots`, reporting each
snapshot's filesystem root (`localhost` for RSYNC, `@home`/`@` for BTRFS).

**Overlap pass is now active.** With Timeshift registered, the orchestrator's
`auto`-mode dedup fires the `timeshift > btrfs` ownership rule: on a
Timeshift-on-Btrfs host, a snapshot both backends report at the same
canonical path is kept for Timeshift and pruned from Btrfs (per-snapshot,
exact `realpath` match, gated on Timeshift positively returning that path).
Explicit `--backend btrfs` still bypasses dedup and shows the raw inventory.

## Future-work backends (documented, not yet implemented)

Tracked here so the abstraction stays mindful of their constraints; each gets a
module only when actually built:

- **LVM-thin** — thin-snapshot volumes; needs activate + mount.
- **Snapper** (openSUSE / Ubuntu) — Btrfs-backed; carries snapshot intent
  (pre/post/single). Owns paths over bare `btrfs` in overlap resolution.
- **borg** — requires `borg mount` (FUSE); `needs_mount=True`.
- **restic** — requires `restic mount` (FUSE); `needs_mount=True`.

## Adding a backend

1. Create `backends/<name>.py` with a `SnapshotBackend` subclass:
   - `name` — matches the `--backend` flag value.
   - `is_available()` — is the tooling installed and usable? Do **not** check
     whether snapshots exist here.
   - `discover()` — return every `DiscoveredSnapshot` the tooling reports. No
     cross-backend filtering. Set `needs_mount=True` only if the snapshot must
     be explicitly mounted before its files are readable.
   - For FUSE-style backends, also override `ensure_mounted()` /`cleanup()`.
2. Register it in `default_registry()` in [`backends/__init__.py`](../backends/__init__.py).
3. Add the `--backend <name>` choice in `parse_args()`.
4. If the backend can see paths another backend also sees (e.g. Snapper sits on
   Btrfs), add an `(owner, peer)` entry to `DEFAULT_OWNERSHIP` in
   `restore_claude_history.py`. The orchestrator prunes a peer's snapshot only
   when the owner **positively returns that exact (canonicalized) path** this
   run — see the directive's overlap-resolution rules.
5. Tests: Layer 1 `is_available()` + `discover()` with mocked subprocess; a
   Layer 3 opt-in real-host test under `tests/integration/`.

## Overlap resolution (auto mode)

`--backend auto` runs every available backend, deduplicates, then requires
exactly one backend to have candidates (else it errors with an ambiguity
message). Deduplication is **per-snapshot, exact-path-match** after
`os.path.realpath()` canonicalization, gated on the owner positively returning
the path. Explicit `--backend <name>` skips deduplication entirely and returns
that backend's full inventory.

**Known v1 limitation:** `realpath` does not canonicalize bind-mount aliases;
the same snapshot reachable via two bind mounts survives as duplicates (which
correctly surfaces as an ambiguity the user resolves with `--backend`). The
future fix is `(st_dev, st_ino)` identity rather than path equality.
