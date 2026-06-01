# Layer 3 — privileged / manual integration tests

These tests exercise **real** snapshot backends against **real** filesystems.
They are **opt-in** and are NOT collected by the default `pytest` run (the root
`pyproject.toml` ignores this directory). Each test also self-skips unless its
prerequisites are met, so an accidental `pytest tests/integration/` on a box
without the right setup is harmless.

Run them explicitly:

```bash
pytest tests/integration/ -v
```

## Why a separate layer

Docker fixtures cannot validate kernel-dependent filesystem semantics (ZFS
module loading, Btrfs subvolume mounts, `.zfs/snapshot` visibility) in
unprivileged containers — they collapse into command-parsing checks we already
get from the Layer 1 subprocess mocks. Real confidence needs a real host.

## ZFS — `test_zfs_real.py`

Requires a writable test pool and permission to create/destroy snapshots.

```bash
# One-time: create a file-backed test pool (root).
truncate -s 256M /tmp/rcb-zfs.img
sudo zpool create rcbtest /tmp/rcb-zfs.img
sudo zfs create rcbtest/home
sudo chown -R "$USER" /rcbtest/home

# Point the test at it and run.
export RCB_ZFS_TEST_DATASET=rcbtest/home
pytest tests/integration/test_zfs_real.py -v

# Teardown.
sudo zpool destroy rcbtest && rm -f /tmp/rcb-zfs.img
```

The test writes a `.claude/projects/<proj>/*.jsonl` tree into the dataset,
takes a snapshot with `zfs snapshot`, deletes the live file, then asserts
`ZfsBackend.discover()` finds the snapshot and the restore loop recovers the
file from `<mountpoint>/.zfs/snapshot/<name>/`.

## Btrfs — `test_btrfs_real.py`

Requires a writable Btrfs mountpoint and permission to create/delete
subvolumes + snapshots (normally root).

```bash
# One-time: create a file-backed test Btrfs filesystem (root).
truncate -s 512M /tmp/rcb-btrfs.img
mkfs.btrfs /tmp/rcb-btrfs.img
sudo mkdir -p /mnt/rcbbtrfs
sudo mount -o loop /tmp/rcb-btrfs.img /mnt/rcbbtrfs
sudo chown "$USER" /mnt/rcbbtrfs

export RCB_BTRFS_TEST_MOUNT=/mnt/rcbbtrfs
sudo -E pytest tests/integration/test_btrfs_real.py -v   # subvolume ops need root

# Teardown.
sudo umount /mnt/rcbbtrfs && rm -f /tmp/rcb-btrfs.img
```

The test creates a subvolume with a marker file, takes a read-only snapshot,
then asserts `BtrfsBackend.discover()` surfaces a snapshot whose `data_root`
contains the marker. `discover()` resolves each `btrfs subvolume list -s` path
(relative to the fs root subvolume) against the live mounts of that filesystem;
snapshots not reachable from any current mount are skipped. Document per-distro
layout quirks (openSUSE `@/.snapshots`, mount options like `nosuid,nodev,ro`)
here as they are discovered.

## Timeshift — `test_timeshift_real.py`

Read-only: points `TimeshiftBackend` at a real (or realistic) snapshots store
and asserts every reported `data_root` is a readable directory. It does not
invoke Timeshift or mount the backup device.

```bash
# Against a real Timeshift host (RSYNC mode, the Ubuntu default):
export RCB_TIMESHIFT_TEST_BASE=/timeshift/snapshots
# config defaults to /etc/timeshift/timeshift.json; override if needed:
# export RCB_TIMESHIFT_TEST_CONFIG=/etc/timeshift/timeshift.json
pytest tests/integration/test_timeshift_real.py -v

# Or against a synthetic tree (no Timeshift install needed):
mkdir -p /tmp/rcb-ts/2026-05-28_00-00-01/localhost/home/$USER/.claude/projects
printf '{"btrfs_mode":"false"}' | sudo tee /etc/timeshift/timeshift.json >/dev/null
export RCB_TIMESHIFT_TEST_BASE=/tmp/rcb-ts
pytest tests/integration/test_timeshift_real.py -v
```

For BTRFS mode, snapshots live under `timeshift-btrfs/snapshots/<ts>/` with
`@`/`@home` subvolumes (persistently at `/timeshift-btrfs/snapshots` or, while
Timeshift has the device mounted, `/run/timeshift/<pid>/backup/...`). On a
Timeshift-on-Btrfs host the `auto` orchestrator prunes the Btrfs backend's
duplicate of a snapshot Timeshift also reports (Timeshift owns).
