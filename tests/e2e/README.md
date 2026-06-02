# Layer 3 end-to-end harness (QEMU + KVM)

Runs each v1 backend's opt-in Layer 3 test inside a real Linux kernel via QEMU,
so we exercise ZFS / Btrfs / Timeshift the way users do — not as mocked
subprocess calls. See [`docs/plans/qemu-e2e-plan.md`](../../docs/plans/qemu-e2e-plan.md)
for the design rationale.

This harness is the release gate, not a per-PR gate. Run before tagging a
release.

## Prereqs

```bash
sudo apt install qemu-system-x86 cloud-image-utils
sudo usermod -aG kvm "$USER"   # then log out + back in (or run via `sg kvm -c '…'`)
```

`/dev/kvm` accessible by your user lets QEMU use hardware virt and the whole
suite runs in ~10 minutes. Without it, QEMU falls back to TCG software emulation
and each backend takes 20–30 minutes — works, just slow.

## Storage layout

| Store | Path | Filesystem requirement |
|---|---|---|
| Image cache (persistent) | `$RCB_E2E_CACHE/images/`<br>default `~/.cache/rcb-e2e/images/` | Any. exfat is fine for the read-only base qcow2. |
| Per-run scratch (writable overlay, seed ISO, SSH keypair) | `/tmp/rcb-e2e-<backend>-<pid>/`, auto-removed | **POSIX** (ext4/xfs/btrfs/zfs). The preflight check refuses to run otherwise. |

On a tight home filesystem, point the cache root at a larger volume:

```bash
ln -s /mnt/<large-volume>/rcb-e2e ~/.cache/rcb-e2e
```

This matches the symlink-into-SSD convention this project already uses (e.g.
`~/cc-watch/extracts` → `/mnt/ssd/cc-watch/extracts`).

## Run

```bash
make e2e              # all three backends, serially
make e2e-zfs          # just ZFS
make e2e-btrfs        # just Btrfs
make e2e-timeshift    # just Timeshift
```

Each target boots a fresh Ubuntu 24.04 cloud image, has cloud-init install the
snapshot tool and create a real snapshot, SSHes in, runs the matching Layer 3
test against the real backend with the right `RCB_*_TEST_*` env var, then tears
the VM down.

## Troubleshooting

- **"scratch root on non-POSIX fs" preflight error.** `/tmp` resolves to an
  exfat or NTFS mount. Either remount `/tmp` on a POSIX fs or override the
  scratch root via `RCB_E2E_SCRATCH_ROOT=/some/ext4/path make e2e-zfs`.
- **SSH never comes up.** First boot of the cloud image installs packages over
  the network; `make e2e-zfs SSH_WAIT_SECS=600` extends the timeout (default
  300s).
- **`/dev/kvm` permission denied.** Either you haven't logged out since
  `usermod -aG kvm`, or your distro doesn't add the group. `sg kvm -c 'make
  e2e-zfs'` works as a one-shot.
- **ZFS module fails to load in the guest.** The harness installs
  `linux-modules-extra-$(uname -r)` so the cloud kernel can load `zfs.ko`. If
  Ubuntu has shipped a new kernel since the cached image was built and the
  modules package lags, `rm -rf "$RCB_E2E_CACHE/images"` and rerun to refetch.
- **First run is slow.** ~600 MB cloud-image download once, then cached.
  Subsequent runs reuse the cached image and only re-do cloud-init.
